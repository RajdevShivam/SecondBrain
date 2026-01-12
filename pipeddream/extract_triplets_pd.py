# pip install requests neo4j thefuzz python-levenshtein

import requests
import json
import re
import os
import time
from neo4j import GraphDatabase
from thefuzz import process, fuzz

# ------------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------------
MODEL_NAME = "gemini-2.0-flash-lite-preview-02-05"

STOPWORDS = {
    "AND", "THE", "FOR", "BUT", "NOT", "WITH", "THAT", "THIS", "FROM", 
    "HAVE", "ARE", "WAS", "ALL", "ONE", "HAS", "CAN", "OUT", "INTO",
    "NOW", "NEW", "BIG", "GET", "USE", "HOW", "WHO", "WHY", "YES"
}

# TIER 1: UNAMBIGUOUS (1-to-1 mappings)
UNAMBIGUOUS_SYNONYMS = {
    "BTC": "Bitcoin", "ETH": "Ethereum", "DOGE": "Dogecoin",
    "AAPL": "Apple Inc", "GOOGL": "Alphabet Inc", "MSFT": "Microsoft",
    "TCS": "Tata Consultancy Services", "INFY": "Infosys", 
    "NIFTY": "Nifty 50", "SENSEX": "BSE Sensex", "SPX": "S&P 500",
    "GARCH": "Generalized Autoregressive Conditional Heteroskedasticity",
    "ARIMA": "Autoregressive Integrated Moving Average",
    "LSTM": "Long Short-Term Memory",
    "EBITDA": "Earnings Before Interest Taxes Depreciation Amortization",
    "USD": "US Dollar", "INR": "Indian Rupee",
    "FED": "Federal Reserve", "RBI": "Reserve Bank Of India",
    "CAPM": "Capital Asset Pricing Model", "EMH": "Efficient Market Hypothesis",
    "HFT": "High Frequency Trading", "CAGR": "Compound Annual Growth Rate",
    "ROE": "Return On Equity", "ROA": "Return On Assets",
    "CPI": "Consumer Price Index", "GDP": "Gross Domestic Product",
    "NLP": "Natural Language Processing", "LLM": "Large Language Model",
}

# TIER 2: AMBIGUOUS (1-to-Many)
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

# TIER 3: CONTEXT HINTS
CONTEXT_HINTS = {
    "Price To Earnings": ["ratio", "multiple", "valuation", "earnings"],
    "Private Equity": ["fund", "acquisition", "buyout", "capital"],
    "Vector Autoregression": ["model", "econometric", "lag", "time series"],
    "Value At Risk": ["risk", "loss", "confidence", "percentile"],
    "Machine Learning": ["algorithm", "training", "prediction", "model"],
    "Maximum Likelihood": ["estimation", "statistical", "parameter"],
    "At The Money": ["option", "strike", "call", "put"],
    "Automated Teller Machine": ["bank", "cash", "withdraw"],
}

def handler(pd: "pipedream"):
    # ------------------------------------------------------------------
    # 1. SETUP & CACHE LOAD
    # ------------------------------------------------------------------
    ai_cache = {}
    if "data_store" in pd.inputs and pd.inputs["data_store"]:
        try:
            ai_cache = pd.inputs["data_store"].get("synonym_cache") or {}
            if not isinstance(ai_cache, dict): 
                ai_cache = {}
        except Exception as e:
            print(f"⚠️ Data Store Error: {e}")
            ai_cache = {}

    routing_result = pd.steps["route_to_database"]["$return_value"]
    if not routing_result.get("graph_eligible", False):
        return {"skipped": True, "reason": "Not graph eligible"}

    message_text = pd.steps["classify_with_gemini"]["$return_value"].get("original_text", "")
    if not message_text or len(message_text.strip()) < 20:
        return {"skipped": True, "reason": "Text too short"}

    # ------------------------------------------------------------------
    # 2. BUILD LOCAL CANONICAL MAP (Dictionaries + Cache)
    # ------------------------------------------------------------------
    canonical_map = {}
    
    # Add unambiguous terms
    for k, v in UNAMBIGUOUS_SYNONYMS.items():
        canonical_map[k.upper()] = v
        if v.upper() not in canonical_map:
            canonical_map[v.upper()] = v
    
    # Add ambiguous terms
    for abbr, meanings in AMBIGUOUS_TERMS.items():
        for meaning in meanings:
            if meaning.upper() not in canonical_map:
                canonical_map[meaning.upper()] = meaning
    
    # Add AI cache
    for k, v in ai_cache.items():
        canonical_map[k.upper()] = v
        if v.upper() not in canonical_map:
            canonical_map[v.upper()] = v
    
    all_known_values = set(canonical_map.keys())
    
    # ------------------------------------------------------------------
    # 3. PREPROCESS: Expand Unambiguous Terms
    # ------------------------------------------------------------------
    preprocessed_text, expansions = preprocess_unambiguous(message_text)
    
    # ------------------------------------------------------------------
    # 4. BUILD CONTEXT HINTS
    # ------------------------------------------------------------------
    disambiguation_guide = build_disambiguation_hints(message_text)
    
    # ------------------------------------------------------------------
    # 5. MAIN EXTRACTION (GEMINI)
    # ------------------------------------------------------------------
    prompt = f"""
Analyze this quantitative finance text:
"{preprocessed_text}"

{disambiguation_guide}

**TASK:**
1. Extract key concepts and relationships.
2. **CORRECT SPELLING ERRORS** (e.g., "Bitcion" -> "Bitcoin").
3. Normalize names to Title Case.
4. If an abbreviation was expanded in text, use the full name.
5. Use context hints for ambiguous terms.

**OUTPUT JSON:**
{{
  "concepts": [{{ "name": "Concept Name", "aliases": ["abbr"], "type": "Type" }}],
  "triplets": [{{ "subject": "A", "relation": "RELATION", "object": "B", "confidence": 0.85 }}]
}}
"""
    
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent?key={api_key}"
        
        response = requests.post(
            url, 
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2}
            }
        )
        response.raise_for_status()
        data = json.loads(response.json()["candidates"][0]["content"]["parts"][0]["text"])

    except Exception as e:
        print(f"❌ Main extraction failed: {e}")
        return {"skipped": True, "error": str(e)}

    # ------------------------------------------------------------------
    # 6. DATABASE LOOKUP (THE NEW LAYER)
    # ------------------------------------------------------------------
    # This connects to Neo4j to find concepts that aren't in our dictionary
    # but MIGHT exist in the graph (e.g. "Monte Carlo Sims" vs "Monte Carlo Simulation")
    
    extracted_names = [c["name"] for c in data.get("concepts", [])]
    
    # Only verify names that we didn't just fix with our dictionary
    names_to_verify = [
        name for name in extracted_names 
        if name.upper() not in canonical_map
    ]
    
    if names_to_verify:
        print(f"🔍 Checking {len(names_to_verify)} terms against Neo4j Graph...")
        db_matches = fetch_fuzzy_matches_from_neo4j(names_to_verify)
        
        # Merge DB results into our canonical map
        for original, canonical in db_matches.items():
            canonical_map[original.upper()] = canonical
            print(f"   ✅ Graph Match: '{original}' -> '{canonical}'")

    # ------------------------------------------------------------------
    # 7. FINAL NORMALIZATION
    # ------------------------------------------------------------------
    final_concepts = []
    seen_concepts = set()

    for concept in data.get("concepts", []):
        original_name = concept.get("name", "").strip()
        upper_name = original_name.upper()
        
        # Determine final name
        final_name = canonical_map.get(upper_name, original_name)
        
        # Merge Logic: If we've already seen this concept (due to normalization), merge aliases
        # This handles cases where "BTC" and "Bitcoin" both appear in text and normalize to "Bitcoin"
        existing = next((c for c in final_concepts if c["name"] == final_name), None)
        
        if existing:
            # Merge aliases
            new_aliases = concept.get("aliases", [])
            if original_name != final_name and original_name not in new_aliases:
                new_aliases.append(original_name)
            
            existing["aliases"] = list(set(existing.get("aliases", []) + new_aliases))
        else:
            # Create new entry
            concept["name"] = final_name
            if "aliases" not in concept:
                concept["aliases"] = []
            
            # Add original as alias if different
            if original_name != final_name and original_name not in concept["aliases"]:
                concept["aliases"].append(original_name)
                
            final_concepts.append(concept)

    data["concepts"] = final_concepts
    
    # ------------------------------------------------------------------
    # 8. UPDATE TRIPLETS
    # ------------------------------------------------------------------
    # We must also update the subject/object names in triplets to match normalized concepts
    for triplet in data.get("triplets", []):
        sub = triplet.get("subject", "").strip().upper()
        obj = triplet.get("object", "").strip().upper()
        
        if sub in canonical_map:
            triplet["subject"] = canonical_map[sub]
        if obj in canonical_map:
            triplet["object"] = canonical_map[obj]

    return {
        **data,
        "normalization_stats": {
            "dictionary_hits": len([k for k in canonical_map if k in UNAMBIGUOUS_SYNONYMS]),
            "db_lookups": len(names_to_verify) if 'names_to_verify' in locals() else 0
        }
    }


# ------------------------------------------------------------------
# NEO4J HELPER FUNCTIONS
# ------------------------------------------------------------------

def fetch_fuzzy_matches_from_neo4j(names):
    """
    Queries Neo4j to find existing nodes that are similar to the input names.
    Returns a dict: { "Input Name": "Canonical Name From DB" }
    """
    matches = {}
    uri = os.environ.get("NEO4J_URI")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD")
    
    if not uri or not password:
        print("⚠️ Neo4j credentials missing - skipping DB lookup")
        return {}

    driver = GraphDatabase.driver(uri, auth=(user, password))
    
    try:
        with driver.session() as session:
            # We fetch all Concept names from DB to do fuzzy matching in Python
            # Note: For massive graphs (100k+ nodes), use Fulltext Search Index instead.
            # For personal graphs (<10k nodes), fetching names is fast enough.
            result = session.run("MATCH (c:Concept) RETURN c.name as name, c.aliases as aliases")
            db_concepts = []
            for record in result:
                db_concepts.append(record["name"])
                if record["aliases"]:
                    db_concepts.extend(record["aliases"])
            
            # Use 'process' from thefuzz to find best matches
            for name in names:
                # threshold=90 means "very high similarity"
                # limit=1 means just get the best one
                best_match = process.extractOne(name, db_concepts, scorer=fuzz.token_sort_ratio)
                
                if best_match and best_match[1] >= 90:
                    # If we matched an alias, we technically need to find the canonical name
                    # But for simplicity, we map to the match. Ideally, we query DB again to get canonical.
                    matches[name] = get_canonical_for_alias(session, best_match[0])
                    
    except Exception as e:
        print(f"⚠️ Neo4j Lookup Error: {e}")
    finally:
        driver.close()
        
    return matches

def get_canonical_for_alias(session, name_or_alias):
    """Helper to find the true node name if we matched an alias"""
    result = session.run("""
        MATCH (c:Concept)
        WHERE c.name = $val OR $val IN c.aliases
        RETURN c.name as canonical LIMIT 1
    """, val=name_or_alias)
    record = result.single()
    return record["canonical"] if record else name_or_alias

# ------------------------------------------------------------------
# (Existing helper functions: preprocess_unambiguous, build_disambiguation_hints...)
# ------------------------------------------------------------------
# [Keep your existing helper functions here exactly as they were]
def preprocess_unambiguous(text):
    expanded = text
    expansions = []
    keys_sorted = sorted(UNAMBIGUOUS_SYNONYMS.keys(), key=len, reverse=True)
    pattern_str = r'\b(' + '|'.join(map(re.escape, keys_sorted)) + r')\b'
    for abbr in set(re.findall(pattern_str, text)):
        full_name = UNAMBIGUOUS_SYNONYMS[abbr]
        expanded = re.sub(r'\b' + re.escape(abbr) + r'\b', f"{full_name} ({abbr})", expanded, count=1)
        expansions.append(abbr)
    return expanded, expansions

def build_disambiguation_hints(text):
    detected = []
    for abbr, meanings in AMBIGUOUS_TERMS.items():
        if re.search(r'\b' + re.escape(abbr) + r'\b', text):
            hint = f"- '{abbr}' could be: {', '.join(meanings)}"
            for meaning in meanings:
                if meaning in CONTEXT_HINTS:
                    clues = ", ".join(CONTEXT_HINTS[meaning])
                    hint += f"\n  → Use '{meaning}' if you see: {clues}"
            detected.append(hint)
    return "\n".join(detected) if detected else ""