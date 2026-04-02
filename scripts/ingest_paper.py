#!/usr/bin/env python3
"""
Paper Ingestion Script for SecondBrain

Ingests an arxiv paper into the Notion Knowledge Base and Neo4j graph:
1. Parses arxiv ID from URL
2. Fetches metadata from arxiv Atom feed (no new deps)
3. Extracts contributions/methodology/results/concepts via Gemini
4. Queries Neo4j for related concepts (1-2 hop traversal)
5. Creates Notion KB page with structured blocks
6. Syncs to Neo4j via existing neo4j_client.sync_page()
7. Marks synced, sets status "Active"

Usage:
    python scripts/ingest_paper.py https://arxiv.org/abs/2501.12948
    python scripts/ingest_paper.py 2501.12948
"""

import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# Add project root to path
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from secondbrain.config import settings
from secondbrain.logger import get_logger
from secondbrain.neo4j_client import Neo4jClient
from secondbrain.notion_client import NotionClient
from secondbrain.sync_state import SyncStateManager

logger = get_logger(__name__)

# Arxiv Atom feed namespace
ATOM_NS = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"

# Gemini extraction prompt
PAPER_EXTRACTION_PROMPT = """
You are analyzing a research paper for a quantitative finance knowledge base.

PAPER:
Title: {title}
Authors: {authors}
Abstract: {abstract}

Extract the following as JSON:
{{
  "key_contributions": ["...", "...", "..."],
  "methodology": "one sentence describing the core technical approach",
  "main_results": "one sentence with the headline finding/number",
  "limitations": "one sentence on stated limitations or caveats",
  "concepts": [{{"name": "...", "type": "...", "aliases": []}}],
  "triplets": [{{"subject": "A", "relation": "RELATION", "object": "B", "confidence": 0.9, "context": "..."}}],
  "domain": "...",
  "related_areas": ["...", "..."]
}}

Rules:
- key_contributions: be specific. "Proposes X that achieves Y on Z" not "proposes a new method"
- concepts: use canonical names (LSTM not lstm, not Long Short Term Memory Networks)
- triplets: use SCREAMING_SNAKE_CASE relations (OUTPERFORMS, USES, APPLIED_TO, EXTENDS)
- related_areas: name things that would already exist in a quant finance KB
"""


def parse_arxiv_id(url_or_id: str) -> str:
    """
    Extract arxiv ID from URL or bare ID string.

    Handles:
        https://arxiv.org/abs/2501.12948
        https://arxiv.org/abs/2501.12948v2
        https://arxiv.org/pdf/2501.12948.pdf
        2501.12948
        2501.12948v2

    Returns:
        Clean arxiv ID (e.g. "2501.12948")
    """
    # Try URL patterns
    match = re.search(r'arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}(?:v\d+)?)', url_or_id)
    if match:
        # Strip version suffix for canonical ID
        raw = match.group(1)
        return re.sub(r'v\d+$', '', raw)

    # Try bare ID
    match = re.match(r'^(\d{4}\.\d{4,5})(?:v\d+)?$', url_or_id.strip())
    if match:
        return match.group(1)

    raise ValueError(f"Cannot parse arxiv ID from: {url_or_id}")


def fetch_arxiv_metadata(arxiv_id: str) -> dict:
    """
    Fetch paper metadata from arxiv Atom feed.

    Args:
        arxiv_id: Clean arxiv ID (e.g. "2501.12948")

    Returns:
        Dict with title, authors, abstract, categories, published, arxiv_url, pdf_url
    """
    url = f"http://export.arxiv.org/api/query?id_list={arxiv_id}"

    try:
        req = Request(url, headers={"User-Agent": "SecondBrain/1.0"})
        with urlopen(req, timeout=15) as resp:
            xml_data = resp.read()
    except (URLError, HTTPError) as e:
        raise RuntimeError(f"Failed to fetch arxiv metadata: {e}")

    root = ET.fromstring(xml_data)
    entries = root.findall(f"{ATOM_NS}entry")

    if not entries:
        raise ValueError(f"No results found for arxiv ID: {arxiv_id}")

    entry = entries[0]

    # Check for error entries (arxiv returns an entry with id but no real content)
    entry_id = entry.findtext(f"{ATOM_NS}id", "")
    if "api/errors" in entry_id:
        raise ValueError(f"arxiv API error for ID: {arxiv_id}")

    title = entry.findtext(f"{ATOM_NS}title", "").strip()
    # Normalize whitespace in title
    title = " ".join(title.split())

    abstract = entry.findtext(f"{ATOM_NS}summary", "").strip()
    abstract = " ".join(abstract.split())

    authors = []
    for author_el in entry.findall(f"{ATOM_NS}author"):
        name = author_el.findtext(f"{ATOM_NS}name", "")
        if name:
            authors.append(name)

    categories = []
    for cat_el in entry.findall(f"{ARXIV_NS}primary_category"):
        term = cat_el.get("term", "")
        if term:
            categories.append(term)
    for cat_el in entry.findall(f"{ATOM_NS}category"):
        term = cat_el.get("term", "")
        if term and term not in categories:
            categories.append(term)

    published = entry.findtext(f"{ATOM_NS}published", "")
    if published:
        # Parse to date only
        published = published[:10]

    arxiv_url = f"https://arxiv.org/abs/{arxiv_id}"
    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

    return {
        "arxiv_id": arxiv_id,
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "categories": categories,
        "published": published,
        "arxiv_url": arxiv_url,
        "pdf_url": pdf_url,
    }


def extract_paper_concepts(metadata: dict) -> dict:
    """
    Extract concepts, triplets, and structured analysis from paper via Gemini.

    Args:
        metadata: Dict from fetch_arxiv_metadata

    Returns:
        Extraction dict with key_contributions, methodology, etc.
    """
    import requests

    prompt = PAPER_EXTRACTION_PROMPT.format(
        title=metadata["title"],
        authors=", ".join(metadata["authors"]),
        abstract=metadata["abstract"],
    )

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_model}:generateContent?key={settings.gemini_api_key}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.1,
        },
    }

    # Reuse retry pattern from master_sync
    last_error = None
    for attempt in range(4):
        try:
            response = requests.post(
                url,
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=settings.gemini_timeout,
            )
            if response.status_code == 200:
                raw_text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
                # Clean markdown fences
                clean = raw_text.strip()
                if clean.startswith("```json"):
                    clean = clean[7:]
                elif clean.startswith("```"):
                    clean = clean[3:]
                if clean.endswith("```"):
                    clean = clean[:-3]
                return json.loads(clean.strip())

            if response.status_code == 429 and attempt < 3:
                delay = 2.0 * (2 ** attempt)
                logger.warning(f"Rate limited, retrying in {delay:.0f}s")
                time.sleep(delay)
                continue

            logger.error(f"Gemini API error: {response.status_code}")
            return None

        except requests.exceptions.Timeout:
            if attempt < 3:
                time.sleep(2.0 * (2 ** attempt))
                continue
            return None
        except Exception as e:
            logger.error(f"Extraction error: {e}")
            return None

    return None


def check_existing_paper(notion_client: NotionClient, kb_db_id: str, arxiv_url: str) -> str:
    """
    Check if a KB page with the same arxiv URL already exists.

    Args:
        notion_client: Notion client instance
        kb_db_id: Knowledge Base database ID
        arxiv_url: arxiv URL to check

    Returns:
        Existing page ID if found, empty string otherwise
    """
    filter_obj = {
        "property": "Source URL",
        "url": {"equals": arxiv_url}
    }
    pages = notion_client.query_database(kb_db_id, filter_obj)
    if pages:
        return pages[0].get("id", "")
    return ""


def build_notion_blocks(metadata: dict, extraction: dict, related: list) -> list:
    """
    Build Notion block children for the paper page body.

    Args:
        metadata: arxiv metadata dict
        extraction: Gemini extraction dict
        related: List of related concept dicts from Neo4j

    Returns:
        List of Notion block objects
    """
    blocks = []

    def heading(text, level=2):
        key = f"heading_{level}"
        return {
            "object": "block",
            "type": key,
            key: {"rich_text": [{"type": "text", "text": {"content": text}}]},
        }

    def paragraph(text):
        # Notion blocks have a 2000-char limit per rich_text element
        if not text:
            return None
        truncated = text[:2000]
        return {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": truncated}}]
            },
        }

    def bullet(text):
        return {
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{"type": "text", "text": {"content": text[:2000]}}]
            },
        }

    # Abstract
    blocks.append(heading("Abstract"))
    p = paragraph(metadata["abstract"])
    if p:
        blocks.append(p)

    # Key Contributions
    contribs = extraction.get("key_contributions", [])
    if contribs:
        blocks.append(heading("Key Contributions"))
        for c in contribs:
            blocks.append(bullet(c))

    # Methodology
    methodology = extraction.get("methodology", "")
    if methodology:
        blocks.append(heading("Methodology"))
        blocks.append(paragraph(methodology))

    # Results
    results = extraction.get("main_results", "")
    if results:
        blocks.append(heading("Results"))
        blocks.append(paragraph(results))

    # Limitations
    limitations = extraction.get("limitations", "")
    if limitations:
        blocks.append(heading("Limitations"))
        blocks.append(paragraph(limitations))

    # Connections to Existing KB
    if related:
        blocks.append(heading("Connections to Existing KB"))
        for r in related:
            name = r.get("name", "?")
            rtype = r.get("type", "")
            line = f"{name}" + (f" ({rtype})" if rtype else "")
            blocks.append(bullet(line))

    # Raw Triplets (for debugging)
    triplets = extraction.get("triplets", [])
    if triplets:
        blocks.append(heading("Raw Triplets"))
        for t in triplets:
            s = t.get("subject", "?")
            r = t.get("relation", "?")
            o = t.get("object", "?")
            conf = t.get("confidence", 0)
            blocks.append(bullet(f"{s} --[{r}]--> {o}  (conf: {conf})"))

    return blocks


def create_kb_page(
    notion_client: NotionClient,
    kb_db_id: str,
    metadata: dict,
    extraction: dict,
    related: list,
) -> str:
    """
    Create a Notion KB page for the paper.

    Returns:
        New page ID or empty string on failure
    """
    safe_title = metadata["title"][:200]
    authors_str = ", ".join(metadata["authors"])[:2000]

    # Build Key Insights from contributions
    contribs = extraction.get("key_contributions", [])
    key_insights = metadata["abstract"][:500]
    if contribs:
        key_insights = " | ".join(contribs)[:2000]

    # Build tags from categories + domain
    tags = metadata.get("categories", [])[:5]

    properties = {
        "Title": {"title": [{"text": {"content": safe_title}}]},
        "Key Insights": {"rich_text": [{"text": {"content": key_insights}}]},
        "PARA Type": {"select": {"name": "Resource"}},
        "Processing Status": {"select": {"name": "New"}},
        "Source URL": {"url": metadata["arxiv_url"]},
    }

    children = build_notion_blocks(metadata, extraction, related)

    page_id = notion_client.create_page(kb_db_id, properties, children=children)
    return page_id or ""


def ingest_paper(url_or_id: str) -> dict:
    """
    Main ingestion pipeline.

    Args:
        url_or_id: arxiv URL or ID

    Returns:
        Dict with page_url, concepts_added, relations_created, related_found
    """
    # Initialize clients
    notion = NotionClient()
    neo4j = Neo4jClient()
    sync_mgr = SyncStateManager(neo4j)

    kb_db_id = settings.knowledge_db_id
    if not kb_db_id:
        raise ValueError("KNOWLEDGE_BASE_DB_ID not configured")

    # Step 1: Parse arxiv ID
    arxiv_id = parse_arxiv_id(url_or_id)
    logger.info(f"Parsed arxiv ID: {arxiv_id}")

    # Step 2: Fetch metadata
    metadata = fetch_arxiv_metadata(arxiv_id)
    logger.info(f"Fetched: {metadata['title'][:80]}...")

    # Idempotency check
    existing_id = check_existing_paper(notion, kb_db_id, metadata["arxiv_url"])
    if existing_id:
        logger.info(f"Paper already exists as page {existing_id[:8]}...")
        page = notion.get_page(existing_id)
        page_url = page.get("url", "") if page else ""
        return {
            "page_url": page_url,
            "page_id": existing_id,
            "concepts_added": 0,
            "relations_created": 0,
            "related_found": 0,
            "already_existed": True,
        }

    # Step 3: Extract via Gemini
    extraction = extract_paper_concepts(metadata)
    if not extraction:
        raise RuntimeError("Gemini extraction failed")

    logger.info(
        f"Extracted {len(extraction.get('concepts', []))} concepts, "
        f"{len(extraction.get('triplets', []))} triplets"
    )

    # Step 4: Find related concepts in Neo4j
    concept_names = [c["name"] for c in extraction.get("concepts", [])]
    related_areas = extraction.get("related_areas", [])
    all_query_names = list(set(concept_names + related_areas))

    related = neo4j.find_related_concepts(all_query_names, max_hops=2, limit=10)
    logger.info(f"Found {len(related)} related concepts in graph")

    # Step 5: Create Notion KB page
    page_id = create_kb_page(notion, kb_db_id, metadata, extraction, related)
    if not page_id:
        raise RuntimeError("Failed to create Notion KB page")

    # Get the page URL
    page = notion.get_page(page_id)
    page_url = page.get("url", "") if page else ""

    logger.info(f"Created KB page: {page_id[:8]}...")

    # Step 6: Sync to Neo4j
    # Build extraction in the format sync_page expects
    sync_extraction = {
        "concepts": extraction.get("concepts", []),
        "triplets": extraction.get("triplets", []),
        "domain": extraction.get("domain", "General"),
    }

    success = neo4j.sync_page(sync_extraction, page_id)
    relations_created = len(extraction.get("triplets", []))

    if success:
        # Step 7: Mark synced and set Active
        content_hash = sync_mgr.compute_content_hash({
            "text": metadata["abstract"],
            "title": metadata["title"],
        })
        sync_mgr.mark_synced(
            page_id=page_id,
            content_hash=content_hash,
            concepts=concept_names,
            relations_count=relations_created,
            source_db="KNOWLEDGE_BASE",
            notion_last_edited=page.get("last_edited_time") if page else None,
        )
        notion.update_status(page_id, "Active")
        logger.info("Synced to Neo4j and marked Active")
    else:
        logger.warning("Neo4j sync failed, page left as New")

    # Step 8: Print summary
    result = {
        "page_url": page_url,
        "page_id": page_id,
        "concepts_added": len(concept_names),
        "relations_created": relations_created,
        "related_found": len(related),
        "already_existed": False,
    }

    return result


def main():
    """CLI entry point."""
    if len(sys.argv) < 2:
        print("Usage: python scripts/ingest_paper.py <arxiv_url_or_id>")
        print("  e.g. python scripts/ingest_paper.py https://arxiv.org/abs/2501.12948")
        print("  e.g. python scripts/ingest_paper.py 2501.12948")
        sys.exit(1)

    url_or_id = sys.argv[1]

    try:
        result = ingest_paper(url_or_id)

        print("\n" + "=" * 50)
        if result.get("already_existed"):
            print("PAPER ALREADY EXISTS")
        else:
            print("PAPER INGESTED SUCCESSFULLY")
        print("=" * 50)
        print(f"Page URL:          {result['page_url']}")
        print(f"Concepts added:    {result['concepts_added']}")
        print(f"Relations created: {result['relations_created']}")
        print(f"Related entries:   {result['related_found']}")
        print("=" * 50)

    except ValueError as e:
        print(f"Input error: {e}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as e:
        print(f"Processing error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        logger.error(f"Ingestion failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
