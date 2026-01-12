#!/usr/bin/env python3
"""
Master Notion-to-Neo4j Sync Engine

Syncs content from Notion databases to Neo4j knowledge graph:
- INBOX: Classifies and routes to destination DBs
- IDEAS/KNOWLEDGE_BASE: Extracts concepts and syncs to graph
- Uses AI for classification and triplet extraction
"""

import json
import time
import requests
from datetime import datetime

# Import from secondbrain package
from secondbrain.config import settings
from secondbrain.logger import get_logger
from secondbrain.dictionaries import (
    UNAMBIGUOUS_SYNONYMS,
    AMBIGUOUS_TERMS,
    CONTEXT_HINTS,
    preprocess_unambiguous,
    build_disambiguation_hints,
    get_canonical_map,
)
from secondbrain.neo4j_client import Neo4jClient, sanitize_relation
from secondbrain.notion_client import NotionClient
from secondbrain.sync_state import SyncStateManager

# Initialize logger
logger = get_logger(__name__)

# Initialize clients
notion_client = NotionClient()
neo4j_client = Neo4jClient()
sync_manager = SyncStateManager(neo4j_client)


def clean_json_string(text):
    """Clean JSON response from AI (remove markdown code blocks)."""
    clean = text.strip()
    if clean.startswith("```json"):
        clean = clean[7:]
    elif clean.startswith("```"):
        clean = clean[3:]
    if clean.endswith("```"):
        clean = clean[:-3]
    return clean.strip()


def gemini_request_with_retry(url, payload, max_retries=3, base_delay=2.0):
    """
    Make a Gemini API request with exponential backoff retry.

    Args:
        url: Gemini API endpoint URL
        payload: Request JSON payload
        max_retries: Maximum retry attempts (default 3)
        base_delay: Base delay in seconds (default 2.0)

    Returns:
        Response JSON or None on failure
    """
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            response = requests.post(
                url,
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=settings.gemini_timeout
            )

            # Success
            if response.status_code == 200:
                return response.json()

            # Rate limited - retry with backoff
            if response.status_code == 429:
                if attempt < max_retries:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"Rate limited (429), retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})")
                    time.sleep(delay)
                    continue
                else:
                    logger.error(f"Rate limited after {max_retries} retries")
                    return None

            # Other error - don't retry
            logger.warning(f"Gemini API error: {response.status_code}")
            return None

        except requests.exceptions.Timeout:
            last_error = "timeout"
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"Timeout, retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
                continue
            else:
                logger.error(f"Timeout after {max_retries} retries")
                return None

        except requests.exceptions.SSLError:
            logger.error("SSL certificate verification failed")
            return None

        except requests.exceptions.RequestException as e:
            last_error = str(e)
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"Network error, retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
                continue
            else:
                logger.error(f"Network error after {max_retries} retries: {e}")
                return None

    return None


def fetch_fuzzy_matches_from_neo4j(names):
    """Find fuzzy matches for concept names in Neo4j."""
    return neo4j_client.fuzzy_match_concepts(
        names,
        threshold=settings.fuzzy_match_threshold
    )


def get_canonical_map_from_dictionaries():
    """Get canonical name mapping from dictionaries."""
    return get_canonical_map()


# =============================================================================
# NOTION IO
# =============================================================================

def fetch_pages_to_sync(db_id):
    """Fetch 'New' or 'Update Graph' items."""
    if not db_id:
        logger.error("Database ID is missing")
        return []
    return notion_client.fetch_pages_to_sync(db_id)


def extract_page_content(page):
    """Extract text content from a page."""
    return notion_client.extract_page_content(page)


def update_status(page_id, status):
    """Update the Processing Status of a page."""
    notion_client.update_status(page_id, status)


def create_in_destination_db(category, text, extracted_data, subcategory=None):
    """Create a page in the appropriate destination database."""
    target_db_id = None
    props = {}
    title = extracted_data.get("title", text[:100])

    # Truncate for Notion property limits
    safe_text = text[:1800] + "..." if len(text) > 1800 else text
    safe_title = title[:200] if len(title) > 200 else title

    if category == "IDEAS":
        target_db_id = settings.ideas_db_id_clean
        props = {
            "Title": {"title": [{"text": {"content": safe_title}}]},
            "One-liner": {"rich_text": [{"text": {"content": safe_text}}]},
            "Status": {"select": {"name": "Raw"}},
            "Processing Status": {"select": {"name": "New"}}
        }
    elif category == "KNOWLEDGE_BASE":
        target_db_id = settings.knowledge_db_id
        props = {
            "Title": {"title": [{"text": {"content": safe_title}}]},
            "Key Insights": {"rich_text": [{"text": {"content": safe_text}}]},
            "PARA Type": {"select": {"name": subcategory or "Resource"}},
            "Processing Status": {"select": {"name": "New"}}
        }
    elif category == "PEOPLE":
        target_db_id = settings.people_db_id_clean
        props = {
            "Name": {"title": [{"text": {"content": safe_title}}]},
            "Context": {"rich_text": [{"text": {"content": safe_text}}]}
        }
    elif category == "ADMIN":
        target_db_id = settings.admin_db_id_clean
        props = {
            "Task": {"title": [{"text": {"content": safe_title}}]},
            "Notes": {"rich_text": [{"text": {"content": safe_text}}]},
            "Status": {"select": {"name": "Not Started"}}
        }
    else:
        return None

    if not target_db_id:
        logger.warning(f"No database configured for category: {category}")
        return None

    new_page_id = notion_client.create_page(target_db_id, props)

    if new_page_id and category == "ADMIN":
        return "ADMIN_CREATED"

    return new_page_id


# =============================================================================
# AI LOGIC
# =============================================================================

def classify_text(text):
    """Classify text into a category using Gemini AI with retry logic."""
    clean_text = preprocess_unambiguous(text)

    # Truncate to avoid huge prompts
    if len(clean_text) > settings.text_truncation_limit:
        clean_text = clean_text[:settings.text_truncation_limit] + "..."

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent?key={settings.gemini_api_key}"

    prompt = f"""
Classify this capture into ONE category:
TEXT: "{clean_text}"

CATEGORIES:
- PEOPLE: Notes about specific individuals, contacts, networking info
- ADMIN: Tasks, to-dos, reminders, action items
- IDEAS: Raw ideas, concepts, thoughts, brainstorms
- KNOWLEDGE_BASE: Study notes, research, facts, reference material
- TRASH: Spam, meaningless, duplicates, or test content

OUTPUT JSON: {{ "category": "...", "subcategory": "...", "confidence": 0.0, "extracted_data": {{"title": "..."}} }}
    """

    try:
        response_json = gemini_request_with_retry(
            url,
            {"contents": [{"parts": [{"text": prompt}]}]},
            max_retries=3,
            base_delay=2.0
        )

        if not response_json:
            return {"category": "REVIEW_NEEDED", "confidence": 0}

        result = json.loads(clean_json_string(
            response_json["candidates"][0]["content"]["parts"][0]["text"]
        ))
        return result

    except Exception as e:
        logger.error(f"Classification error: {e}")
        return {"category": "REVIEW_NEEDED", "confidence": 0}


def extract_triplets(text):
    """Extract concepts and relationships from text using Gemini AI with retry logic."""
    processed = preprocess_unambiguous(text)

    if len(processed) > settings.text_truncation_limit:
        processed = processed[:settings.text_truncation_limit] + "..."

    hints = build_disambiguation_hints(text)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent?key={settings.gemini_api_key}"

    prompt = f"""
Analyze this text:
"{processed}"
{hints}
TASK: Extract concepts & relations. Normalize names (Title Case). Correct spelling.
OUTPUT JSON:
{{
  "concepts": [{{ "name": "Concept", "aliases": [], "type": "Type" }}],
  "triplets": [{{ "subject": "A", "relation": "RELATION", "object": "B", "confidence": 0.9, "context": "..." }}],
  "domain": "Field"
}}
"""

    try:
        response_json = gemini_request_with_retry(
            url,
            {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1}
            },
            max_retries=3,
            base_delay=2.0
        )

        if not response_json:
            return None

        return json.loads(clean_json_string(
            response_json["candidates"][0]["content"]["parts"][0]["text"]
        ))

    except Exception as e:
        logger.error(f"Triplet extraction error: {e}")
        return None


# =============================================================================
# NEO4J SYNC
# =============================================================================

def normalize_extraction(extraction):
    """Normalize concept names using dictionaries and fuzzy matching."""
    if not extraction or not extraction.get("concepts"):
        return extraction

    canonical_map = get_canonical_map_from_dictionaries()
    names = [c["name"] for c in extraction["concepts"]]
    unknowns = [n for n in names if n.upper() not in canonical_map]

    if unknowns:
        db_matches = fetch_fuzzy_matches_from_neo4j(unknowns)
        for orig, canon in db_matches.items():
            canonical_map[orig.upper()] = canon

    final_concepts = []
    for c in extraction["concepts"]:
        orig = c.get("name", "").strip()
        final = canonical_map.get(orig.upper(), orig)

        existing = next((x for x in final_concepts if x["name"] == final), None)
        if existing:
            new_aliases = c.get("aliases", [])
            if orig != final:
                new_aliases.append(orig)
            existing["aliases"] = list(set(existing.get("aliases", []) + new_aliases))
        else:
            c["name"] = final
            if "aliases" not in c:
                c["aliases"] = []
            if orig != final:
                c["aliases"].append(orig)
            final_concepts.append(c)

    extraction["concepts"] = final_concepts

    for t in extraction.get("triplets", []):
        s = t.get("subject", "").strip().upper()
        o = t.get("object", "").strip().upper()
        if s in canonical_map:
            t["subject"] = canonical_map[s]
        if o in canonical_map:
            t["object"] = canonical_map[o]

    return extraction


def sync_page_to_neo4j(extraction, page_id):
    """Sync extraction results to Neo4j graph."""
    extraction = normalize_extraction(extraction)
    if not extraction or not extraction.get("concepts"):
        return False

    try:
        # Use neo4j_client for all operations
        success = neo4j_client.sync_page(extraction, page_id)

        if success:
            # Track sync state
            concepts = [c["name"] for c in extraction.get("concepts", [])]
            relations_count = len(extraction.get("triplets", []))
            content_hash = sync_manager.compute_content_hash({
                "page_id": page_id,
                "text": str(extraction),
                "title": ""
            })
            sync_manager.mark_synced(
                page_id=page_id,
                content_hash=content_hash,
                concepts=concepts,
                relations_count=relations_count,
                source_db="GRAPH_SYNC"
            )

        return success

    except Exception as e:
        logger.error(f"Neo4j sync error: {e}", extra={"page_id": page_id[:8]})
        return False


# =============================================================================
# MAIN PROCESSING
# =============================================================================

def process_inbox_item(page):
    """Process an item from the Capture Inbox."""
    page_id = page['page_id']
    text = page['text']

    logger.info(f"Processing inbox item: {page['title'][:50]}...")

    if len(text) < 10:
        update_status(page_id, "Processed")
        logger.info("Skipping - text too short")
        return

    # 1. Classify
    cl = classify_text(text)
    cat = cl.get("category", "REVIEW_NEEDED")
    confidence = cl.get("confidence", 0)

    logger.info(f"Classified as {cat}", extra={"confidence": confidence})

    if cat == "TRASH":
        update_status(page_id, "Processed")
        logger.info("Discarded as trash")
        return

    if cat == "REVIEW_NEEDED" or confidence < settings.low_confidence_threshold:
        update_status(page_id, "Reviewing")
        logger.info("Needs manual review - low confidence")
        return

    # 2. Move to Destination
    new_page_id = create_in_destination_db(
        cat, text,
        cl.get("extracted_data", {}),
        cl.get("subcategory")
    )

    if new_page_id == "ADMIN_CREATED":
        update_status(page_id, "Processed")
        logger.info("Admin task created (no graph sync)")
        return
    elif cat == "PEOPLE":
        if new_page_id:
            logger.info("Person added to PEOPLE database")
            extraction = extract_triplets(text)
            if extraction and extraction.get("concepts"):
                if sync_page_to_neo4j(extraction, new_page_id):
                    logger.info("Person relationships synced to graph")
        update_status(page_id, "Processed")
        return
    elif not new_page_id:
        logger.error(f"Failed to create page in {cat}")
        update_status(page_id, "Reviewing")
        return

    logger.info(f"Moved to {cat}")

    # 3. Sync to Graph (IDEAS and KNOWLEDGE_BASE)
    if cat in ["IDEAS", "KNOWLEDGE_BASE"]:
        extraction = extract_triplets(text)
        if extraction and extraction.get("concepts"):
            if sync_page_to_neo4j(extraction, new_page_id):
                logger.info("Synced to graph")
                update_status(new_page_id, "Active")
            else:
                logger.warning("Graph sync failed")
        else:
            logger.info("No concepts extracted")
            update_status(new_page_id, "Active")

    update_status(page_id, "Processed")


def process_destination_item(page, db_type):
    """Process an item from IDEAS or KNOWLEDGE_BASE for graph sync."""
    page_id = page['page_id']
    text = page['text']

    logger.info(f"Updating {db_type}: {page['title']}")

    extraction = extract_triplets(text)
    if extraction:
        if sync_page_to_neo4j(extraction, page_id):
            logger.info("Graph updated")
            update_status(page_id, "Active")
        else:
            logger.warning("Sync failed")
    else:
        logger.warning("No concepts found")
        update_status(page_id, "Active")


def validate_config():
    """Validate all required configuration is present."""
    logger.info("=" * 50)
    logger.info("CONFIG CHECK")
    logger.info("=" * 50)

    try:
        settings.validate_notion()
        settings.validate_gemini()
        settings.validate_neo4j()

        logger.info("All API credentials present")

        # Check database IDs
        db_config = settings.db_config
        for db_name, config in db_config.items():
            if config["id"]:
                logger.info(f"{db_name}: {config['id'][:8]}...")
            else:
                if db_name == "INBOX":
                    raise ValueError(f"Missing critical: {db_name}_DB_ID")
                logger.warning(f"Missing: {db_name}")

        logger.info("Configuration valid")
        return True

    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        return False


def main():
    """Main entry point."""
    logger.info("=" * 50)
    logger.info("MASTER SYNC ENGINE STARTED")
    logger.info("=" * 50)

    if not validate_config():
        return

    # Databases to scan
    DATABASES_TO_SCAN = ["INBOX", "IDEAS", "KNOWLEDGE_BASE"]
    db_config = settings.db_config

    for name in DATABASES_TO_SCAN:
        config = db_config.get(name)
        if not config or not config["id"]:
            continue

        logger.info(f"Scanning {name}...")
        pages = fetch_pages_to_sync(config["id"])

        if not pages:
            logger.info("No updates found")
            continue

        logger.info(f"Found {len(pages)} items")

        for p in pages:
            content = extract_page_content(p)
            try:
                if name == "INBOX":
                    process_inbox_item(content)
                else:
                    process_destination_item(content, config["type"])
            except Exception as e:
                logger.error(f"Error processing item: {e}")
            time.sleep(1)

    logger.info("=" * 50)
    logger.info("SYNC COMPLETE")
    logger.info("=" * 50)


if __name__ == "__main__":
    main()
