#!/usr/bin/env python3
"""
Master Notion-to-Neo4j Sync Engine (Robust & Normalized)
- Auto-cleans Database IDs (works with URLs or IDs)
- Syncs INBOX, IDEAS, and KNOWLEDGE BASE
- Handles Updates via "Wipe & Replace"
"""

import requests
import json
import time
import os
import re
from datetime import datetime
from dotenv import load_dotenv
from neo4j import GraphDatabase
from thefuzz import process, fuzz

load_dotenv()

# ==============================================================================
# CONFIGURATION & ID CLEANER
# ==============================================================================

def clean_notion_id(dirty_id):
    """Extracts 32-char UUID from URL or messy string"""
    if not dirty_id: return None
    clean = re.sub(r'[\[\]\(\)]', '', dirty_id)
    match = re.search(r'([a-f0-9]{32})', clean)
    return match.group(1) if match else clean.strip()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
GEMINI_MODEL = "gemini-2.0-flash-lite-preview-02-05"

# Clean IDs automatically
DB_CONFIG = {
    "INBOX": {"id": clean_notion_id(os.getenv("CAPTURE_INBOX_DB_ID")), "type": "Inbox"},
    "IDEAS": {"id": clean_notion_id(os.getenv("IDEAS_DB_ID")), "type": "Idea"},
    "KNOWLEDGE_BASE": {"id": clean_notion_id(os.getenv("KNOWLEDGE_BASE_DB_ID")), "type": "Knowledge"},
    "PEOPLE": {"id": clean_notion_id(os.getenv("PEOPLE_DB_ID")), "type": "Person"},
    "ADMIN": {"id": clean_notion_id(os.getenv("ADMIN_DB_ID")), "type": "Task"},
}

# ==============================================================================
# NORMALIZATION DICTIONARIES
# ==============================================================================

# Words to ignore during concept extraction
STOPWORDS = {
    "AND", "THE", "FOR", "BUT", "NOT", "WITH", "THAT", "THIS", "FROM",
    "HAVE", "ARE", "WAS", "ALL", "ONE", "HAS", "CAN", "OUT", "INTO",
    "NOW", "NEW", "BIG", "GET", "USE", "HOW", "WHO", "WHY", "YES"
}

# Tier 1: Unambiguous abbreviations -> Full names (deterministic O(1) lookup)
UNAMBIGUOUS_SYNONYMS = {
    # Crypto
    "BTC": "Bitcoin", "ETH": "Ethereum", "DOGE": "Dogecoin",
    # Stocks
    "AAPL": "Apple Inc", "GOOGL": "Alphabet Inc", "MSFT": "Microsoft",
    "TCS": "Tata Consultancy Services", "INFY": "Infosys",
    # Indices
    "NIFTY": "Nifty 50", "SENSEX": "BSE Sensex", "SPX": "S&P 500",
    # Currencies
    "USD": "US Dollar", "INR": "Indian Rupee",
    # Finance Models
    "GARCH": "Generalized Autoregressive Conditional Heteroskedasticity",
    "ARIMA": "Autoregressive Integrated Moving Average",
    "CAPM": "Capital Asset Pricing Model",
    "EMH": "Efficient Market Hypothesis",
    # Finance Metrics
    "EBITDA": "Earnings Before Interest Taxes Depreciation Amortization",
    "HFT": "High Frequency Trading",
    "CAGR": "Compound Annual Growth Rate",
    "ROE": "Return On Equity",
    "ROA": "Return On Assets",
    "CPI": "Consumer Price Index",
    "GDP": "Gross Domestic Product",
    # Institutions
    "FED": "Federal Reserve",
    "RBI": "Reserve Bank Of India",
    # AI/ML
    "NLP": "Natural Language Processing",
    "LLM": "Large Language Model",
    "LSTM": "Long Short-Term Memory",
    "CNN": "Convolutional Neural Network",
    "RNN": "Recurrent Neural Network",
    "GPT": "Generative Pre-trained Transformer",
    "RAG": "Retrieval Augmented Generation",
}

# Tier 2: Ambiguous abbreviations (need context to resolve)
AMBIGUOUS_TERMS = {
    "PE": ["Price To Earnings", "Private Equity"],
    "VAR": ["Vector Autoregression", "Value At Risk"],
    "ML": ["Machine Learning", "Maximum Likelihood"],
    "IV": ["Implied Volatility", "Independent Variable"],
    "ATM": ["At The Money", "Automated Teller Machine"],
    "IR": ["Information Ratio", "Interest Rate"],
    "ES": ["Expected Shortfall", "E-mini S&P"],
    "MV": ["Mean-Variance", "Market Value"],
    "BL": ["Black-Litterman", "Baseline"],
}

# Tier 3: Context hints for disambiguation
CONTEXT_HINTS = {
    "Price To Earnings": ["ratio", "multiple", "valuation", "earnings"],
    "Private Equity": ["fund", "acquisition", "buyout", "capital"],
    "Vector Autoregression": ["model", "econometric", "lag", "time series"],
    "Value At Risk": ["risk", "loss", "confidence", "percentile"],
    "Machine Learning": ["algorithm", "training", "prediction", "model", "neural"],
    "Maximum Likelihood": ["estimation", "statistical", "parameter"],
    "At The Money": ["option", "strike", "call", "put"],
    "Automated Teller Machine": ["bank", "cash", "withdraw"],
    "Implied Volatility": ["option", "vega", "skew"],
    "Independent Variable": ["regression", "predictor", "feature"],
}

# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

def get_notion_headers():
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }

def preprocess_unambiguous(text):
    expanded = text
    keys_sorted = sorted(UNAMBIGUOUS_SYNONYMS.keys(), key=len, reverse=True)
    pattern_str = r'\b(' + '|'.join(map(re.escape, keys_sorted)) + r')\b'
    for abbr in set(re.findall(pattern_str, text)):
        full = UNAMBIGUOUS_SYNONYMS[abbr]
        expanded = re.sub(r'\b' + re.escape(abbr) + r'\b', f"{full} ({abbr})", expanded, count=1)
    return expanded

def build_disambiguation_hints(text):
    detected = []
    for abbr, meanings in AMBIGUOUS_TERMS.items():
        if re.search(r'\b' + re.escape(abbr) + r'\b', text):
            hint = f"- '{abbr}' could be: {', '.join(meanings)}"
            for m in meanings:
                if m in CONTEXT_HINTS:
                    hint += f"\n  -> Use '{m}' if you see: {', '.join(CONTEXT_HINTS[m])}"
            detected.append(hint)
    return "\n".join(detected) if detected else ""

def get_canonical_map_from_dictionaries():
    canonical_map = {}
    for k, v in UNAMBIGUOUS_SYNONYMS.items():
        canonical_map[k.upper()] = v
        if v.upper() not in canonical_map: canonical_map[v.upper()] = v
    for abbr, meanings in AMBIGUOUS_TERMS.items():
        for meaning in meanings:
            if meaning.upper() not in canonical_map:
                canonical_map[meaning.upper()] = meaning
    return canonical_map

def fetch_fuzzy_matches_from_neo4j(names):
    matches = {}
    if not NEO4J_URI: return {}
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        with driver.session() as session:
            result = session.run("MATCH (c:Concept) RETURN c.name as name, c.aliases as aliases")
            db_concepts = []
            for record in result:
                db_concepts.append(record["name"])
                if record["aliases"]: db_concepts.extend(record["aliases"])
            
            if not db_concepts: return {}

            for name in names:
                best_match = process.extractOne(name, db_concepts, scorer=fuzz.token_sort_ratio)
                if best_match and best_match[1] >= 88:
                    res = session.run("""
                        MATCH (c:Concept) WHERE c.name = $val OR $val IN c.aliases 
                        RETURN c.name as canonical LIMIT 1
                    """, val=best_match[0])
                    rec = res.single()
                    if rec: matches[name] = rec["canonical"]
    except Exception: pass
    finally: driver.close()
    return matches

def clean_json_string(text):
    clean = text.strip()
    if clean.startswith("```json"): clean = clean[7:]
    elif clean.startswith("```"): clean = clean[3:]
    if clean.endswith("```"): clean = clean[:-3]
    return clean.strip()

# ==============================================================================
# NOTION IO
# ==============================================================================

def fetch_pages_to_sync(db_id):
    """Fetch 'New' or 'Update Graph' items"""
    
    # 1. CLEAN THE ID (Just in case)
    if not db_id:
        print(f"  ❌ Error: Database ID is missing.")
        return []
    
    clean_id = db_id.strip()
    
    # 2. CONSTRUCT THE URL (Clean f-string, no brackets)
    url = f"https://api.notion.com/v1/databases/{clean_id}/query"
    
    # Debug print (Optional: helps you see exactly what URL is being used)
    # print(f"  🔍 Debug URL: {url}") 

    payload = {
        "filter": {
            "or": [
                {"property": "Processing Status", "select": {"equals": "New"}},
                {"property": "Processing Status", "select": {"equals": "Update Graph"}},
                {"property": "Processing Status", "select": {"is_empty": True}}
            ]
        },
        "page_size": 100
    }
    
    try:
        response = requests.post(url, headers=get_notion_headers(), json=payload)
        response.raise_for_status()
        return response.json().get("results", [])
    except Exception as e:
        print(f"  ❌ Error fetching from DB {clean_id}: {e}")
        return []

def extract_page_content(page):
    page_id = page["id"]
    props = page.get("properties", {})
    
    title = "Untitled"
    title_prop = props.get("Title") or props.get("Name") or props.get("Task")
    if title_prop and title_prop.get("title"):
        title = "".join([t.get("plain_text", "") for t in title_prop["title"]])
    
    blocks_url = f"https://api.notion.com/v1/blocks/{page_id}/children"
    try:
        blocks_response = requests.get(blocks_url, headers=get_notion_headers())
        blocks = blocks_response.json().get("results", [])
    except: blocks = []
    
    block_texts = []
    for block in blocks:
        block_type = block.get("type")
        if block_type in ["paragraph", "heading_1", "heading_2", "heading_3", "bulleted_list_item", "numbered_list_item"]:
            rich_text = block.get(block_type, {}).get("rich_text", [])
            t = "".join([x.get("plain_text", "") for x in rich_text])
            if t.strip(): block_texts.append(t)
            
    full_text = f"{title}\n\n" + "\n".join(block_texts)
    
    status_prop = props.get("Processing Status")
    current_status = status_prop["select"].get("name", "") if status_prop and status_prop.get("select") else ""
    
    return {
        "page_id": page_id,
        "text": full_text.strip(),
        "title": title,
        "current_status": current_status,
        "notion_url": page.get("url", "")
    }

def update_status(page_id, status):
    url = f"https://api.notion.com/v1/pages/{page_id}"
    payload = {"properties": {"Processing Status": {"select": {"name": status}}}}
    try:
        requests.patch(url, headers=get_notion_headers(), json=payload)
    except Exception as e:
        print(f"  ⚠️ Failed to update status: {e}")

def create_in_destination_db(category, text, extracted_data, subcategory=None):
    target_db_id = None
    props = {}
    title = extracted_data.get("title", text[:100])
    # Truncate text for Notion property limits (2000 char max)
    safe_text = text[:1800] + "..." if len(text) > 1800 else text
    # Truncate title too (Notion title limit is 2000 chars)
    safe_title = title[:200] if len(title) > 200 else title

    if category == "IDEAS":
        target_db_id = DB_CONFIG["IDEAS"]["id"]
        props = {
            "Title": {"title": [{"text": {"content": safe_title}}]},
            "One-liner": {"rich_text": [{"text": {"content": safe_text}}]},
            "Status": {"select": {"name": "Raw"}}
        }
    elif category == "KNOWLEDGE_BASE":
        target_db_id = DB_CONFIG["KNOWLEDGE_BASE"]["id"]
        props = {
            "Title": {"title": [{"text": {"content": safe_title}}]},
            "Key Insights": {"rich_text": [{"text": {"content": safe_text}}]},
            "PARA Type": {"select": {"name": subcategory or "Resource"}}
        }
    elif category == "PEOPLE":
        target_db_id = DB_CONFIG["PEOPLE"]["id"]
        props = {
            "Name": {"title": [{"text": {"content": safe_title}}]},
            "Context": {"rich_text": [{"text": {"content": safe_text}}]}
        }
    elif category == "ADMIN":
        target_db_id = DB_CONFIG["ADMIN"]["id"]
        props = {
            "Task": {"title": [{"text": {"content": safe_title}}]},
            "Notes": {"rich_text": [{"text": {"content": safe_text}}]},
            "Status": {"select": {"name": "Not Started"}}
        }
        # ADMIN tasks don't need graph sync, but we still create them
        # Return special marker after creation
    else:
        return None

    if not target_db_id:
        print(f"  [WARNING] No database configured for category: {category}")
        return None

    # Set Processing Status for graph-synced databases (set to "New", will be updated to "Active" after sync)
    if category in ["IDEAS", "KNOWLEDGE_BASE"]:
        props["Processing Status"] = {"select": {"name": "New"}}

    try:
        response = requests.post(
            "https://api.notion.com/v1/pages",
            headers=get_notion_headers(),
            json={"parent": {"database_id": target_db_id}, "properties": props}
        )
        response.raise_for_status()
        new_page_id = response.json()["id"]

        # Return special marker for ADMIN (created but no graph sync needed)
        if category == "ADMIN":
            return "ADMIN_CREATED"

        return new_page_id
    except requests.exceptions.HTTPError as e:
        print(f"  [ERROR] Creating page failed: {e}")
        print(f"  [DEBUG] Notion Response: {e.response.text}")
        return None
    except Exception as e:
        print(f"  [ERROR] Creating destination page: {e}")
        return None

# ==============================================================================
# AI LOGIC
# ==============================================================================

def classify_text(text):
    clean_text = preprocess_unambiguous(text)
    # Truncate to avoid huge prompts
    if len(clean_text) > 3000:
        clean_text = clean_text[:3000] + "..."

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    prompt = f"""
Classify this capture into ONE category:
TEXT: "{clean_text}"

CATEGORIES:
- PEOPLE: Notes about specific individuals, contacts, networking info, who someone is
- ADMIN: Tasks, to-dos, reminders, action items, things to do
- IDEAS: Raw ideas, concepts, thoughts, brainstorms, hypotheses
- KNOWLEDGE_BASE: Study notes, research, facts, reference material, tutorials, how-tos
- TRASH: Spam, meaningless, duplicates, or test content

OUTPUT JSON: {{ "category": "...", "subcategory": "...", "confidence": 0.0, "extracted_data": {{"title": "..."}} }}
    """
    try:
        response = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=30  # Add timeout to prevent hanging
        )
        if response.status_code != 200:
            print(f"    [API Error] {response.status_code}: {response.text[:200]}")
            return {"category": "REVIEW_NEEDED", "confidence": 0}
        result = json.loads(clean_json_string(response.json()["candidates"][0]["content"]["parts"][0]["text"]))
        return result
    except requests.exceptions.SSLError as e:
        print(f"    [SSL Error] Certificate verification failed. Try: pip install --upgrade certifi")
        return {"category": "REVIEW_NEEDED", "confidence": 0}
    except requests.exceptions.Timeout:
        print(f"    [Timeout] Gemini API took too long")
        return {"category": "REVIEW_NEEDED", "confidence": 0}
    except requests.exceptions.RequestException as e:
        print(f"    [Network Error] {e}")
        return {"category": "REVIEW_NEEDED", "confidence": 0}
    except Exception as e:
        print(f"    [Classification Error] {e}")
        return {"category": "REVIEW_NEEDED", "confidence": 0}

def extract_triplets(text):
    processed = preprocess_unambiguous(text)
    # Truncate to avoid huge prompts
    if len(processed) > 3000:
        processed = processed[:3000] + "..."

    hints = build_disambiguation_hints(text)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
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
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1}
        }
        response = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=30
        )
        if response.status_code != 200:
            print(f"    [Triplet API Error] {response.status_code}")
            return None
        return json.loads(clean_json_string(response.json()["candidates"][0]["content"]["parts"][0]["text"]))
    except requests.exceptions.SSLError:
        print(f"    [SSL Error] Try: pip install --upgrade certifi")
        return None
    except Exception as e:
        print(f"    [Triplet Extraction Error] {e}")
        return None

# ==============================================================================
# NEO4J SYNC (WIPE & REPLACE)
# ==============================================================================

def normalize_extraction(extraction):
    if not extraction or not extraction.get("concepts"): return extraction
    
    canonical_map = get_canonical_map_from_dictionaries()
    names = [c["name"] for c in extraction["concepts"]]
    unknowns = [n for n in names if n.upper() not in canonical_map]
    
    if unknowns:
        db_matches = fetch_fuzzy_matches_from_neo4j(unknowns)
        for orig, canon in db_matches.items(): canonical_map[orig.upper()] = canon
    
    final_concepts = []
    for c in extraction["concepts"]:
        orig = c.get("name", "").strip()
        final = canonical_map.get(orig.upper(), orig)
        
        existing = next((x for x in final_concepts if x["name"] == final), None)
        if existing:
            new_aliases = c.get("aliases", [])
            if orig != final: new_aliases.append(orig)
            existing["aliases"] = list(set(existing.get("aliases", []) + new_aliases))
        else:
            c["name"] = final
            if "aliases" not in c: c["aliases"] = []
            if orig != final: c["aliases"].append(orig)
            final_concepts.append(c)
    
    extraction["concepts"] = final_concepts
    for t in extraction.get("triplets", []):
        s, o = t.get("subject", "").strip().upper(), t.get("object", "").strip().upper()
        if s in canonical_map: t["subject"] = canonical_map[s]
        if o in canonical_map: t["object"] = canonical_map[o]
    
    return extraction

def sanitize_relation(raw):
    clean = raw.replace(" ", "_").replace("-", "_").upper()
    clean = ''.join(c for c in clean if c.isalnum() or c == '_')
    return clean if clean and clean[0].isalpha() else "RELATED_TO"

def sync_page_to_neo4j(extraction, page_id):
    extraction = normalize_extraction(extraction)
    if not extraction or not extraction.get("concepts"): return False
    
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        with driver.session() as session:
            ts = datetime.now().isoformat()
            
            # 1. DELETE OLD LINKS owned by this page
            print(f"    🧹 Wiping old graph connections for {page_id}...")
            session.run("MATCH ()-[r {source_page: $pid}]->() DELETE r", pid=page_id)
            
            # 2. MERGE CONCEPTS (Nodes are shared, so we don't delete them, just merge)
            concepts = extraction.get("concepts", [])
            batch = [{
                "name": c["name"], "type": c.get("type", "Concept"), 
                "aliases": c.get("aliases", []), "domain": extraction.get("domain", "General")
            } for c in concepts]
            
            if batch:
                session.run("""
                    UNWIND $batch AS item
                    MERGE (c:Concept {name: item.name})
                    ON CREATE SET c.type = item.type, c.domains = [item.domain], c.aliases = item.aliases, c.first_seen = datetime($ts)
                    ON MATCH SET c.last_mentioned = datetime($ts), c.aliases = COALESCE(c.aliases, []) + [x IN item.aliases WHERE NOT x IN COALESCE(c.aliases, [])]
                """, batch=batch, ts=ts)
            
            # 3. CREATE NEW LINKS (Stamped with page_id)
            triplets = extraction.get("triplets", [])
            for t in triplets:
                sub, obj = t.get("subject"), t.get("object")
                if sub and obj:
                    rtype = sanitize_relation(t.get("relation"))
                    session.run(f"""
                        MATCH (a:Concept {{name: $sub}}), (b:Concept {{name: $obj}})
                        MERGE (a)-[r:`{rtype}` {{source_page: $pid}}]->(b)
                        ON CREATE SET r.first_linked = datetime($ts)
                        SET r.confidence = $conf, r.context = $ctx, r.last_updated = datetime($ts)
                    """, sub=sub, obj=obj, pid=page_id, conf=t.get("confidence", 0.8), ctx=t.get("context", "")[:200], ts=ts)
            return True
    except Exception as e:
        print(f"    ❌ Neo4j Error: {e}")
        return False
    finally:
        driver.close()

# ==============================================================================
# MAIN CONTROLLER
# ==============================================================================

def process_inbox_item(page):
    page_id = page['page_id']
    text = page['text']
    print(f"  [INBOX] {page['title'][:50]}...")

    if len(text) < 10:
        update_status(page_id, "Processed")
        print("    [SKIP] Text too short")
        return

    # 1. Classify
    cl = classify_text(text)
    cat = cl.get("category", "REVIEW_NEEDED")
    confidence = cl.get("confidence", 0)
    print(f"    [CLASSIFY] {cat} (confidence: {confidence})")

    if cat == "TRASH":
        update_status(page_id, "Processed")
        print("    [TRASH] Discarded")
        return

    if cat == "REVIEW_NEEDED" or confidence < 0.6:
        update_status(page_id, "Reviewing")
        print("    [REVIEW] Low confidence, needs manual review")
        return

    # 2. Move to Destination
    new_page_id = create_in_destination_db(cat, text, cl.get("extracted_data", {}), cl.get("subcategory"))

    if new_page_id == "ADMIN_CREATED":
        update_status(page_id, "Processed")
        print("    [OK] Admin task created (no graph sync)")
        return
    elif new_page_id == "PEOPLE_CREATED" or cat == "PEOPLE":
        # PEOPLE items: create page but optionally sync to graph for relationships
        if new_page_id and new_page_id not in ["ADMIN_CREATED"]:
            print(f"    [OK] Person added to PEOPLE database")
            # Optionally extract relationships for people too
            extraction = extract_triplets(text)
            if extraction and extraction.get("concepts"):
                if sync_page_to_neo4j(extraction, new_page_id):
                    print("    [OK] Person relationships synced to Graph")
        update_status(page_id, "Processed")
        return
    elif not new_page_id:
        print(f"    [ERROR] Failed to create page in {cat}")
        update_status(page_id, "Reviewing")
        return

    print(f"    [OK] Moved to {cat}")

    # 3. Sync to Graph (IDEAS and KNOWLEDGE_BASE)
    if cat in ["IDEAS", "KNOWLEDGE_BASE"]:
        extraction = extract_triplets(text)
        if extraction and extraction.get("concepts"):
            if sync_page_to_neo4j(extraction, new_page_id):
                print("    [OK] Synced to Graph")
                # Update destination page status to "Active" after successful sync
                update_status(new_page_id, "Active")
            else:
                print("    [WARNING] Graph sync failed")
        else:
            print("    [INFO] No concepts extracted")
            # Still mark as Active even if no concepts (nothing to sync)
            update_status(new_page_id, "Active")

    # Mark original INBOX item as Processed
    update_status(page_id, "Processed")

def process_destination_item(page, db_type):
    page_id = page['page_id']
    text = page['text']
    print(f"  🔄 Updating {db_type}: {page['title']}")
    
    extraction = extract_triplets(text)
    if extraction:
        if sync_page_to_neo4j(extraction, page_id):
            print("    ✅ Graph Updated.")
            update_status(page_id, "Active")
        else:
            print("    ⚠️ Sync Failed.")
    else:
        print("    ⚠️ No concepts found.")
        update_status(page_id, "Active")

def validate_config():
    """Validate all required configuration is present"""
    print("=" * 60)
    print("CONFIG CHECK")
    print("=" * 60)

    errors = []

    # Check API credentials
    checks = [
        ("NOTION_TOKEN", NOTION_TOKEN),
        ("GEMINI_API_KEY", GEMINI_API_KEY),
        ("NEO4J_URI", NEO4J_URI),
        ("NEO4J_PASSWORD", NEO4J_PASSWORD),
    ]

    for name, value in checks:
        if value:
            print(f"  [OK] {name}: {'*' * 8}...{value[-4:] if len(value) > 4 else '****'}")
        else:
            print(f"  [MISSING] {name}")
            errors.append(name)

    # Check Database IDs
    print("\nDatabase IDs:")
    for db_name, config in DB_CONFIG.items():
        if config["id"]:
            print(f"  [OK] {db_name}: {config['id'][:8]}...")
        else:
            print(f"  [MISSING] {db_name}")
            if db_name == "INBOX":  # INBOX is critical
                errors.append(f"{db_name}_DB_ID")

    print("=" * 60)

    if errors:
        print(f"\n[ERROR] Missing critical config: {', '.join(errors)}")
        print("Check your .env file and ensure all variables are set.")
        return False

    print("[OK] All critical config present!\n")
    return True

def main():
    print("="*60 + "\n MASTER SYNC ENGINE STARTED\n" + "="*60)

    # Validate config before proceeding
    if not validate_config():
        return

    # Define which databases to scan and how to process them
    # INBOX: Classify and route to destination DBs
    # IDEAS/KNOWLEDGE_BASE: Sync to Neo4j on "New" or "Update Graph"
    # PEOPLE/ADMIN: Skip (no graph sync needed)
    DATABASES_TO_SCAN = ["INBOX", "IDEAS", "KNOWLEDGE_BASE"]

    for name in DATABASES_TO_SCAN:
        config = DB_CONFIG.get(name)
        if not config or not config["id"]:
            continue

        print(f"\n>>> Scanning {name}...")
        pages = fetch_pages_to_sync(config["id"])

        if not pages:
            print("   No updates found.")
            continue

        print(f"   Found {len(pages)} items.")

        for p in pages:
            content = extract_page_content(p)
            try:
                if name == "INBOX":
                    process_inbox_item(content)
                else:
                    # Process destination items (IDEAS, KNOWLEDGE_BASE) for graph re-sync
                    process_destination_item(content, config["type"])
            except Exception as e:
                print(f"   [ERROR] Critical Error processing item: {e}")
            time.sleep(1)

    print("\n" + "="*60)
    print(" SYNC COMPLETE")
    print("="*60)

if __name__ == "__main__":
    main()