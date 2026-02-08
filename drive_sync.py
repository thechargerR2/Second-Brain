import os
from html import escape
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload
from database import get_entry, get_all_entries, update_drive_file_id

BASE_DIR = os.path.dirname(__file__)
CREDENTIALS_PATH = os.path.join(BASE_DIR, "credentials.json")
TOKEN_PATH = os.path.join(BASE_DIR, "token.json")
SCOPES = ["https://www.googleapis.com/auth/drive.file"]
FOLDER_NAME = "Second Brain"


def is_drive_configured():
    return os.path.exists(CREDENTIALS_PATH)


def is_drive_authenticated():
    return os.path.exists(TOKEN_PATH)


def get_drive_service():
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=8090)
        with open(TOKEN_PATH, "w") as token:
            token.write(creds.to_json())
    return build("drive", "v3", credentials=creds)


def get_or_create_folder(service):
    results = service.files().list(
        q=f"name='{FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
        spaces="drive",
        fields="files(id, name)",
    ).execute()
    folders = results.get("files", [])
    if folders:
        return folders[0]["id"]
    folder_metadata = {
        "name": FOLDER_NAME,
        "mimeType": "application/vnd.google-apps.folder",
    }
    folder = service.files().create(body=folder_metadata, fields="id").execute()
    return folder["id"]


def format_entry_as_html(entry):
    entry_type = escape(entry["type"].upper())
    title = escape(entry["title"])
    created = escape(entry["created_at"])
    content = entry["content"] or ""
    url = entry["url"] or ""

    html = f"""<html>
<head><title>[{entry_type}] {title}</title></head>
<body>
<h1>{title}</h1>
<p><strong>Type:</strong> {entry_type} &nbsp; <strong>Date:</strong> {created}</p>
"""
    if url:
        escaped_url = escape(url, quote=True)
        html += f'<p><strong>URL:</strong> <a href="{escaped_url}">{escaped_url}</a></p>\n'
    if content:
        paragraphs = content.split("\n")
        for p in paragraphs:
            if p.strip():
                html += f"<p>{escape(p)}</p>\n"
    html += "</body></html>"
    return html


def create_drive_file(service, entry, folder_id):
    entry_type = entry["type"].upper()
    title = f"[{entry_type}] {entry['title']}"
    html = format_entry_as_html(entry)
    file_metadata = {
        "name": title,
        "parents": [folder_id],
        "mimeType": "application/vnd.google-apps.document",
    }
    media = MediaInMemoryUpload(
        html.encode("utf-8"), mimetype="text/html", resumable=False
    )
    file = service.files().create(
        body=file_metadata, media_body=media, fields="id"
    ).execute()
    return file["id"]


def update_drive_file(service, file_id, entry):
    entry_type = entry["type"].upper()
    title = f"[{entry_type}] {entry['title']}"
    html = format_entry_as_html(entry)
    service.files().update(
        fileId=file_id,
        body={"name": title},
        media_body=MediaInMemoryUpload(
            html.encode("utf-8"), mimetype="text/html", resumable=False
        ),
    ).execute()


def delete_drive_file(service, file_id):
    service.files().update(fileId=file_id, body={"trashed": True}).execute()


def sync_entry_to_drive(entry_id):
    if not is_drive_configured() or not is_drive_authenticated():
        return
    try:
        service = get_drive_service()
        entry = get_entry(entry_id)
        if not entry:
            return
        folder_id = get_or_create_folder(service)
        if entry["drive_file_id"]:
            update_drive_file(service, entry["drive_file_id"], entry)
        else:
            file_id = create_drive_file(service, entry, folder_id)
            update_drive_file_id(entry_id, file_id)
    except Exception as e:
        print(f"Drive sync error for entry {entry_id}: {e}")


def delete_entry_from_drive(entry_id):
    if not is_drive_configured() or not is_drive_authenticated():
        return
    try:
        entry = get_entry(entry_id)
        if not entry or not entry["drive_file_id"]:
            return
        service = get_drive_service()
        delete_drive_file(service, entry["drive_file_id"])
    except Exception as e:
        print(f"Drive delete error for entry {entry_id}: {e}")


def sync_all_entries():
    synced = 0
    errors = 0
    service = get_drive_service()
    folder_id = get_or_create_folder(service)
    entries = get_all_entries()
    for entry in entries:
        try:
            if entry["drive_file_id"]:
                update_drive_file(service, entry["drive_file_id"], entry)
            else:
                file_id = create_drive_file(service, entry, folder_id)
                update_drive_file_id(entry["id"], file_id)
            synced += 1
        except Exception as e:
            print(f"Drive sync error for entry {entry['id']}: {e}")
            errors += 1
    return synced, errors
