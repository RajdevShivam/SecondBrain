"""
Graph Query Bot - Natural Language Knowledge Queries
=====================================================
Trigger: Telegram message containing "?" or starting with "/ask"
Output: AI-generated answer based on Neo4j graph + Notion data

Setup in Pipedream:
1. Use your existing Telegram trigger workflow
2. Add a filter step to check for queries (see filter logic below)
3. Add this Python step
4. Add Telegram "Send Message" step

Filter Logic (add before this step):
  return steps.trigger.event.message?.text?.includes("?") ||
         steps.trigger.event.message?.text?.startsWith("/ask");
"""

import requests
import json
import os
import re
from neo4j import GraphDatabase

# ------------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------------
GEMINI_MODEL = "gemini-2.0-flash-lite-preview-02-05"


def handler(pd: "pipedream"):
    """
    Handles natural language queries against the knowledge graph.
    """

    # Get the user's question
    message = pd.steps["trigger"]["event"].get("message", {})
    query_text = message.get("text", "").strip()
    chat_id = message.get("chat", {}).get("id")

    # Clean up query (remove /ask prefix if present)
    if query_text.startswith("/ask"):
        query_text = query_text[4:].strip()

    if not query_text or len(query_text) < 3:
        return {
            "response": "Please ask a question about your knowledge. Example: 'What do I know about options pricing?'",
            "chat_id": chat_id
        }

    print(f"🔍 Query: {query_text}")

    # ------------------------------------------------------------------
    # 1. EXTRACT KEY CONCEPTS FROM QUERY
    # ------------------------------------------------------------------

    concepts = extract_query_concepts(query_text)
    print(f"📌 Extracted concepts: {concepts}")

    # ------------------------------------------------------------------
    # 2. QUERY NEO4J FOR RELATED KNOWLEDGE
    # ------------------------------------------------------------------

    graph_results = query_knowledge_graph(concepts, query_text)

    # ------------------------------------------------------------------
    # 3. QUERY NOTION FOR ADDITIONAL CONTEXT
    # ------------------------------------------------------------------

    notion_results = search_notion(concepts)

    # ------------------------------------------------------------------
    # 4. GENERATE RESPONSE WITH AI
    # ------------------------------------------------------------------

    response = generate_response(query_text, graph_results, notion_results)

    return {
        "response": response,
        "chat_id": chat_id,
        "concepts_searched": concepts,
        "graph_results_count": len(graph_results.get("concepts", [])),
        "notion_results_count": len(notion_results)
    }


# ------------------------------------------------------------------
# CONCEPT EXTRACTION
# ------------------------------------------------------------------

def extract_query_concepts(query):
    """Use AI to extract key concepts from the query"""

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        # Fallback: simple extraction
        return simple_concept_extraction(query)

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={api_key}"

    prompt = f"""
Extract the key concepts/topics from this question. Return ONLY a JSON array of strings.

Question: "{query}"

Examples:
- "What do I know about Bitcoin?" -> ["Bitcoin"]
- "How does volatility relate to options?" -> ["volatility", "options"]
- "Tell me about machine learning in finance" -> ["machine learning", "finance"]

Output ONLY the JSON array, nothing else.
"""

    try:
        response = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 100}
            },
            timeout=10
        )
        response.raise_for_status()

        text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        # Clean and parse
        text = text.strip().replace("```json", "").replace("```", "")
        concepts = json.loads(text)

        if isinstance(concepts, list):
            return [c.strip() for c in concepts if c.strip()]

    except Exception as e:
        print(f"⚠️ Concept extraction failed: {e}")

    return simple_concept_extraction(query)


def simple_concept_extraction(query):
    """Fallback: Extract nouns and key phrases"""
    # Remove common question words
    stop_words = {"what", "how", "why", "when", "where", "who", "is", "are", "do",
                  "does", "can", "could", "would", "should", "about", "the", "a", "an",
                  "my", "me", "i", "you", "know", "tell", "explain", "describe"}

    words = re.findall(r'\b[a-zA-Z]+\b', query.lower())
    concepts = [w for w in words if w not in stop_words and len(w) > 2]

    return concepts[:5]  # Limit to 5 concepts


# ------------------------------------------------------------------
# NEO4J QUERIES
# ------------------------------------------------------------------

def query_knowledge_graph(concepts, original_query):
    """Query Neo4j for related knowledge"""

    uri = os.environ.get("NEO4J_URI")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD")

    if not uri or not password:
        return {"concepts": [], "connections": [], "sources": []}

    results = {
        "concepts": [],
        "connections": [],
        "sources": []
    }

    driver = None
    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))

        with driver.session() as session:

            # 1. Find matching concepts (fuzzy match)
            for concept in concepts:
                result = session.run("""
                    MATCH (c:Concept)
                    WHERE toLower(c.name) CONTAINS toLower($term)
                       OR any(alias IN c.aliases WHERE toLower(alias) CONTAINS toLower($term))
                    RETURN c.name as name, c.type as type, c.domains as domains,
                           c.mention_count as mentions
                    LIMIT 5
                """, term=concept)

                for record in result:
                    results["concepts"].append({
                        "name": record["name"],
                        "type": record["type"],
                        "domains": record["domains"] or [],
                        "mentions": record["mentions"] or 1
                    })

            # 2. Find connections between concepts
            if results["concepts"]:
                concept_names = [c["name"] for c in results["concepts"]]

                result = session.run("""
                    MATCH (a:Concept)-[r]->(b:Concept)
                    WHERE a.name IN $names OR b.name IN $names
                    RETURN a.name as source, type(r) as relation, b.name as target,
                           r.context as context, r.confidence as confidence
                    LIMIT 20
                """, names=concept_names)

                for record in result:
                    results["connections"].append({
                        "source": record["source"],
                        "relation": record["relation"],
                        "target": record["target"],
                        "context": record["context"] or "",
                        "confidence": record["confidence"] or 0.8
                    })

            # 3. Find source documents (Ideas, Notes) mentioning these concepts
            if results["concepts"]:
                concept_names = [c["name"] for c in results["concepts"]]

                result = session.run("""
                    MATCH (s)-[:MENTIONS]->(c:Concept)
                    WHERE c.name IN $names
                    RETURN DISTINCT labels(s)[0] as type, s.title as title,
                           s.url as url, collect(c.name) as concepts
                    LIMIT 10
                """, names=concept_names)

                for record in result:
                    results["sources"].append({
                        "type": record["type"],
                        "title": record["title"],
                        "url": record["url"],
                        "concepts": record["concepts"]
                    })

    except Exception as e:
        print(f"⚠️ Neo4j query error: {e}")
    finally:
        if driver:
            driver.close()

    return results


# ------------------------------------------------------------------
# NOTION SEARCH
# ------------------------------------------------------------------

def search_notion(concepts):
    """Search Notion for pages mentioning concepts"""

    notion_token = os.environ.get("NOTION_TOKEN")
    if not notion_token:
        return []

    headers = {
        "Authorization": f"Bearer {notion_token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }

    results = []

    # Search for each concept
    for concept in concepts[:3]:  # Limit to avoid too many API calls
        try:
            response = requests.post(
                "https://api.notion.com/v1/search",
                headers=headers,
                json={
                    "query": concept,
                    "page_size": 5
                },
                timeout=10
            )
            response.raise_for_status()

            for page in response.json().get("results", []):
                if page.get("object") == "page":
                    title = extract_page_title(page)
                    results.append({
                        "title": title,
                        "url": page.get("url", ""),
                        "type": page.get("parent", {}).get("type", "unknown"),
                        "matched_concept": concept
                    })

        except Exception as e:
            print(f"⚠️ Notion search error for '{concept}': {e}")

    # Deduplicate by URL
    seen_urls = set()
    unique_results = []
    for r in results:
        if r["url"] not in seen_urls:
            seen_urls.add(r["url"])
            unique_results.append(r)

    return unique_results[:10]


def extract_page_title(page):
    """Extract title from Notion search result"""
    props = page.get("properties", {})
    for name in ["Title", "Name", "Task", "title", "name"]:
        if name in props:
            prop = props[name]
            if prop.get("type") == "title":
                title_arr = prop.get("title", [])
                if title_arr:
                    return title_arr[0].get("text", {}).get("content", "Untitled")
    return "Untitled"


# ------------------------------------------------------------------
# RESPONSE GENERATION
# ------------------------------------------------------------------

def generate_response(query, graph_results, notion_results):
    """Generate natural language response"""

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return build_fallback_response(graph_results, notion_results)

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={api_key}"

    # Build context
    context = f"""
USER QUESTION: "{query}"

=== KNOWLEDGE GRAPH DATA ===

MATCHING CONCEPTS:
{format_concepts(graph_results.get('concepts', []))}

CONNECTIONS FOUND:
{format_connections(graph_results.get('connections', []))}

SOURCE DOCUMENTS:
{format_sources(graph_results.get('sources', []))}

=== NOTION SEARCH RESULTS ===
{format_notion_results(notion_results)}
"""

    prompt = f"""
You are a helpful knowledge assistant. Answer the user's question based on their personal knowledge base.

{context}

INSTRUCTIONS:
1. Answer the question directly and specifically
2. Reference actual data from the graph/Notion results
3. Show connections between concepts when relevant
4. If information is limited, say so honestly
5. Suggest related areas to explore
6. Keep response concise but informative
7. Use Telegram Markdown (*bold*, _italic_)
8. End with a follow-up question or suggestion

If no relevant data was found, acknowledge this and suggest what to capture.
"""

    try:
        response = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.5, "maxOutputTokens": 1000}
            },
            timeout=20
        )
        response.raise_for_status()

        text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        return text.strip()

    except Exception as e:
        print(f"⚠️ Response generation failed: {e}")
        return build_fallback_response(graph_results, notion_results)


def format_concepts(concepts):
    if not concepts:
        return "No matching concepts found"
    lines = []
    for c in concepts:
        lines.append(f"- {c['name']} ({c['type'] or 'Concept'}) - {c['mentions']} mentions")
    return "\n".join(lines)


def format_connections(connections):
    if not connections:
        return "No connections found"
    lines = []
    for c in connections[:10]:
        lines.append(f"- {c['source']} --[{c['relation']}]--> {c['target']}")
    return "\n".join(lines)


def format_sources(sources):
    if not sources:
        return "No source documents found"
    lines = []
    for s in sources:
        concepts_str = ", ".join(s.get("concepts", [])[:3])
        lines.append(f"- [{s['type']}] {s['title']} (mentions: {concepts_str})")
    return "\n".join(lines)


def format_notion_results(results):
    if not results:
        return "No Notion pages found"
    lines = []
    for r in results:
        lines.append(f"- {r['title']} (matched: {r['matched_concept']})")
    return "\n".join(lines)


def build_fallback_response(graph_results, notion_results):
    """Build simple response if AI fails"""

    lines = ["🔍 *Search Results*\n"]

    concepts = graph_results.get("concepts", [])
    if concepts:
        lines.append("*Found in your knowledge graph:*")
        for c in concepts[:5]:
            lines.append(f"• {c['name']}")
        lines.append("")

    connections = graph_results.get("connections", [])
    if connections:
        lines.append("*Connections:*")
        for c in connections[:5]:
            lines.append(f"• {c['source']} → {c['target']}")
        lines.append("")

    if notion_results:
        lines.append("*Related pages:*")
        for r in notion_results[:5]:
            lines.append(f"• {r['title']}")

    if not concepts and not notion_results:
        lines.append("I couldn't find relevant information in your knowledge base.")
        lines.append("\n💡 Try capturing some notes about this topic first!")

    return "\n".join(lines)
