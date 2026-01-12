import requests
import json
import re
import os

def handler(pd: "pipedream"):
    """
    Classifies incoming text using Google Gemini (Flash Model).
    Returns a Python dictionary with the category and extracted data.
    """
    
    # ------------------------------------------------------------------
    # 1. GET INPUT
    # ------------------------------------------------------------------
    # We attempt to grab the text from the previous step.
    # Adjust "create_in_capture_inbox" to match your actual previous step name.
    # If that fails, we look for the trigger event.
    
    input_text = ""
    
    # Try getting from the previous step return value
    if "create_in_capture_inbox" in pd.steps:
        input_text = pd.steps["create_in_capture_inbox"].get("$return_value", {}).get("messageText", "")
    
    # Fallback to trigger event
    if not input_text:
        input_text = pd.steps["trigger"]["event"].get("message", {}).get("text", "")
        
    print(f"DEBUG: Input Text Received: '{input_text}'")

    if not input_text:
        return {"category": "TRASH", "reasoning": "Empty input"}

    # ------------------------------------------------------------------
    # 2. CONFIGURE API
    # ------------------------------------------------------------------
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("Missing GEMINI_API_KEY in Environment Variables")

    # We use the v1beta endpoint + the canonical 'gemini-1.5-flash' model name.
    # This is the most reliable combination currently.
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={api_key}"
    
    headers = {
        "Content-Type": "application/json"
    }

    # ------------------------------------------------------------------
    # 3. DEFINE PROMPT
    # ------------------------------------------------------------------
    # We ask for JSON in the text prompt to avoid API config errors.
    prompt_text = f"""
    You are a "Second Brain" classifier. Analyze the INPUT and return a JSON object.
    
    CATEGORIES:
    - PEOPLE (CRM, meetings, follow-ups)
    - ADMIN (Tasks, chores, bills, scheduling)
    - IDEAS (Thoughts, random notes, concepts)
    - KNOWLEDGE_BASE (Subtypes: Project, Area, Resource, Archive)
    - TRASH (Spam, nonsense)

    INPUT: "{input_text}"

    INSTRUCTIONS:
    1. Be decisive.
    2. Output ONLY raw JSON. No Markdown formatting.
    3. JSON Format:
    {{
        "category": "String",
        "subcategory": "String (use 'None' if not applicable)",
        "confidence": Float (0.0 to 1.0),
        "reasoning": "Brief string explanation",
        "extracted_data": {{
            "title": "A clean title for Notion",
            "key_info": "Summary"
        }}
    }}
    """

    payload = {
        "contents": [{
            "parts": [{"text": prompt_text}]
        }],
        "generationConfig": {
            "temperature": 0.1,  # Low temp = more deterministic/consistent
            "maxOutputTokens": 500
        }
    }

    # ------------------------------------------------------------------
    # 4. CALL API & PARSE
    # ------------------------------------------------------------------
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status() # Raises specific error if status is 400/404/500
        
        data = response.json()
        
        # Dig into the nested Gemini response structure
        raw_ai_text = data["candidates"][0]["content"]["parts"][0]["text"]
        
        # Clean up: Remove ```json and ``` if the model included them
        clean_json_text = re.sub(r"```json|```", "", raw_ai_text).strip()
        
        # Parse String -> Dictionary
        result = json.loads(clean_json_text)
        
        # ------------------------------------------------------------------
        # 5. QUANT LOGIC (Confidence Check)
        # ------------------------------------------------------------------
        if result.get("confidence", 0) < 0.6:
            result["original_category"] = result.get("category")
            result["category"] = "REVIEW_NEEDED"

        # Pass the original text through for the next step
        result["original_text"] = input_text
        
        print(f"DEBUG: Success! Category: {result.get('category')}")
        return result

    except Exception as e:
        print(f"ERROR: {e}")
        # If it fails, return a safe fallback so the workflow doesn't crash
        return {
            "category": "REVIEW_NEEDED",
            "reasoning": f"Code Error: {str(e)}",
            "original_text": input_text,
            "extracted_data": {"title": input_text[:50]}
        }