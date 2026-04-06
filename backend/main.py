import os
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import logging
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import datetime
import requests

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Local Connect Central Orchestrator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Configuration ---
CHATWOOT_BASE_URL = os.environ.get("CHATWOOT_BASE_URL", "http://localhost:3002")
CHATWOOT_API_TOKEN = os.environ.get("CHATWOOT_API_TOKEN", "")
CHATWOOT_ACCOUNT_ID = os.environ.get("CHATWOOT_ACCOUNT_ID", "1")
DIFY_API_URL = os.environ.get("DIFY_API_URL", "https://api.dify.ai/v1")
DIFY_API_KEY = os.environ.get("DIFY_API_KEY", "")

SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "REPLACE_WITH_YOUR_SPREADSHEET_ID")
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
CREDENTIALS_FILE = 'credentials.json'

# --- In-Memory State ---
# Map Chatwoot conversation_id -> Dify conversation_id
dify_conversations = {}

# --- API Methods ---
def append_to_sheet(range_name, values):
    if SPREADSHEET_ID == "REPLACE_WITH_YOUR_SPREADSHEET_ID":
        logger.warning("SPREADSHEET_ID is not set! Skipping Google Sheets update.")
        return False
        
    try:
        credentials = Credentials.from_service_account_file(
            CREDENTIALS_FILE, scopes=SCOPES)
        service = build('sheets', 'v4', credentials=credentials)
        
        body = {
            'values': [values]
        }
        result = service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID, range=range_name,
            valueInputOption="USER_ENTERED", body=body).execute()
        
        logger.info(f"{result.get('updates').get('updatedCells')} cells appended to {range_name}.")
        return True
    except Exception as e:
        logger.error(f"Failed to append to Google Sheets: {e}")
        return False

def send_chatwoot_message(account_id: int, conversation_id: int, content: str):
    headers = {"api_access_token": CHATWOOT_API_TOKEN}
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{account_id}/conversations/{conversation_id}/messages"
    payload = {"content": content, "message_type": "outgoing"}
    try:
        resp = requests.post(url, json=payload, headers=headers)
        if resp.status_code in [200, 201]:
            logger.info(f"Replied to Chatwoot Conversation {conversation_id}.")
        else:
            logger.error(f"Chatwoot API Error: Status {resp.status_code} - {resp.text}")
    except Exception as e:
        logger.error(f"Failed to send message to Chatwoot: {e}")

def add_chatwoot_label(account_id: int, conversation_id: int, label: str):
    headers = {"api_access_token": CHATWOOT_API_TOKEN}
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{account_id}/conversations/{conversation_id}/labels"
    payload = {"labels": [label]}
    try:
        requests.post(url, json=payload, headers=headers)
        logger.info(f"Added label '{label}' to Conversation {conversation_id}.")
    except Exception as e:
        logger.error(f"Failed to add label to Chatwoot: {e}")

def toggle_typing_status(account_id: int, conversation_id: int, status: str):
    """
    status should be 'on' or 'off'
    """
    headers = {"api_access_token": CHATWOOT_API_TOKEN}
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{account_id}/conversations/{conversation_id}/toggle_typing_status"
    payload = {"typing_status": status}
    try:
        requests.post(url, json=payload, headers=headers)
        logger.info(f"Set typing status '{status}' for Conversation {conversation_id}.")
    except Exception as e:
        logger.error(f"Failed to toggle typing status: {e}")

def ask_dify(query: str, user_id: str, conversation_id: int):
    if not DIFY_API_KEY:
        return "DIFY API KEY not found."
        
    headers = {
        "Authorization": f"Bearer {DIFY_API_KEY}",
        "Content-Type": "application/json"
    }
    
    dify_conv_id = dify_conversations.get(conversation_id, "")
    
    payload = {
        "inputs": {},
        "query": query,
        "response_mode": "blocking",
        "conversation_id": dify_conv_id,
        "user": str(user_id)
    }
    
    try:
        resp = requests.post(f"{DIFY_API_URL}/chat-messages", json=payload, headers=headers)
        data = resp.json()
        new_conv_id = data.get("conversation_id")
        if new_conv_id:
            dify_conversations[conversation_id] = new_conv_id
            
        return data.get("answer", "ขออภัย ฉันไม่สามารถดำเนินการได้ในขณะนี้")
    except Exception as e:
        logger.error(f"Failed to query Dify: {e}")
        return "ขออภัย ระบบขัดข้องชั่วคราว [HANDOFF]"

def process_ai_reply(account_id: int, content: str, user_id: str, conversation_id: int):
    logger.info(f"Processing AI Reply for conversation {conversation_id}...")
    
    # Show typing indicator while Dify is "thinking"
    toggle_typing_status(account_id, conversation_id, "on")
    
    answer = ask_dify(content, user_id, conversation_id)
    
    # Turn off typing indicator once we have the answer
    toggle_typing_status(account_id, conversation_id, "off")
    
    if "[HANDOFF]" in answer:
        clean_answer = answer.replace("[HANDOFF]", "").strip()
        if not clean_answer:
            clean_answer = "ระบบกำลังส่งต่อเพื่อติดต่อกับพนักงานจริง กรุณารอสักครู่..."
        
        send_chatwoot_message(account_id, conversation_id, clean_answer)
        add_chatwoot_label(account_id, conversation_id, "human-needed")
    else:
        # Standard AI response
        send_chatwoot_message(account_id, conversation_id, answer)

# --- Models ---
class SosPayload(BaseModel):
    pin: str
    nickname: str
    phone: str
    latitude: float
    longitude: float

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Local Connect Webhook Server is running."}

@app.post("/webhook/chatwoot")
async def chatwoot_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Receives events from Chatwoot, such as new messages created.
    """
    payload = await request.json()
    event_name = payload.get("event")
    logger.info(f"Received Chatwoot Webhook Event: {event_name}")
    
    # Process the payload based on event type
    if event_name == "message_created":
        message_type = payload.get("message_type") # incoming (0) vs outgoing (1)
        content = payload.get("content") or ""
        sender = payload.get("sender", {})
        sender_name = sender.get("name", "Unknown")
        # Ensure sender_id is extracted properly from dict if needed and cast to str
        sender_id = str(sender.get("id", "user")) if isinstance(sender, dict) else "user"
        is_private = payload.get("private", False)
        
        conversation = payload.get("conversation", {})
        conversation_id = conversation.get("id")
        labels = conversation.get("labels", [])
        
        # Determine the correct account ID from payload (often found in 'account' dict)
        account = payload.get("account", {})
        account_id = account.get("id", CHATWOOT_ACCOUNT_ID)
        
        logger.info(f"--- WEBHOOK DETAILS ---")
        logger.info(f"Account ID: {account_id}")
        logger.info(f"Message Type: {message_type}")
        logger.info(f"Is Private: {is_private}")
        logger.info(f"Content: {content[:50]}")
        logger.info(f"Sender: {sender_name} (ID: {sender_id})")
        logger.info(f"Conversation ID: {conversation_id}, Labels: {labels}")
        logger.info(f"-----------------------")
        
        # Only process incoming user messages that are not private notes
        if message_type in [0, "incoming"] and not is_private:
            # Clean HTML out of content basic tags before sending to DIFY
            clean_content = content.replace("<p>", "").replace("</p>", "").strip()
            logger.info(f"New Incoming Message from {sender_name}: {clean_content[:50]}...")
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # Log to Sheets
            background_tasks.add_task(append_to_sheet, "Sheet1!A:D", [timestamp, "CHAT", sender_name, clean_content])
            
            # Check if agent is currently handling it
            if "human-needed" in labels:
                logger.info(f"Skipping AI reply because 'human-needed' label is present in Convo {conversation_id}.")
                return {"status": "skipped_human_needed"}
                
            # If no human needed, trigger AI
            background_tasks.add_task(process_ai_reply, account_id, clean_content, sender_id, conversation_id)
        
    return {"status": "received"}

@app.post("/webhook/sos")
async def sos_webhook(sos_data: SosPayload):
    """
    Receives SOS triggers from the Next.js Frontend.
    """
    logger.info(f"🚨 SOS Webhook Triggered by {sos_data.nickname} ({sos_data.phone}) at Room {sos_data.pin}")
    logger.info(f"📍 Location: Lat {sos_data.latitude}, Lng {sos_data.longitude}")
    
    # 1. Save to Google Sheets
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    google_maps_link = f"https://www.google.com/maps/search/?api=1&query={sos_data.latitude},{sos_data.longitude}"
    
    append_to_sheet(
        "Sheet1!A:G", 
        [timestamp, "SOS/CONTACT", sos_data.pin, sos_data.nickname, sos_data.phone, google_maps_link, f"{sos_data.latitude}, {sos_data.longitude}"]
    )
    
    # 2. Trigger Chatwoot internal message (alerting the admin inbox)
    # create_chatwoot_internal_note(sos_data)
    
    return {"status": "sos_logged", "message": "SOS received and processed by backend"}

if __name__ == "__main__":
    logger.info("Starting Local Connect Orchestrator...")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
