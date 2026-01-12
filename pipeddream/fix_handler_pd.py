"""
Fix Button Handler - Correct AI Misclassifications
====================================================
Trigger: Telegram message starting with "/fix"
Output: Moves item to correct category, confirms via Telegram

Commands:
- /fix IDEAS     - Move last capture to IDEAS
- /fix ADMIN     - Move last capture to ADMIN
- /fix PEOPLE    - Move last capture to PEOPLE
- /fix KNOWLEDGE - Move last capture to KNOWLEDGE_BASE
- /undo          - Revert last classification

Setup in Pipedream:
1. Create a new workflow OR add to existing Telegram workflow
2. Filter: steps.trigger.event.message?.text?.startsWith("/fix") ||
           steps.trigger.event.message?.text?.startsWith("/undo")
3. Add this Python step
4. Add Telegram "Send Message" step
"""

import requests
import json
import os
from datetime import datetime, timezone

# ------------------------------------------------------------------
# CATEGORY MAPPINGS
# ------------------------------------------------------------------
CATEGORY_MAP = {
    "IDEAS": {"env": "IDEAS_DB_ID", "name": "Ideas", "graph_sync": True},
    "IDEA": {"env": "IDEAS_DB_ID", "name": "Ideas", "graph_sync": True},
    "ADMIN": {"env": "ADMIN_DB_ID", "name": "Admin", "graph_sync": False},
    "TASK": {"env": "ADMIN_DB_ID", "name": "Admin", "graph_sync": False},
    "TASKS": {"env": "ADMIN_DB_ID", "name": "Admin", "graph_sync": False},
    "PEOPLE": {"env": "PEOPLE_DB_ID", "name": "People", "graph_sync": False},
    "PERSON": {"env": "PEOPLE_DB_ID", "name": "People", "graph_sync": False},
    "KNOWLEDGE": {"env": "KNOWLEDGE_BASE_DB_ID", "name": "Knowledge Base", "graph_sync": True},
    "KNOWLEDGE_BASE": {"env": "KNOWLEDGE_BASE_DB_ID", "name": "Knowledge Base", "graph_sync": True},
    "KB": {"env": "KNOWLEDGE_BASE_DB_ID", "name": "Knowledge Base", "graph_sync": True},
}


def handler(pd: "pipedream"):
    """
    Handles /fix commands to reclassify items.
    """

    message = pd.steps["trigger"]["event"].get("message", {})
    command_text = message.get("text", "").strip()
    chat_id = message.get("chat", {}).get("id")

    notion_token = os.environ.get("NOTION_TOKEN")
    headers = {
        "Authorization": f"Bearer {notion_token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }

    # ------------------------------------------------------------------
    # PARSE COMMAND
    # ------------------------------------------------------------------

    if command_text.lower().startswith("/undo"):
        return handle_undo(headers, chat_id)

    if command_text.lower().startswith("/fix"):
        parts = command_text.split()
        if len(parts) < 2:
            return {
                "response": "❌ *Usage:* `/fix CATEGORY`\n\nCategories: IDEAS, ADMIN, PEOPLE, KNOWLEDGE",
                "chat_id": chat_id,
                "success": False
            }

        target_category = parts[1].upper()

        if target_category not in CATEGORY_MAP:
            return {
                "response": f"❌ Unknown category: `{target_category}`\n\nValid: IDEAS, ADMIN, PEOPLE, KNOWLEDGE",
                "chat_id": chat_id,
                "success": False
            }

        return handle_fix(headers, chat_id, target_category)

    return {
        "response": "❓ Unknown command. Use `/fix CATEGORY` or `/undo`",
        "chat_id": chat_id,
        "success": False
    }


def handle_fix(headers, chat_id, target_category):
    """Move the most recent capture to a different category"""

    # 1. Find the most recent item in Capture Inbox that was "Processed"
    recent_item = find_recent_processed_item(headers)

    if not recent_item:
        return {
            "response": "⚠️ No recent processed items found to fix.",
            "chat_id": chat_id,
            "success": False
        }

    page_id = recent_item["id"]
    title = recent_item["title"]
    content = recent_item["content"]
    old_category = recent_item.get("category", "Unknown")

    print(f"📝 Fixing: '{title}' from {old_category} to {target_category}")

    # 2. Create new entry in target database
    target_config = CATEGORY_MAP[target_category]
    target_db_id = os.environ.get(target_config["env"])

    if not target_db_id:
        return {
            "response": f"❌ Database ID not configured for {target_category}",
            "chat_id": chat_id,
            "success": False
        }

    new_page_id = create_in_target_db(headers, target_db_id, target_category, title, content)

    if not new_page_id:
        return {
            "response": "❌ Failed to create entry in target database.",
            "chat_id": chat_id,
            "success": False
        }

    # 3. Update the Capture Inbox item to show it was fixed
    update_capture_inbox(headers, page_id, target_category)

    # 4. Store fix action for potential undo
    store_fix_action(page_id, new_page_id, old_category, target_category, title)

    # 5. Build response
    target_name = target_config["name"]
    response = f"""✅ *Fixed!*

📄 *{title[:50]}{'...' if len(title) > 50 else ''}*

Moved from: {old_category}
Moved to: *{target_name}*

{"🔗 Will sync to graph on next run." if target_config["graph_sync"] else ""}

_Use `/undo` to revert this change._"""

    return {
        "response": response,
        "chat_id": chat_id,
        "success": True,
        "old_category": old_category,
        "new_category": target_category,
        "page_id": new_page_id
    }


def handle_undo(headers, chat_id):
    """Revert the last fix action"""

    # Get last fix action from data store (if available)
    last_action = get_last_fix_action()

    if not last_action:
        return {
            "response": "⚠️ No recent fix action to undo.\n\n_Undo only works for the most recent `/fix` command._",
            "chat_id": chat_id,
            "success": False
        }

    # Archive the incorrectly-placed page
    try:
        archive_url = f"https://api.notion.com/v1/pages/{last_action['new_page_id']}"
        requests.patch(
            archive_url,
            headers=headers,
            json={"archived": True}
        )
    except Exception as e:
        print(f"⚠️ Could not archive page: {e}")

    # Mark the original capture inbox item as needing reprocessing
    try:
        update_url = f"https://api.notion.com/v1/pages/{last_action['original_page_id']}"
        requests.patch(
            update_url,
            headers=headers,
            json={
                "properties": {
                    "Processing Status": {"select": {"name": "New"}}
                }
            }
        )
    except Exception as e:
        print(f"⚠️ Could not reset original item: {e}")

    clear_last_fix_action()

    return {
        "response": f"""↩️ *Undone!*

📄 *{last_action['title'][:50]}*

Reverted from: {last_action['new_category']}
Status: Back in queue for reprocessing

_The item will be reclassified on next sync._""",
        "chat_id": chat_id,
        "success": True
    }


# ------------------------------------------------------------------
# HELPER FUNCTIONS
# ------------------------------------------------------------------

def find_recent_processed_item(headers):
    """Find the most recently processed item in Capture Inbox"""

    inbox_db_id = os.environ.get("CAPTURE_INBOX_DB_ID")
    if not inbox_db_id:
        return None

    url = f"https://api.notion.com/v1/databases/{inbox_db_id}/query"

    payload = {
        "filter": {
            "property": "Processing Status",
            "select": {"equals": "Processed"}
        },
        "sorts": [{"timestamp": "last_edited_time", "direction": "descending"}],
        "page_size": 1
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()

        results = response.json().get("results", [])
        if not results:
            return None

        page = results[0]
        props = page.get("properties", {})

        # Extract title
        title = "Untitled"
        for name in ["Title", "Name", "title"]:
            if name in props and props[name].get("type") == "title":
                title_arr = props[name].get("title", [])
                if title_arr:
                    title = title_arr[0].get("text", {}).get("content", "Untitled")
                    break

        # Extract content
        content = ""
        for name in ["Content", "Notes", "content"]:
            if name in props and props[name].get("type") == "rich_text":
                text_arr = props[name].get("rich_text", [])
                if text_arr:
                    content = text_arr[0].get("text", {}).get("content", "")
                    break

        # Extract AI category if stored
        category = "Unknown"
        if "AI Category" in props and props["AI Category"].get("type") == "select":
            sel = props["AI Category"].get("select")
            if sel:
                category = sel.get("name", "Unknown")

        return {
            "id": page["id"],
            "title": title,
            "content": content,
            "category": category
        }

    except Exception as e:
        print(f"⚠️ Error finding recent item: {e}")
        return None


def create_in_target_db(headers, db_id, category, title, content):
    """Create entry in the target database"""

    # Build properties based on category
    if category in ["IDEAS", "IDEA"]:
        props = {
            "Title": {"title": [{"text": {"content": title}}]},
            "Content": {"rich_text": [{"text": {"content": content}}]},
            "Status": {"select": {"name": "Raw"}},
            "Processing Status": {"select": {"name": "New"}}  # Trigger graph sync
        }
    elif category in ["ADMIN", "TASK", "TASKS"]:
        props = {
            "Task": {"title": [{"text": {"content": title}}]},
            "Notes": {"rich_text": [{"text": {"content": content}}]},
            "Status": {"select": {"name": "Not Started"}}
        }
    elif category in ["PEOPLE", "PERSON"]:
        props = {
            "Name": {"title": [{"text": {"content": title}}]},
            "Notes": {"rich_text": [{"text": {"content": content}}]},
            "Priority": {"select": {"name": "Medium"}}
        }
    elif category in ["KNOWLEDGE", "KNOWLEDGE_BASE", "KB"]:
        props = {
            "Title": {"title": [{"text": {"content": title}}]},
            "Content": {"rich_text": [{"text": {"content": content}}]},
            "PARA Type": {"select": {"name": "Resource"}},
            "Status": {"select": {"name": "Active"}},
            "Processing Status": {"select": {"name": "New"}}  # Trigger graph sync
        }
    else:
        return None

    try:
        response = requests.post(
            "https://api.notion.com/v1/pages",
            headers=headers,
            json={"parent": {"database_id": db_id}, "properties": props},
            timeout=10
        )
        response.raise_for_status()
        return response.json()["id"]

    except Exception as e:
        print(f"❌ Error creating in target DB: {e}")
        return None


def update_capture_inbox(headers, page_id, new_category):
    """Update the original capture inbox item"""

    url = f"https://api.notion.com/v1/pages/{page_id}"

    try:
        requests.patch(
            url,
            headers=headers,
            json={
                "properties": {
                    "Processing Status": {"select": {"name": "Fixed"}},
                    "AI Category": {"select": {"name": new_category}}
                }
            },
            timeout=10
        )
    except Exception as e:
        print(f"⚠️ Could not update capture inbox: {e}")


# ------------------------------------------------------------------
# SIMPLE IN-MEMORY STATE (for undo)
# Note: In production, use Pipedream Data Store for persistence
# ------------------------------------------------------------------

_last_fix_action = None


def store_fix_action(original_page_id, new_page_id, old_category, new_category, title):
    global _last_fix_action
    _last_fix_action = {
        "original_page_id": original_page_id,
        "new_page_id": new_page_id,
        "old_category": old_category,
        "new_category": new_category,
        "title": title,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


def get_last_fix_action():
    return _last_fix_action


def clear_last_fix_action():
    global _last_fix_action
    _last_fix_action = None
