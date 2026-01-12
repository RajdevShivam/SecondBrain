import requests
import os
import time
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from neo4j import GraphDatabase
from dotenv import load_dotenv
from telegream_utils import send_telegram_message

# Load .env for local testing
load_dotenv()

# CONFIGURATION
GEMINI_MODEL = "gemini-2.0-flash-lite-preview-02-05"
# ⚠️ SAFETY LIMIT: Keep this at 3 to avoid Notion 429 errors
MAX_WORKERS = 2 

def main():
    start_time = time.time()
    print("📅 Starting Weekly Review (Throttled Parallel Mode)...")
    
    notion_token = os.environ.get("NOTION_TOKEN")
    if not notion_token:
        print("❌ Error: NOTION_TOKEN is missing")
        return

    headers = {
        "Authorization": f"Bearer {notion_token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }

    week_start = datetime.now(timezone.utc) - timedelta(days=7)
    week_start_iso = week_start.strftime("%Y-%m-%d")

    # ------------------------------------------------------------------
    # 1. PARALLEL NOTION FETCHING (Max 3 requests at a time)
    # ------------------------------------------------------------------
    print("⏳ Fetching Notion data...")
    
    # We use a dictionary to store results so we can map them back correctly
    results = {}
    
    # Define all the fetch tasks we need to do
    # Format: (Key_Name, Function, Args)
    tasks = [
        ("weekly_tasks", fetch_items_this_week, (headers, "ADMIN_DB_ID", week_start_iso)),
        ("weekly_ideas", fetch_items_this_week, (headers, "IDEAS_DB_ID", week_start_iso)),
        ("weekly_knowledge", fetch_items_this_week, (headers, "KNOWLEDGE_BASE_DB_ID", week_start_iso)),
        ("weekly_people", fetch_items_this_week, (headers, "PEOPLE_DB_ID", week_start_iso)),
        ("weekly_captures", fetch_items_this_week, (headers, "CAPTURE_INBOX_DB_ID", week_start_iso)),
        ("completed_tasks", fetch_completed_tasks, (headers, week_start_iso)),
        ("pending_tasks", fetch_pending_items, (headers, "ADMIN_DB_ID", "Status", "Not Started")),
        ("review_items", fetch_review_items, (headers,))
    ]

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # Submit all tasks
        future_to_key = {executor.submit(func, *args): key for key, func, args in tasks}
        
        # Gather results as they complete
        for future in future_to_key:
            key = future_to_key[future]
            try:
                results[key] = future.result()
            except Exception as e:
                print(f"⚠️ Failed to fetch {key}: {e}")
                results[key] = []

    # Calculate Notion Stats
    notion_stats = {
        "captures": len(results["weekly_captures"]),
        "ideas": len(results["weekly_ideas"]),
        "knowledge": len(results["weekly_knowledge"]),
        "people": len(results["weekly_people"]),
        "tasks_created": len(results["weekly_tasks"]),
        "tasks_completed": len(results["completed_tasks"]),
        "tasks_pending": len(results["pending_tasks"]),
        "needs_review": len(results["review_items"])
    }
    print(f"📊 Notion Data Loaded ({time.time() - start_time:.2f}s)")

    # ------------------------------------------------------------------
    # 2. NEO4J DATA (Sequential - Database drivers prefer single threads)
    # ------------------------------------------------------------------
    print("🕸️ Querying Knowledge Graph...")
    graph_insights = fetch_graph_insights(week_start_iso)
    print(f"🔗 Graph: {len(graph_insights.get('new_connections', []))} new connections")

    # ------------------------------------------------------------------
    # 3. GENERATE REVIEW
    # ------------------------------------------------------------------
    print("🧠 Generating AI Analysis...")
    context = build_review_context(
        notion_stats, results["weekly_ideas"], results["weekly_knowledge"], 
        results["weekly_people"], results["completed_tasks"], 
        results["pending_tasks"], results["review_items"], graph_insights
    )
    
    review_text = generate_comprehensive_review(context)
    
    if not review_text:
        review_text = build_fallback_review(notion_stats, graph_insights)

    # ------------------------------------------------------------------
    # 4. SEND TO TELEGRAM
    # ------------------------------------------------------------------
    print("📨 Sending to Telegram...")
    send_telegram_message(review_text)
    
    print(f"🏁 Weekly Review Complete! Total time: {time.time() - start_time:.2f}s")

# ------------------------------------------------------------------
# NOTION QUERIES (Helpers)
# ------------------------------------------------------------------

def query_db(db_id, headers, payload):
    """Generic safe query wrapper"""
    if not db_id: return []
    try:
        url = f"https://api.notion.com/v1/databases/{db_id}/query"
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        response.raise_for_status()
        
        items = []
        for p in response.json().get("results", []):
            items.append({
                "title": extract_title(p), 
                "content": extract_content(p)
            })
        return items
    except Exception:
        # Fail silently in parallel threads to avoid crashing everything
        return []

def fetch_items_this_week(headers, db_env, date):
    return query_db(os.environ.get(db_env), headers, {
        "filter": {"timestamp": "created_time", "created_time": {"on_or_after": date}}, "page_size": 100
    })

def fetch_completed_tasks(headers, date):
    return query_db(os.environ.get("ADMIN_DB_ID"), headers, {
        "filter": {"and": [{"property": "Status", "select": {"equals": "Done"}}, 
                           {"timestamp": "last_edited_time", "last_edited_time": {"on_or_after": date}}]},
        "page_size": 50
    })

def fetch_pending_items(headers, db_env, prop, val):
    return query_db(os.environ.get(db_env), headers, {
        "filter": {"property": prop, "select": {"equals": val}}, "page_size": 20
    })

def fetch_review_items(headers):
    return query_db(os.environ.get("CAPTURE_INBOX_DB_ID"), headers, {
        "filter": {"property": "Processing Status", "select": {"equals": "Reviewing"}}, "page_size": 10
    })

def extract_title(page):
    props = page.get("properties", {})
    for name in ["Title", "Name", "Task"]:
        if name in props:
            t = props[name].get("title", [])
            if t: return t[0].get("text", {}).get("content", "Untitled")
    return "Untitled"

def extract_content(page):
    props = page.get("properties", {})
    for name in ["Content", "Notes", "Key Insights", "One-liner"]:
        if name in props:
            t = props[name].get("rich_text", [])
            if t: return t[0].get("text", {}).get("content", "")
    return ""

# ------------------------------------------------------------------
# NEO4J & AI HELPERS
# ------------------------------------------------------------------

def fetch_graph_insights(date):
    uri = os.environ.get("NEO4J_URI")
    user = os.environ.get("NEO4J_USER")
    pwd = os.environ.get("NEO4J_PASSWORD")
    if not uri: return {}
    
    insights = {"new_connections": [], "top_concepts": [], "actionable_ideas": [], "clusters": []}
    driver = None
    try:
        driver = GraphDatabase.driver(uri, auth=(user, pwd))
        with driver.session() as s:
            # 1. New Connections
            res = s.run("MATCH (a:Concept)-[r]->(b:Concept) WHERE r.first_linked >= datetime($d) RETURN a.name, type(r), b.name LIMIT 20", d=date+"T00:00:00Z")
            insights["new_connections"] = [{"source": r[0], "relation": r[1], "target": r[2]} for r in res]
            
            # 2. Top Concepts (Fixed for Neo4j 5+)
            res = s.run("""
                MATCH (c:Concept)-[r]-() 
                WITH c, count(r) as conn 
                WHERE conn > 1 
                RETURN c.name, conn 
                ORDER BY conn DESC 
                LIMIT 10
            """)
            insights["top_concepts"] = [{"name": r[0], "connections": r[1]} for r in res]
            
            # 3. Actionable Ideas
            res = s.run("MATCH (i:Idea)-[:MENTIONS]->(c:Concept) WITH i, count(c) as cc WHERE cc >= 2 RETURN i.title, cc ORDER BY cc DESC LIMIT 5")
            insights["actionable_ideas"] = [{"title": r[0], "count": r[1]} for r in res]
            
    except Exception as e:
        print(f"⚠️ Neo4j error: {e}")
    finally:
        if driver: driver.close()
    return insights

def build_review_context(stats, ideas, knowledge, people, completed, pending, review, graph):
    return f"""
    WEEKLY REVIEW DATA
    Stats: {stats}
    
    New Ideas ({len(ideas)}): {[i['title'] for i in ideas[:10]]}
    New Knowledge ({len(knowledge)}): {[k['title'] for k in knowledge[:10]]}
    Tasks Completed ({len(completed)}): {[t['title'] for t in completed[:10]]}
    Tasks Pending ({len(pending)}): {[t['title'] for t in pending[:10]]}
    
    Graph Insights:
    - New Connections: {len(graph.get('new_connections', []))}
    - Top Concepts: {[c['name'] for c in graph.get('top_concepts', [])]}
    - Actionable Ideas: {[i['title'] for i in graph.get('actionable_ideas', [])]}
    """

def generate_comprehensive_review(context):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key: return None
    
    prompt = f"""
    You are a personal knowledge assistant. Generate a COMPREHENSIVE weekly review.
    
    {context}
    
    FORMAT:
    1. **Week in Numbers** (Quick stats)
    2. **Deep Work** (Tasks completed & knowledge captured)
    3. **Graph Intelligence** (Connections & patterns found in Neo4j)
    4. **Focus for Next Week** (Based on pending tasks)
    
    Use Telegram Markdown (*bold*). Be insightful, not just descriptive.
    """
    
    try:
        res = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={api_key}",
            headers={"Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=30
        )
        return res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception: return None

def build_fallback_review(stats, graph):
    return f"📊 *Weekly Stats*\nCaptures: {stats['captures']}\nTasks Done: {stats['tasks_completed']}\nGraph Connections: {len(graph.get('new_connections', []))}"

if __name__ == "__main__":
    main()