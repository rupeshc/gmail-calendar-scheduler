import os
import base64
import json
import re
import requests
from datetime import datetime, timedelta

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


# ==============================
# CONFIG
# ==============================

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar"
]

TIMEZONE = "Asia/Kolkata"
OLLAMA_MODEL = "phi3:mini"


# ==============================
# AUTHENTICATION
# ==============================

def authenticate():
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
# EMAIL FUNCTIONS
# ==============================

def get_unread_emails(service):
    print("Fetching unread emails...")
    results = service.users().messages().list(
        userId="me",
        labelIds=["INBOX"],
        q="is:unread"
    ).execute()

    return results.get("messages", [])


def get_email_body(service, msg_id):
    message = service.users().messages().get(
        userId="me",
        id=msg_id,
        format="full"
    ).execute()

    payload = message.get("payload", {})
    parts = payload.get("parts")

    body = ""

    if parts:
        for part in parts:
            if part["mimeType"] == "text/plain":
                data = part["body"].get("data")
                if data:
                    body = base64.urlsafe_b64decode(data).decode("utf-8")
                    break
    else:
        data = payload["body"].get("data")
        if data:
            body = base64.urlsafe_b64decode(data).decode("utf-8")

    return body


# ==============================
# OLLAMA EXTRACTION
# ==============================

def extract_event_from_email(email_text):

    prompt = f"""
You are a strict calendar event extraction engine.

Extract meeting, interview, appointment, workshop,
conference, class, review session, or scheduled event
from ANY email format.

If NO real event exists, return EXACTLY:

{{
"title": "",
"date": "",
"end_date": "",
"start_time": "",
"end_time": "",
"location": "",
"description": ""
}}

If event exists, return ONLY valid JSON:

{{
"title": "short clear title",
"date": "YYYY-MM-DD",
"end_date": "",
"start_time": "HH:MM",
"end_time": "HH:MM",
"location": "",
"description": "1 sentence summary"
}}

Rules:
- No explanation
- No markdown
- No trailing commas
- Date must be YYYY-MM-DD
- Time must be 24-hour HH:MM

EMAIL:
\"\"\"{email_text}\"\"\"
"""

    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "temperature": 0,
                "top_p": 0.9
            }
        )

        raw_output = response.json().get("response", "").strip()

        # Extract JSON block
        start = raw_output.find("{")
        end = raw_output.rfind("}")

        if start == -1 or end == -1:
            return {}

        json_text = raw_output[start:end+1]

        # Remove trailing commas
        json_text = re.sub(r",\s*}", "}", json_text)
        json_text = re.sub(r",\s*]", "]", json_text)

        data = json.loads(json_text)

        return data

    except Exception:
        return {}


# ==============================
# VALIDATION HELPERS
# ==============================

def sanitize_date(date_string):
    match = re.search(r"\d{4}-\d{2}-\d{2}", str(date_string))
    return match.group(0) if match else None


def is_valid_time(time_string):
    return re.match(r"^\d{2}:\d{2}$", str(time_string))


# ==============================
# CALENDAR CREATION
# ==============================

def create_calendar_event(service, event_data):
    try:
        if not event_data.get("title") or not event_data.get("date"):
            print("⚠ Invalid event data.")
            return False

        raw_date = event_data.get("date")
        date = sanitize_date(raw_date)

        if not date:
            print("⚠ Invalid date format.")
            return False

        start_time = event_data.get("start_time") or "09:00"

        if not is_valid_time(start_time):
            start_time = "09:00"

        end_time = event_data.get("end_time")

        start_dt = datetime.strptime(
            f"{date} {start_time}",
            "%Y-%m-%d %H:%M"
        )

        if not end_time or not is_valid_time(end_time):
            end_dt = start_dt + timedelta(hours=1)
        else:
            end_dt = datetime.strptime(
                f"{date} {end_time}",
                "%Y-%m-%d %H:%M"
            )

        event = {
            "summary": event_data["title"],
            "location": event_data.get("location", ""),
            "description": event_data.get("description", ""),
            "start": {
                "dateTime": start_dt.isoformat(),
                "timeZone": TIMEZONE,
            },
            "end": {
                "dateTime": end_dt.isoformat(),
                "timeZone": TIMEZONE,
            },
        }

        service.events().insert(
            calendarId="primary",
            body=event
        ).execute()

        print("✅ Event created successfully.")
        return True

    except Exception as e:
        print("❌ Calendar error:", e)
        return False


# ==============================
# MARK AS READ
# ==============================

def mark_as_read(service, msg_id):
    try:
        service.users().messages().modify(
            userId="me",
            id=msg_id,
            body={"removeLabelIds": ["UNREAD"]}
        ).execute()

        print("📩 Email marked as read.")
    except Exception as e:
        print("⚠ Failed to mark as read:", e)


# ==============================
# MAIN
# ==============================

def main():
    gmail_service, calendar_service = authenticate()

    messages = get_unread_emails(gmail_service)

    if not messages:
        print("No unread emails.")
        return

    for msg in messages:
        print("\nProcessing email...")

        email_body = get_email_body(gmail_service, msg["id"])

        if not email_body.strip():
            continue

        event_data = extract_event_from_email(email_body)

        print("Extracted Event:", event_data)

        if not event_data or not event_data.get("title"):
            print("⚠ No valid event found.")
            continue

        success = create_calendar_event(calendar_service, event_data)

        if success:
            mark_as_read(gmail_service, msg["id"])


if __name__ == "__main__":
    main()