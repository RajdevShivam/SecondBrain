#!/usr/bin/env python3
"""
Weekly Review - Comprehensive weekly summary via Telegram.

Aggregates data from Notion databases and Neo4j knowledge graph,
generates AI insights, and sends a detailed weekly review.
"""

import time
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

# Import from secondbrain package
from secondbrain.config import settings
from secondbrain.logger import get_logger
from secondbrain.notion_client import NotionClient
from secondbrain.neo4j_client import Neo4jClient
from secondbrain.telegram_client import TelegramClient
from secondbrain.graph_services import GraphHealthChecker
from secondbrain.retry import retry_with_backoff
import requests

# Initialize logger
logger = get_logger(__name__)

# Initialize clients
notion_client = NotionClient()
neo4j_client = Neo4jClient()
telegram_client = TelegramClient()

MAX_WORKERS = 2


def main():
    """Main entry point for weekly review."""
    start_time = time.time()
    logger.info("Starting Weekly Review")

    try:
        settings.validate_notion()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        return

    week_start = datetime.now(timezone.utc) - timedelta(days=7)
    week_start_iso = week_start.strftime("%Y-%m-%d")

    # ------------------------------------------------------------------
    # 1. PARALLEL NOTION FETCHING
    # ------------------------------------------------------------------
    logger.info("Fetching Notion data...")

    results = {}
    tasks = [
        ("weekly_tasks", fetch_items_this_week, ("ADMIN", week_start_iso)),
        ("weekly_ideas", fetch_items_this_week, ("IDEAS", week_start_iso)),
        ("weekly_knowledge", fetch_items_this_week, ("KNOWLEDGE_BASE", week_start_iso)),
        ("weekly_people", fetch_items_this_week, ("PEOPLE", week_start_iso)),
        ("weekly_captures", fetch_items_this_week, ("INBOX", week_start_iso)),
        ("completed_tasks", fetch_completed_tasks, (week_start_iso,)),
        ("pending_tasks", fetch_pending_items, ("ADMIN", "Status", "Not Started")),
        ("review_items", fetch_review_items, ())
    ]

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_key = {executor.submit(func, *args): key for key, func, args in tasks}

        for future in future_to_key:
            key = future_to_key[future]
            try:
                results[key] = future.result()
            except Exception as e:
                logger.warning(f"Failed to fetch {key}: {e}")
                results[key] = []

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

    logger.info(
        f"Notion data loaded in {time.time() - start_time:.2f}s",
        extra=notion_stats
    )

    # ------------------------------------------------------------------
    # 2. NEO4J DATA
    # ------------------------------------------------------------------
    logger.info("Querying knowledge graph...")
    graph_insights = fetch_graph_insights(week_start_iso)

    # Get graph health metrics
    health_checker = GraphHealthChecker(neo4j_client)
    graph_health = health_checker.get_health_report()

    logger.info(
        f"Graph: {len(graph_insights.get('new_connections', []))} new connections",
        extra={"health": graph_health}
    )

    # ------------------------------------------------------------------
    # 3. GENERATE REVIEW
    # ------------------------------------------------------------------
    logger.info("Generating AI analysis...")
    context = build_review_context(
        notion_stats,
        results["weekly_ideas"],
        results["weekly_knowledge"],
        results["weekly_people"],
        results["completed_tasks"],
        results["pending_tasks"],
        results["review_items"],
        graph_insights,
        graph_health
    )

    review_text = generate_comprehensive_review(context)

    if not review_text:
        review_text = build_fallback_review(notion_stats, graph_insights, graph_health)

    # ------------------------------------------------------------------
    # 4. SEND TO TELEGRAM
    # ------------------------------------------------------------------
    logger.info("Sending to Telegram...")
    telegram_client.send_message(review_text)

    logger.info(f"Weekly review complete. Total time: {time.time() - start_time:.2f}s")


# ------------------------------------------------------------------
# NOTION QUERIES
# ------------------------------------------------------------------

def get_db_id(db_name):
    """Get database ID by name from config."""
    db_config = settings.db_config
    config = db_config.get(db_name, {})
    return config.get("id")


def fetch_items_this_week(db_name, date):
    """Fetch items created this week from a database."""
    db_id = get_db_id(db_name)
    if not db_id:
        return []
    return notion_client.query_database(
        db_id,
        {
            "filter": {"timestamp": "created_time", "created_time": {"on_or_after": date}},
            "page_size": 100
        }
    )


def fetch_completed_tasks(date):
    """Fetch tasks completed this week."""
    db_id = get_db_id("ADMIN")
    if not db_id:
        return []
    return notion_client.query_database(
        db_id,
        {
            "filter": {
                "and": [
                    {"property": "Status", "select": {"equals": "Done"}},
                    {"timestamp": "last_edited_time", "last_edited_time": {"on_or_after": date}}
                ]
            },
            "page_size": 50
        }
    )


def fetch_pending_items(db_name, prop, val):
    """Fetch items with a specific property value."""
    db_id = get_db_id(db_name)
    if not db_id:
        return []
    return notion_client.query_database(
        db_id,
        {
            "filter": {"property": prop, "select": {"equals": val}},
            "page_size": 20
        }
    )


def fetch_review_items():
    """Fetch items needing manual review."""
    db_id = get_db_id("INBOX")
    if not db_id:
        return []
    return notion_client.query_database(
        db_id,
        {
            "filter": {"property": "Processing Status", "select": {"equals": "Reviewing"}},
            "page_size": 10
        }
    )


# ------------------------------------------------------------------
# NEO4J QUERIES
# ------------------------------------------------------------------

def fetch_graph_insights(date):
    """Fetch insights from the knowledge graph."""
    insights = {
        "new_connections": [],
        "top_concepts": [],
        "actionable_ideas": [],
        "clusters": []
    }

    try:
        with neo4j_client.session() as session:
            # 1. New Connections (using first_linked)
            result = session.run(
                """
                MATCH (a:Concept)-[r]->(b:Concept)
                WHERE r.first_linked >= datetime($d)
                RETURN a.name, type(r), b.name
                LIMIT 20
                """,
                d=date + "T00:00:00Z"
            )
            insights["new_connections"] = [
                {"source": r[0], "relation": r[1], "target": r[2]}
                for r in result
            ]

            # 2. Top Concepts by connections
            result = session.run(
                """
                MATCH (c:Concept)-[r]-()
                WITH c, count(r) as conn
                WHERE conn > 1
                RETURN c.name, conn
                ORDER BY conn DESC
                LIMIT 10
                """
            )
            insights["top_concepts"] = [
                {"name": r[0], "connections": r[1]}
                for r in result
            ]

            # 3. Actionable Ideas (ideas with multiple concept mentions)
            result = session.run(
                """
                MATCH (i:Idea)-[:MENTIONS]->(c:Concept)
                WITH i, count(c) as cc
                WHERE cc >= 2
                RETURN i.title, cc
                ORDER BY cc DESC
                LIMIT 5
                """
            )
            insights["actionable_ideas"] = [
                {"title": r[0], "count": r[1]}
                for r in result
            ]

    except Exception as e:
        logger.error(f"Neo4j query error: {e}")

    return insights


# ------------------------------------------------------------------
# AI GENERATION
# ------------------------------------------------------------------

def build_review_context(stats, ideas, knowledge, people, completed, pending, review, graph, health):
    """Build context string for AI review generation."""
    return f"""
WEEKLY REVIEW DATA
Stats: {stats}

New Ideas ({len(ideas)}): {[extract_title(i) for i in ideas[:10]]}
New Knowledge ({len(knowledge)}): {[extract_title(k) for k in knowledge[:10]]}
Tasks Completed ({len(completed)}): {[extract_title(t) for t in completed[:10]]}
Tasks Pending ({len(pending)}): {[extract_title(t) for t in pending[:10]]}

Graph Insights:
- New Connections: {len(graph.get('new_connections', []))}
- Top Concepts: {[c['name'] for c in graph.get('top_concepts', [])]}
- Actionable Ideas: {[i['title'] for i in graph.get('actionable_ideas', [])]}

Graph Health:
- Total Concepts: {health.get('total_concepts', 0)}
- Total Relationships: {health.get('total_relationships', 0)}
- Orphan Concepts: {health.get('orphan_concepts', 0)}
- Stale Concepts (90+ days): {health.get('stale_concepts', 0)}
"""


def extract_title(item):
    """Extract title from a Notion page result."""
    if isinstance(item, dict):
        if "title" in item:
            return item["title"]
        props = item.get("properties", {})
        for name in ["Title", "Name", "Task"]:
            if name in props:
                t = props[name].get("title", [])
                if t:
                    return t[0].get("text", {}).get("content", "Untitled")
    return "Untitled"


def gemini_request_with_retry(url, payload, max_retries=3, base_delay=2.0, timeout=None):
    """
    Make a Gemini API request with exponential backoff retry.

    Args:
        url: Gemini API endpoint
        payload: Request payload
        max_retries: Maximum number of retries (default: 3)
        base_delay: Base delay in seconds (default: 2.0)
        timeout: Request timeout in seconds (default: settings.gemini_timeout)

    Returns:
        Response JSON or None if all retries failed
    """
    last_error = None
    timeout = timeout or settings.gemini_timeout

    for attempt in range(max_retries + 1):
        try:
            response = requests.post(
                url,
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=timeout
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


def generate_comprehensive_review(context):
    """Generate comprehensive weekly review using Gemini."""
    if not settings.gemini_api_key:
        return None

    prompt = f"""
You are a personal knowledge assistant. Generate a COMPREHENSIVE weekly review.

{context}

FORMAT:
1. **Week in Numbers** (Quick stats)
2. **Deep Work** (Tasks completed & knowledge captured)
3. **Graph Intelligence** (Connections & patterns found in Neo4j)
4. **Graph Health** (Orphans, stale concepts needing attention)
5. **Focus for Next Week** (Based on pending tasks)

Use Telegram Markdown (*bold*). Be insightful, not just descriptive.
"""

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent?key={settings.gemini_api_key}"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}

        # Use 2 minute timeout for comprehensive reviews (they have large prompts)
        result = gemini_request_with_retry(url, payload, timeout=120)
        if result:
            return result["candidates"][0]["content"]["parts"][0]["text"].strip()
        return None

    except Exception as e:
        logger.error(f"AI review generation error: {e}")
        return None


def build_fallback_review(stats, graph, health):
    """Build a simple fallback review when AI fails."""
    lines = [
        f"*Weekly Review* - {datetime.now().strftime('%B %d, %Y')}\n",
        f"*Captures*: {stats['captures']}",
        f"*Tasks Done*: {stats['tasks_completed']}",
        f"*Tasks Pending*: {stats['tasks_pending']}",
        f"*New Ideas*: {stats['ideas']}",
        f"\n*Graph Stats*:",
        f"- New Connections: {len(graph.get('new_connections', []))}",
        f"- Total Concepts: {health.get('total_concepts', 0)}",
        f"- Orphans: {health.get('orphan_concepts', 0)}"
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
