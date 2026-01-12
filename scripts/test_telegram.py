import requests
import os
from dotenv import load_dotenv

load_dotenv()

def test_connection():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    print(f"🔑 Token starts with: {token[:5]}..." if token else "❌ Token MISSING")
    print(f"🆔 Chat ID: {chat_id}")

    if not token or not chat_id:
        print("❌ Missing credentials in .env file!")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    
    # 1. Try sending a simple "Hello"
    print("\nAttempting to send test message...")
    try:
        response = requests.post(url, json={
            "chat_id": chat_id,
            "text": "Hello! This is a test message from your Python script."
        })
        
        # Print the raw response from Telegram
        print(f"Response Code: {response.status_code}")
        print(f"Response Body: {response.text}") # <--- THIS IS THE KEY INFO
        
        response.raise_for_status()
        print("✅ Success! Your credentials work.")
        
    except Exception as e:
        print("❌ Failed.")

if __name__ == "__main__":
    test_connection()