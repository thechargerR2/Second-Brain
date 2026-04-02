"""
Google Integration Blueprint for Second Brain
"""
import os
import json
import base64
from datetime import datetime, timedelta
from pathlib import Path
from email.mime.text import MIMEText
from flask import Blueprint, request, jsonify, redirect, session
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

GOOGLE_DIR = Path(__file__).parent
CREDENTIALS_FILE = GOOGLE_DIR / "credentials.json"
TOKEN_FILE = GOOGLE_DIR / "google_token.json"
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/drive.file",
]
REDIRECT_URI = "http://localhost:5001/google/callback"
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
google_bp = Blueprint("google", __name__)

def _load_creds():
    if not TOKEN_FILE.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_creds(creds)
        except Exception:
            return None
    return creds if creds and creds.valid else None

def _save_creds(creds):
    TOKEN_FILE.write_text(creds.to_json())

def _get_gmail():
    creds = _load_creds()
    return build("gmail", "v1", credentials=creds) if creds else None

def _get_calendar():
    creds = _load_creds()
    return build("calendar", "v3", credentials=creds) if creds else None

def _require_auth(func):
    from functools import wraps
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not _load_creds():
            return jsonify({"error": "Not authenticated. Visit /google/auth first."}), 401
        return func(*args, **kwargs)
    return wrapper

@google_bp.route("/auth")
def auth():
    if not CREDENTIALS_FILE.exists():
        return jsonify({"error": "Missing credentials.json"}), 500
    flow = Flow.from_client_secrets_file(str(CREDENTIALS_FILE), scopes=SCOPES, redirect_uri=REDIRECT_URI)
    auth_url, state = flow.authorization_url(access_type="offline", include_granted_scopes="true", prompt="consent")
    return redirect(auth_url)

@google_bp.route("/callback")
def callback():
    flow = Flow.from_client_secrets_file(str(CREDENTIALS_FILE), scopes=SCOPES, redirect_uri=REDIRECT_URI)
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    _save_creds(creds)
    return jsonify({"status": "authenticated", "message": "Google API connected with Gmail and Calendar!"})

@google_bp.route("/status")
def status():
    creds = _load_creds()
    if creds:
        return jsonify({"authenticated": True, "scopes": list(creds.scopes) if creds.scopes else []})
    return jsonify({"authenticated": False})

@google_bp.route("/gmail/search")
@_require_auth
def gmail_search():
    query = request.args.get("q", "")
    max_results = min(int(request.args.get("max", 10)), 50)
    gmail = _get_gmail()
    if not gmail:
        return jsonify({"error": "Gmail not available"}), 500
    try:
        results = gmail.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
        messages = results.get("messages", [])
        emails = []
        for msg in messages:
            detail = gmail.users().messages().get(userId="me", id=msg["id"], format="metadata", metadataHeaders=["From", "To", "Subject", "Date"]).execute()
            headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
            emails.append({"id": msg["id"], "thread_id": detail.get("threadId"), "from": headers.get("From", ""), "to": headers.get("To", ""), "subject": headers.get("Subject", ""), "date": headers.get("Date", ""), "snippet": detail.get("snippet", ""), "labels": detail.get("labelIds", [])})
        return jsonify({"query": query, "count": len(emails), "emails": emails})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@google_bp.route("/gmail/read/<message_id>")
@_require_auth
def gmail_read(message_id):
    gmail = _get_gmail()
    if not gmail:
        return jsonify({"error": "Gmail not available"}), 500
    try:
        msg = gmail.users().messages().get(userId="me", id=message_id, format="full").execute()
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        body = _extract_body(msg.get("payload", {}))
        return jsonify({"id": message_id, "from": headers.get("From", ""), "to": headers.get("To", ""), "subject": headers.get("Subject", ""), "date": headers.get("Date", ""), "body": body})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@google_bp.route("/gmail/send", methods=["POST"])
@_require_auth
def gmail_send():
    data = request.get_json(silent=True) or {}
    to = data.get("to", "").strip()
    subject = data.get("subject", "").strip()
    body_text = data.get("body", "").strip()
    if not to or not subject:
        return jsonify({"error": "Missing to and/or subject"}), 400
    gmail = _get_gmail()
    if not gmail:
        return jsonify({"error": "Gmail not available"}), 500
    try:
        message = MIMEText(body_text)
        message["to"] = to
        message["subject"] = subject
        if data.get("cc"):
            message["cc"] = data["cc"]
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        result = gmail.users().messages().send(userId="me", body={"raw": raw}).execute()
        return jsonify({"status": "sent", "message_id": result.get("id")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@google_bp.route("/calendar/events")
@_require_auth
def calendar_events():
    days = int(request.args.get("days", 7))
    max_results = int(request.args.get("max", 20))
    cal = _get_calendar()
    if not cal:
        return jsonify({"error": "Calendar not available"}), 500
    try:
        now = datetime.utcnow()
        time_min = now.isoformat() + "Z"
        time_max = (now + timedelta(days=days)).isoformat() + "Z"
        result = cal.events().list(calendarId="primary", timeMin=time_min, timeMax=time_max, maxResults=max_results, singleEvents=True, orderBy="startTime").execute()
        events = []
        for ev in result.get("items", []):
            start = ev.get("start", {}).get("dateTime", ev.get("start", {}).get("date", ""))
            end = ev.get("end", {}).get("dateTime", ev.get("end", {}).get("date", ""))
            events.append({"id": ev.get("id"), "summary": ev.get("summary", "(No title)"), "start": start, "end": end, "location": ev.get("location", ""), "description": ev.get("description", ""), "attendees": [a.get("email") for a in ev.get("attendees", [])]})
        return jsonify({"days": days, "count": len(events), "events": events})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@google_bp.route("/calendar/create", methods=["POST"])
@_require_auth
def calendar_create():
    data = request.get_json(silent=True) or {}
    summary = data.get("summary", "").strip()
    start = data.get("start", "").strip()
    end = data.get("end", "").strip()
    if not summary or not start or not end:
        return jsonify({"error": "Missing summary, start, or end"}), 400
    cal = _get_calendar()
    if not cal:
        return jsonify({"error": "Calendar not available"}), 500
    try:
        event_body = {"summary": summary, "start": {"dateTime": start, "timeZone": "America/New_York"}, "end": {"dateTime": end, "timeZone": "America/New_York"}}
        if data.get("description"):
            event_body["description"] = data["description"]
        if data.get("location"):
            event_body["location"] = data["location"]
        if data.get("attendees"):
            event_body["attendees"] = [{"email": e} for e in data["attendees"]]
        result = cal.events().insert(calendarId="primary", body=event_body).execute()
        return jsonify({"status": "created", "event_id": result.get("id"), "link": result.get("htmlLink")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@google_bp.route("/calendar/update/<event_id>", methods=["PUT"])
@_require_auth
def calendar_update(event_id):
    data = request.get_json(silent=True) or {}
    cal = _get_calendar()
    if not cal:
        return jsonify({"error": "Calendar not available"}), 500
    try:
        existing = cal.events().get(calendarId="primary", eventId=event_id).execute()
        for key in ["summary", "description", "location"]:
            if key in data:
                existing[key] = data[key]
        if "start" in data:
            existing["start"] = {"dateTime": data["start"], "timeZone": "America/New_York"}
        if "end" in data:
            existing["end"] = {"dateTime": data["end"], "timeZone": "America/New_York"}
        if "attendees" in data:
            existing["attendees"] = [{"email": e} for e in data["attendees"]]
        result = cal.events().update(calendarId="primary", eventId=event_id, body=existing).execute()
        return jsonify({"status": "updated", "event_id": result.get("id")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def _extract_body(payload):
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
        if part.get("parts"):
            result = _extract_body(part)
            if result:
                return result
    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
    return ""
