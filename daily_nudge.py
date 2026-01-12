#!/usr/bin/env python3
"""
Daily Nudge - Morning productivity briefing via Telegram.

Fetches pending tasks, raw ideas, people to follow up, and recent captures
from Notion, then generates an AI-powered summary and sends it via Telegram.
"""

import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# Import from secondbrain package
from secondbrain.config import settings
from secondbrain.logger import get_logger
from secondbrain.notion_client import NotionClient
from secondbrain.telegram_client import TelegramClient
from secondbrain.retry import retry_with_backoff
import requests

# Initialize logger
logger = get_logger(__name__)

# Initialize clients
notion_client = NotionClient()
telegram_client = TelegramClient()

MAX_WORKERS = 2


def main():
    """Main entry point for daily nudge."""
    start_time = time.time()
    logger.info("Starting Daily Nudge")

    try:
        settings.validate_notion()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        return

    # ------------------------------------------------------------------
    # 1. FETCH DATA IN PARALLEL
    # ------------------------------------------------------------------
    logger.info("Fetching data from Notion...")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_tasks = executor.submit(fetch_pending_tasks)
        future_ideas = executor.submit(fetch_raw_ideas)
        future_people = executor.submit(fetch_people_followups)
        future_captures = executor.submit(fetch_recent_captures, 1)
        future_reviews = executor.submit(fetch_review_items)

        pending_tasks = future_tasks.result()
        raw_ideas = future_ideas.result()
        people_followups = future_people.result()
        yesterday_captures = future_captures.result()
        review_items = future_reviews.result()

    elapsed_data = time.time() - start_time
    logger.info(
        f"Data fetched in {elapsed_data:.2f}s",
        extra={
            "tasks": len(pending_tasks),
            "ideas": len(raw_ideas),
            "people": len(people_followups),
            "captures": len(yesterday_captures)
        }
    )

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
    # 3. GENERATE SUMMARY
    # ------------------------------------------------------------------
    logger.info("Generating AI summary...")
    ai_start = time.time()
    summary = generate_smart_summary(context)
    logger.info(f"AI finished in {time.time() - ai_start:.2f}s")

    # ------------------------------------------------------------------
    # 4. SEND TO TELEGRAM
    # ------------------------------------------------------------------
    if summary:
        message = f"*Daily Nudge* - {datetime.now().strftime('%B %d')}\n\n{summary}"
    else:
        message = build_fallback_message(
            pending_tasks, raw_ideas, people_followups,
            yesterday_captures, review_items
        )

    telegram_client.send_message(message)
    logger.info(f"Total runtime: {time.time() - start_time:.2f}s")


# ------------------------------------------------------------------
# NOTION QUERIES
# ------------------------------------------------------------------

def fetch_pending_tasks():
    """Fetch tasks with 'Not Started' status."""
    if not settings.admin_db_id:
        return []
    return notion_client.query_database(
        settings.admin_db_id,
        {
            "filter": {"property": "Status", "select": {"equals": "Not Started"}},
            "page_size": 10,
            "sorts": [{"property": "Priority", "direction": "ascending"}]
        }
    )


def fetch_raw_ideas():
    """Fetch ideas with 'Raw' status."""
    if not settings.ideas_db_id:
        return []
    return notion_client.query_database(
        settings.ideas_db_id,
        {
            "filter": {"property": "Status", "select": {"equals": "Raw"}},
            "page_size": 5,
            "sorts": [{"timestamp": "created_time", "direction": "descending"}]
        }
    )


def fetch_people_followups():
    """Fetch people with high priority or follow-up needed."""
    if not settings.people_db_id:
        return []
    return notion_client.query_database(
        settings.people_db_id,
        {
            "filter": {"or": [
                {"property": "Priority", "select": {"equals": "High"}},
                {"property": "Priority", "select": {"equals": "Follow Up"}}
            ]},
            "page_size": 5
        }
    )


def fetch_recent_captures(days=1):
    """Fetch captures from the last N days."""
    if not settings.inbox_db_id:
        return []
    threshold = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    return notion_client.query_database(
        settings.inbox_db_id,
        {
            "filter": {"property": "Captured Date", "date": {"on_or_after": threshold}},
            "page_size": 10
        }
    )


def fetch_review_items():
    """Fetch items needing manual review."""
    if not settings.inbox_db_id:
        return []
    return notion_client.query_database(
        settings.inbox_db_id,
        {
            "filter": {"property": "Processing Status", "select": {"equals": "Reviewing"}},
            "page_size": 5
        }
    )


# ------------------------------------------------------------------
# HELPERS
# ------------------------------------------------------------------

def format_items(items):
    """Format a list of items for display."""
    if not items:
        return "None"
    return "\n".join([f"- {extract_title(i)}" for i in items[:10]])


def extract_title(item):
    """Extract title from a Notion page result."""
    if isinstance(item, dict):
        # If it's already processed
        if "title" in item:
            return item["title"]
        # Raw Notion page
        props = item.get("properties", {})
        for name in ["Title", "Name", "Task", "title", "name"]:
            if name in props:
                title_arr = props[name].get("title", [])
                if title_arr:
                    return title_arr[0].get("text", {}).get("content", "Untitled")
    return "Untitled"


def gemini_request_with_retry(url, payload, max_retries=3, base_delay=2.0):
    """
    Make a Gemini API request with exponential backoff retry.

    Args:
        url: Gemini API endpoint
        payload: Request payload
        max_retries: Maximum number of retries (default: 3)
        base_delay: Base delay in seconds (default: 2.0)

    Returns:
        Response JSON or None if all retries failed
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

            if response.status_code == 200:
                return response.json()

            if response.status_code == 429:
                if attempt < max_retries:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"Rate limited (429), retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})")
                    time.sleep(delay)
                    continue
                else:
                    logger.error(f"Rate limited after {max_retries} retries")
                    return None

            logger.warning(f"Gemini API error: {response.status_code}")
            return None

        except requests.exceptions.Timeout:
            last_error = "timeout"
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"Request timeout, retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
                continue
            else:
                logger.error(f"Request timeout after {max_retries} retries")
                return None

        except requests.exceptions.SSLError as e:
            logger.error(f"SSL error (no retry): {e}")
            return None

        except requests.exceptions.RequestException as e:
            last_error = str(e)
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"Request error, retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries}): {e}")
                time.sleep(delay)
                continue
            else:
                logger.error(f"Request failed after {max_retries} retries: {e}")
                return None

    return None


def generate_smart_summary(context):
    """Generate AI summary using Gemini."""
    if not settings.gemini_api_key:
        return None

    prompt = f"""
You are a personal productivity assistant. Create a concise daily briefing.
{context}
INSTRUCTIONS: Prioritize actions, max 3 bullets per section, use emojis.
OUTPUT FORMAT: Telegram Markdown (*bold*).
"""

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent?key={settings.gemini_api_key}"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}

        result = gemini_request_with_retry(url, payload)
        if result:
            return result["candidates"][0]["content"]["parts"][0]["text"].strip()
        return None

    except Exception as e:
        logger.error(f"AI summary error: {e}")
        return None


def build_fallback_message(tasks, ideas, people, captures, reviews):
    """Build a simple fallback message when AI fails."""
    lines = [f"*Daily Nudge* - {datetime.now().strftime('%B %d')}\n"]
    if tasks:
        lines.append(f"*Tasks*: {len(tasks)} pending")
    if captures:
        lines.append(f"*Captured*: {len(captures)} items")
    if reviews:
        lines.append(f"*Review*: {len(reviews)} items")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
