import requests
import os
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
from telegream_utils import send_telegram_message

# Load .env file for local testing
load_dotenv()

MAX_WORKERS = 2

# CONFIGURATION
GEMINI_MODEL = "gemini-2.0-flash-lite-preview-02-05"

def main():
    start_time = time.time()
    print("🚀 Starting Daily Nudge (Parallel Mode)...")
    
    notion_token = os.environ.get("NOTION_TOKEN")
    if not notion_token:
        print("❌ Error: NOTION_TOKEN is missing")
        return

    headers = {
        "Authorization": f"Bearer {notion_token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }

    # ------------------------------------------------------------------
    # 1. FETCH DATA IN PARALLEL (The Speedup ⚡)
    # ------------------------------------------------------------------
    print("⏳ Fetching data from Notion...")
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # Submit all tasks at once
        future_tasks = executor.submit(fetch_pending_tasks, headers)
        future_ideas = executor.submit(fetch_raw_ideas, headers)
        future_people = executor.submit(fetch_people_followups, headers)
        future_captures = executor.submit(fetch_recent_captures, headers, 1)
        future_reviews = executor.submit(fetch_review_items, headers)

        # Get results as they finish
        pending_tasks = future_tasks.result()
        raw_ideas = future_ideas.result()
        people_followups = future_people.result()
        yesterday_captures = future_captures.result()
        review_items = future_reviews.result()

    elapsed_data = time.time() - start_time
    print(f"✅ Data fetched in {elapsed_data:.2f}s")
    print(f"📊 Stats: {len(pending_tasks)} tasks, {len(raw_ideas)} ideas, "
          f"{len(people_followups)} people, {len(yesterday_captures)} captures.")

    # ------------------------------------------------------------------
    # 2. PREPARE AI CONTEXT
    # ------------------------------------------------------------------
    context = f"""
Today is {datetime.now().strftime('%A, %B %d, %Y')}.

PENDING TASKS ({len(pending_tasks)}):
{format_items(pending_tasks)}

RAW IDEAS TO DEVELOP ({len(raw_ideas)}):
{format_items(raw_ideas)}

PEOPLE TO FOLLOW UP ({len(people_followups)}):
{format_items(people_followups)}

YESTERDAY'S CAPTURES ({len(yesterday_captures)}):
{format_items(yesterday_captures)}

ITEMS NEEDING REVIEW ({len(review_items)}):
{format_items(review_items)}
"""

    # ------------------------------------------------------------------
    # 3. GENERATE SUMMARY (This still takes 5-10s due to AI)
    # ------------------------------------------------------------------
    print("🧠 Generating AI summary...")
    ai_start = time.time()
    summary = generate_smart_summary(context)
    print(f"✅ AI finished in {time.time() - ai_start:.2f}s")

    # ------------------------------------------------------------------
    # 4. SEND TO TELEGRAM
    # ------------------------------------------------------------------
    if summary:
        message = f"🧠 *Daily Nudge* - {datetime.now().strftime('%B %d')}\n\n{summary}"
    else:
        message = build_fallback_message(pending_tasks, raw_ideas, people_followups, yesterday_captures, review_items)

    send_telegram_message(message)
    print(f"🏁 Total Runtime: {time.time() - start_time:.2f}s")

# ------------------------------------------------------------------
# HELPERS & QUERIES (Same as before)
# ------------------------------------------------------------------

def fetch_pending_tasks(headers):
    return query_database(os.environ.get("ADMIN_DB_ID"), headers, {
        "filter": {"property": "Status", "select": {"equals": "Not Started"}},
        "page_size": 10,
        "sorts": [{"property": "Priority", "direction": "ascending"}]
    })

def fetch_raw_ideas(headers):
    return query_database(os.environ.get("IDEAS_DB_ID"), headers, {
        "filter": {"property": "Status", "select": {"equals": "Raw"}},
        "page_size": 5,
        "sorts": [{"timestamp": "created_time", "direction": "descending"}]
    })

def fetch_people_followups(headers):
    return query_database(os.environ.get("PEOPLE_DB_ID"), headers, {
        "filter": {"or": [
            {"property": "Priority", "select": {"equals": "High"}},
            {"property": "Priority", "select": {"equals": "Follow Up"}}
        ]},
        "page_size": 5
    })

def fetch_recent_captures(headers, days=1):
    threshold = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    return query_database(os.environ.get("CAPTURE_INBOX_DB_ID"), headers, {
        "filter": {"property": "Captured Date", "date": {"on_or_after": threshold}},
        "page_size": 10
    })

def fetch_review_items(headers):
    return query_database(os.environ.get("CAPTURE_INBOX_DB_ID"), headers, {
        "filter": {"property": "Processing Status", "select": {"equals": "Reviewing"}},
        "page_size": 5
    })

def query_database(db_id, headers, payload):
    if not db_id: return []
    try:
        response = requests.post(f"https://api.notion.com/v1/databases/{db_id}/query", headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        return [{"title": extract_title(p)} for p in response.json().get("results", [])]
    except Exception as e:
        print(f"⚠️ Query error {db_id}: {e}")
        return []

def extract_title(page):
    props = page.get("properties", {})
    for name in ["Title", "Name", "Task", "title", "name"]:
        if name in props:
            title_arr = props[name].get("title", [])
            if title_arr: return title_arr[0].get("text", {}).get("content", "Untitled")
    return "Untitled"

def format_items(items):
    return "\n".join([f"- {i['title']}" for i in items[:10]]) if items else "None"

def generate_smart_summary(context):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key: return None
    
    prompt = f"""
    You are a personal productivity assistant. Create a concise daily briefing.
    {context}
    INSTRUCTIONS: Prioritize actions, max 3 bullets per section, use emojis. 
    OUTPUT FORMAT: Telegram Markdown (*bold*).
    """
    
    try:
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={api_key}",
            headers={"Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=20
        )
        return response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        print(f"⚠️ AI error: {e}")
        return None

def build_fallback_message(tasks, ideas, people, captures, reviews):
    lines = [f"🧠 *Daily Nudge* - {datetime.now().strftime('%B %d')}\n"]
    if tasks: lines.append(f"📋 *Tasks*: {len(tasks)} pending")
    if captures: lines.append(f"📥 *Captured*: {len(captures)} items")
    if reviews: lines.append(f"⚠️ *Review*: {len(reviews)} items")
    return "\n".join(lines)

if __name__ == "__main__":
    main()