import { axios } from "@pipedream/platform";

export default defineComponent({
  async run({ steps, $ }) {
    // Get message text and chat ID from trigger
    const messageText = steps.trigger.event.message?.text || "";
    const chatId = steps.trigger.event.message?.chat?.id;
    
    // Validate message
    if (!messageText || messageText.trim().length === 0) {
      console.log("Empty message, skipping");
      return { skipped: true };
    }
    
    console.log("Processing message:", messageText.substring(0, 50));
    
    // Create entry in Capture Inbox
    const response = await axios($, {
      method: "POST",
      url: "https://api.notion.com/v1/pages",
      headers: {
        "Authorization": `Bearer ${process.env.NOTION_TOKEN}`,
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
      },
      data: {
        parent: { 
          database_id: process.env.CAPTURE_INBOX_DB_ID 
        },
        properties: {
          "Title": {
            "title": [{
              "text": {
                "content": messageText.substring(0, 100)
              }
            }]
          },
          "Content": {
            "rich_text": [{
              "text": {
                "content": messageText
              }
            }]
          },
          "Processing Status": {
            "select": {
              "name": "New"
            }
          },
          "Captured Date": {
            "date": {
              "start": new Date().toISOString().split('T')[0]
            }
          }
        }
      }
    });
    
    console.log("✅ Created in Capture Inbox:", response.id);
    
    return {
      capturePageId: response.id,
      capturePageUrl: response.url,
      messageText: messageText,
      chatId: chatId
    };
  }
});