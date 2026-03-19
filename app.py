import os
import base64
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Google APIs
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# Gemini
from google import genai

# ==============================
# CONFIGURATION
# ==============================

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar"
]

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    raise Exception("Gemini API key not found in .env")

genai_client = genai.Client(api_key=GEMINI_API_KEY)

# ==============================
# GOOGLE AUTH
# ==============================

def authenticate_google():
    creds = None

    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json", SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open("token.json", "w") as token:
            token.write(creds.to_json())

    gmail_service = build("gmail", "v1", credentials=creds)
    calendar_service = build("calendar", "v3", credentials=creds)

    return gmail_service, calendar_service


# ==============================
# GET EMAIL TEXT
# ==============================

def get_email_body(message):
    payload = message["payload"]

    if "parts" in payload:
        for part in payload["parts"]:
            if part["mimeType"] == "text/plain":
                data = part["body"]["data"]
                return base64.urlsafe_b64decode(data).decode("utf-8")
    else:
        if payload["mimeType"] == "text/plain":
            data = payload["body"]["data"]
            return base64.urlsafe_b64decode(data).decode("utf-8")

    return ""


# ==============================
# GEMINI EVENT EXTRACTION
# ==============================

def extract_event_with_gemini(email_text):
    today = datetime.now().strftime("%Y-%m-%d")

    prompt = f"""
Today’s date is {today}.

If the email contains:
- tomorrow
- next Wednesday
- upcoming Friday
- this Monday
Convert them into exact date in YYYY-MM-DD format.

If time range like:
- from 3 PM to 5 PM
Extract start_time and end_time.

If duration like:
- 2 hour meeting
Assume start time mentioned and calculate end time.

Return ONLY valid JSON.
No markdown.
If no event found return {{}}.

Format:
{{
  "title": "",
  "date": "YYYY-MM-DD",
  "start_time": "HH:MM",
  "end_time": "HH:MM",
  "location": "",
  "description": ""
}}

Email:
{email_text}
"""

    response = genai_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )

    raw_text = response.text.strip()

    # Remove markdown if Gemini adds it
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        raw_text = raw_text.replace("json", "").strip()

    try:
        return json.loads(raw_text)
    except:
        return {}


# ==============================
# CREATE CALENDAR EVENT
# ==============================

def create_calendar_event(calendar_service, event_data):

    if not event_data or "date" not in event_data:
        print("No event detected.")
        return

    try:
        date = event_data["date"]
        start_time = event_data.get("start_time", "09:00")
        end_time = event_data.get("end_time")

        # If end_time not provided → default 1 hour
        if not end_time:
            start_dt = datetime.strptime(f"{date} {start_time}", "%Y-%m-%d %H:%M")
            end_dt = start_dt + timedelta(hours=1)
            end_time = end_dt.strftime("%H:%M")

        start_datetime = f"{date}T{start_time}:00"
        end_datetime = f"{date}T{end_time}:00"

        event = {
            "summary": event_data.get("title", "New Event"),
            "location": event_data.get("location", ""),
            "description": event_data.get("description", ""),
            "start": {
                "dateTime": start_datetime,
                "timeZone": "Asia/Kolkata",
            },
            "end": {
                "dateTime": end_datetime,
                "timeZone": "Asia/Kolkata",
            },
        }

        created_event = calendar_service.events().insert(
            calendarId="primary",
            body=event
        ).execute()

        print("✅ Event created:", created_event.get("htmlLink"))

    except Exception as e:
        print("❌ Error creating event:", e)


# ==============================
# MAIN FUNCTION
# ==============================

def main():
    gmail_service, calendar_service = authenticate_google()

    print("Fetching unread emails...\n")

    results = gmail_service.users().messages().list(
        userId="me",
        q="is:unread",
        maxResults=20
    ).execute()

    messages = results.get("messages", [])

    if not messages:
        print("No unread emails found.")
        return

    for msg in messages:
        message = gmail_service.users().messages().get(
            userId="me",
            id=msg["id"]
        ).execute()

        email_text = get_email_body(message)

        if not email_text.strip():
            continue

        print("\nProcessing email...\n")

        event_data = extract_event_with_gemini(email_text)

        print("Extracted Event:", event_data)

        create_calendar_event(calendar_service, event_data)


if __name__ == "__main__":
    main()