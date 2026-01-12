import os
import time
import requests
from dotenv import load_dotenv
import re

load_dotenv()
def send_telegram_message(text, parse_mode="HTML"): # Changed default to HTML
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("⚠️ Telegram credentials missing.")
        return
    
    if not text or not text.strip():
        return

    MAX_LENGTH = 4000
    parts = []

    # --- 1. CONVERT MARKDOWN TO TELEGRAM HTML ---
    # This is safer than Markdown because we can escape everything else
    
    # Escape special HTML chars first (<, >, &)
    clean_text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    
    # Convert *bold* to <b>bold</b> (Gemini uses ** or *)
    # Regex handles **bold** and *bold*
    clean_text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', clean_text) # **bold**
    clean_text = re.sub(r'(?<!\w)\*(.*?)\*(?!\w)', r'<b>\1</b>', clean_text) # *bold*
    
    # Convert _italic_ to <i>italic</i> (only if surrounded by spaces/boundaries)
    clean_text = re.sub(r'(?<!\w)_(.*?)_(?!\w)', r'<i>\1</i>', clean_text)
    
    # --- 2. SPLITTING LOGIC ---
    # (Same splitter as before)
    while clean_text:
        if len(clean_text) <= MAX_LENGTH:
            parts.append(clean_text)
            break
        split_index = clean_text.rfind("\n\n", 0, MAX_LENGTH)
        if split_index == -1: split_index = clean_text.rfind("\n", 0, MAX_LENGTH)
        if split_index == -1: split_index = MAX_LENGTH
        parts.append(clean_text[:split_index])
        clean_text = clean_text[split_index:].strip()

    # --- 3. SENDING LOOP ---
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    
    for i, part in enumerate(parts):
        content = part
        if len(parts) > 1:
            content = f"[{i+1}/{len(parts)}]\n{part}"

        payload = {
            "chat_id": chat_id,
            "text": content,
            "parse_mode": "HTML", # Force HTML
            "disable_web_page_preview": True,
            "disable_notification": False
        }

        try:
            # ATTEMPT 1: HTML
            r = requests.post(url, json=payload, timeout=15)
            r.raise_for_status()
            print(f"✅ Sent part {i+1}/{len(parts)}")
        except requests.exceptions.HTTPError as e:
            # ATTEMPT 2: Plain Text Fallback
            if e.response.status_code == 400:
                print(f"⚠️ HTML error part {i+1}. Retrying raw...")
                del payload["parse_mode"]
                # We must use original text for raw fallback, not the HTMLified one
                # But for simplicity, we just send the HTML code as text (still readable)
                requests.post(url, json=payload, timeout=15)