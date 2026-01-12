import requests
import os

def handler(pd: "pipedream"):
    """
    Routes classified messages to appropriate Notion databases.
    Only routes KNOWLEDGE_BASE and IDEAS to prepare for GraphRAG.
    PEOPLE and ADMIN stay in Notion only (no graph).
    """
    
    # ------------------------------------------------------------------
    # 1. GET CLASSIFICATION RESULTS
    # ------------------------------------------------------------------
    classification = pd.steps["classify_with_gemini"]["$return_value"]
    
    category = classification.get("category", "REVIEW_NEEDED")
    confidence = classification.get("confidence", 0)
    reasoning = classification.get("reasoning", "No reasoning provided")
    message_text = classification.get("original_text", "")
    extracted_data = classification.get("extracted_data", {})
    
    capture_page_id = pd.steps["create_in_capture_inbox"]["$return_value"]["capturePageId"]
    
    print(f"DEBUG: Routing category={category}, confidence={confidence}")
    
    # ------------------------------------------------------------------
    # 2. NOTION API SETUP
    # ------------------------------------------------------------------
    notion_token = os.environ.get("NOTION_TOKEN")
    headers = {
        "Authorization": f"Bearer {notion_token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    
    # ------------------------------------------------------------------
    # 3. LOW CONFIDENCE → FLAG FOR REVIEW
    # ------------------------------------------------------------------
    if confidence < 0.6 or category == "REVIEW_NEEDED":
        print("⚠️ Low confidence - flagging for manual review")
        
        # Update Capture Inbox status
        update_url = f"https://api.notion.com/v1/pages/{capture_page_id}"
        update_payload = {
            "properties": {
                "Processing Status": {"select": {"name": "Reviewing"}},
                "AI Category": {"select": {"name": category}},
                "AI Confidence": {"number": confidence},
                "AI Reasoning": {"rich_text": [{"text": {"content": reasoning}}]}
            }
        }
        
        response = requests.patch(update_url, headers=headers, json=update_payload)
        
        return {
            "action": "flagged_for_review",
            "category": category,
            "confidence": confidence
        }
    
    # ------------------------------------------------------------------
    # 4. HIGH CONFIDENCE → ROUTE TO TARGET DATABASE
    # ------------------------------------------------------------------
    target_page_id = None
    target_database = None
    
    try:
        if category == "PEOPLE":
            target_page_id = create_in_people(headers, message_text, extracted_data)
            target_database = "People"
            
        elif category == "ADMIN":
            target_page_id = create_in_admin(headers, message_text, extracted_data)
            target_database = "Admin"
            
        elif category == "IDEAS":
            target_page_id = create_in_ideas(headers, message_text, extracted_data)
            target_database = "Ideas"
            
        elif category == "KNOWLEDGE_BASE":
            subcategory = classification.get("subcategory", "Resource")
            target_page_id = create_in_knowledge_base(headers, message_text, extracted_data, subcategory)
            target_database = "Knowledge Base"
            
        elif category == "TRASH":
            print("📦 Classified as trash - not creating entry")
            target_database = "Trash (not created)"
        
        # Update Capture Inbox to "Processed"
        update_url = f"https://api.notion.com/v1/pages/{capture_page_id}"
        update_payload = {
            "properties": {
                "Processing Status": {"select": {"name": "Processed"}},
                "AI Category": {"select": {"name": category}},
                "AI Confidence": {"number": confidence},
                "AI Reasoning": {"rich_text": [{"text": {"content": reasoning}}]}
            }
        }
        
        requests.patch(update_url, headers=headers, json=update_payload)
        
        print(f"✅ Successfully routed to: {target_database}")
        
        return {
            "action": "routed",
            "category": category,
            "target_database": target_database,
            "target_page_id": target_page_id,
            "graph_eligible": category in ["IDEAS", "KNOWLEDGE_BASE"]  # Flag for GraphRAG
        }
        
    except Exception as e:
        print(f"ERROR routing to database: {e}")
        return {
            "action": "error",
            "error": str(e),
            "category": category
        }


# ------------------------------------------------------------------
# HELPER FUNCTIONS - Create entries in each database
# ------------------------------------------------------------------

def create_in_people(headers, text, extracted_data):
    """Create entry in People database"""
    title = extracted_data.get("title", text[:100])

    payload = {
        "parent": {"database_id": os.environ.get("PEOPLE_DB_ID")},
        "properties": {
            "Name": {"title": [{"text": {"content": title}}]},
            "Notes": {"rich_text": [{"text": {"content": text}}]},
            "Priority": {"select": {"name": "Medium"}}
        }
    }
    
    response = requests.post(
        "https://api.notion.com/v1/pages",
        headers=headers,
        json=payload
    )
    response.raise_for_status()
    return response.json()["id"]


def create_in_admin(headers, text, extracted_data):
    """Create entry in Admin database"""
    title = extracted_data.get("title", text)
    
    payload = {
        "parent": {"database_id": os.environ.get("ADMIN_DB_ID")},
        "properties": {
            "Task": {"title": [{"text": {"content": title}}]},
            "Notes": {"rich_text": [{"text": {"content": text}}]},
            "Status": {"select": {"name": "Not Started"}},
            "Priority": {"select": {"name": "Medium"}}
        }
    }
    
    response = requests.post(
        "https://api.notion.com/v1/pages",
        headers=headers,
        json=payload
    )
    response.raise_for_status()
    return response.json()["id"]


def create_in_ideas(headers, text, extracted_data):
    """Create entry in Ideas database"""
    title = extracted_data.get("title", text[:100])

    payload = {
        "parent": {"database_id": os.environ.get("IDEAS_DB_ID")},
        "properties": {
            "Title": {"title": [{"text": {"content": title}}]},
            "Content": {"rich_text": [{"text": {"content": text}}]},
            "Status": {"select": {"name": "Raw"}},
            "Potential": {"select": {"name": "Medium"}},
            "Processing Status": {"select": {"name": "Active"}}
        }
    }

    response = requests.post(
        "https://api.notion.com/v1/pages",
        headers=headers,
        json=payload
    )
    response.raise_for_status()
    return response.json()["id"]


def create_in_knowledge_base(headers, text, extracted_data, subcategory):
    """Create entry in Knowledge Base database"""
    title = extracted_data.get("title", text[:100])
    para_type = subcategory if subcategory else "Resource"

    payload = {
        "parent": {"database_id": os.environ.get("KNOWLEDGE_BASE_DB_ID")},
        "properties": {
            "Title": {"title": [{"text": {"content": title}}]},
            "PARA Type": {"select": {"name": para_type}},
            "Status": {"select": {"name": "Active"}},
            "Content": {"rich_text": [{"text": {"content": text}}]},
            "Processing Status": {"select": {"name": "Active"}}
        }
    }

    response = requests.post(
        "https://api.notion.com/v1/pages",
        headers=headers,
        json=payload
    )
    response.raise_for_status()
    return response.json()["id"]