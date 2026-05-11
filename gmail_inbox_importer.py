#!/usr/bin/env python3
"""
Second Brain Gmail Importer
Polls thechargerr2@gmail.com via IMAP for emails with "SB" in the subject,
imports their body text, URLs, and attachments into the second-brain DB,
then archives the email.

Usage:
    python3 gmail_inbox_importer.py              # Run once
    python3 gmail_inbox_importer.py --dry-run    # Preview without importing

How to use from iPhone/iPad:
    Email thechargerr2@gmail.com with "SB" in the subject line.
    Examples:
        Subject: SB Meeting notes from today
        Subject: SB Interesting article
        Subject: SB Screenshot from project

    The body becomes the note content, any URLs become link entries,
    and attachments (images, PDFs, docs, etc.) are saved to attachments/.
"""

import os
import sys
import json
import imaplib
import email
import email.policy
import logging
import re
from datetime import datetime
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ATTACHMENTS_DIR = os.path.join(BASE_DIR, "attachments")
LOG_DIR = os.path.join(BASE_DIR, "logs")
PROCESSED_IDS_FILE = os.path.join(BASE_DIR, ".gmail_processed_ids.json")

sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.expanduser("~/stock_screener"))

# ── Logging ───────────────────────────────────────────────────────────
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(ATTACHMENTS_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "gmail_importer.log")),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("gmail_importer")

# ── Email credentials from stock screener config ──────────────────────
sys.path.insert(0, os.path.expanduser("~/stock_screener"))
from config import EMAIL_CONFIG

IMAP_SERVER = "imap.gmail.com"
IMAP_PORT = 993
EMAIL_ADDR = EMAIL_CONFIG["sender_email"]
EMAIL_PASS = EMAIL_CONFIG["sender_password"]

# Allowed senders (only process emails from your own addresses)
ALLOWED_SENDERS = {
    "thechargerr2@gmail.com",
    "ronwrusso@gmail.com",
    "ron@broadburch.com",
    "rrusso@cinq.care",
}

# File extensions for categorization
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".heic", ".gif", ".webp", ".svg", ".tiff", ".bmp"}
MEDIA_EXTENSIONS = {".mp4", ".mov", ".m4a", ".mp3", ".wav", ".aac"}


# ── Deduplication ──────────────────────────────────────────────────────

def load_processed_ids() -> set:
    """Load set of already-processed email Message-IDs."""
    if os.path.exists(PROCESSED_IDS_FILE):
        try:
            with open(PROCESSED_IDS_FILE, "r") as f:
                return set(json.load(f))
        except (json.JSONDecodeError, IOError):
            pass
    return set()


def save_processed_ids(ids: set):
    """Persist processed Message-IDs. Keeps last 5000 to prevent unbounded growth."""
    trimmed = sorted(ids)[-5000:]  # keep most recent by sort order
    with open(PROCESSED_IDS_FILE, "w") as f:
        json.dump(trimmed, f)


def extract_urls(text):
    """Extract URLs from text, filtering out common non-article URLs."""
    url_pattern = r'https?://[^\s<>"\')\]}]+'
    raw_urls = list(set(re.findall(url_pattern, text)))
    # Filter out tracking pixels, unsubscribe links, email infrastructure
    skip_patterns = [
        r"unsubscribe", r"tracking", r"click\.", r"mailchimp",
        r"list-manage", r"googleusercontent\.com/proxy",
        r"google\.com/maps", r"fonts\.googleapis",
    ]
    filtered = []
    for url in raw_urls:
        if any(re.search(p, url, re.IGNORECASE) for p in skip_patterns):
            continue
        filtered.append(url)
    return filtered


def fetch_article_content(url, timeout=15):
    """
    Fetch a URL and extract the main article text using readability.
    Returns (page_title, article_text) or (None, None) on failure.
    """
    import urllib.request
    import ssl

    try:
        # Some sites block default urllib user-agent
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
        })
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            # Only process HTML pages
            content_type = resp.headers.get("Content-Type", "")
            if "html" not in content_type.lower():
                log.info(f"    Skipping non-HTML URL: {url}")
                return None, None

            raw_html = resp.read(2_000_000)  # Cap at 2MB
            charset = resp.headers.get_content_charset() or "utf-8"
            html = raw_html.decode(charset, errors="replace")

        # Use readability to extract main content
        from readability import Document
        doc = Document(html)
        page_title = doc.short_title() or ""

        # Get the cleaned HTML content, then strip tags for plain text
        article_html = doc.summary()
        article_text = strip_html(article_html)

        # Sanity check: skip if too short (probably a paywall or login page)
        if len(article_text) < 100:
            log.info(f"    Article too short ({len(article_text)} chars), may be paywalled: {url}")
            return page_title, None

        log.info(f"    Fetched article: \"{page_title}\" ({len(article_text)} chars)")
        return page_title, article_text

    except Exception as e:
        log.warning(f"    Failed to fetch article from {url}: {e}")
        return None, None


def ocr_image(filepath):
    """
    Extract text from an image using macOS Vision framework (native OCR).
    Returns the extracted text or empty string.
    """
    try:
        import Vision
        import Quartz

        # Load image via Quartz
        img_url = Quartz.CFURLCreateWithFileSystemPath(
            None, filepath, Quartz.kCFURLPOSIXPathStyle, False
        )
        img_source = Quartz.CGImageSourceCreateWithURL(img_url, None)
        if not img_source:
            log.warning(f"    Could not load image for OCR: {filepath}")
            return ""

        cg_image = Quartz.CGImageSourceCreateImageAtIndex(img_source, 0, None)
        if not cg_image:
            log.warning(f"    Could not create CGImage for OCR: {filepath}")
            return ""

        # Create and run text recognition request
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(True)

        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(
            cg_image, None
        )
        success = handler.performRequests_error_([request], None)

        if not success[0]:
            log.warning(f"    OCR request failed for: {filepath}")
            return ""

        # Extract recognized text
        results = request.results()
        if not results:
            return ""

        lines = []
        for observation in results:
            candidate = observation.topCandidates_(1)
            if candidate:
                lines.append(candidate[0].string())

        extracted = "\n".join(lines)
        log.info(f"    OCR extracted {len(extracted)} chars from image")
        return extracted

    except Exception as e:
        log.warning(f"    OCR failed for {filepath}: {e}")
        return ""


def get_body_text(msg):
    """Extract plain text body from email message."""
    body = ""

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))

            # Skip attachments
            if "attachment" in disposition:
                continue

            if content_type == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    body += payload.decode(charset, errors="replace")
            elif content_type == "text/html" and not body:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    html = payload.decode(charset, errors="replace")
                    body = strip_html(html)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            if msg.get_content_type() == "text/html":
                body = strip_html(text)
            else:
                body = text

    return body.strip()


def strip_html(html):
    """Simple HTML tag stripping."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<p[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def save_attachment(part):
    """Save an email attachment to the attachments directory.
    Returns (original_filename, relative_path)."""
    filename = part.get_filename() or "attachment"
    payload = part.get_payload(decode=True)
    if not payload:
        return None, None

    base, ext = os.path.splitext(filename)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    stored_name = f"{base}_{ts}{ext}"
    dest = os.path.join(ATTACHMENTS_DIR, stored_name)

    with open(dest, "wb") as f:
        f.write(payload)

    return filename, f"attachments/{stored_name}"


def get_attachments(msg):
    """Extract and save all attachments from email. Returns list of (filename, path)."""
    attachments = []

    if not msg.is_multipart():
        return attachments

    for part in msg.walk():
        disposition = str(part.get("Content-Disposition", ""))
        content_type = part.get_content_type()

        # Check for attachments (explicit or inline images)
        is_attachment = "attachment" in disposition
        is_inline_image = (
            "inline" in disposition
            and content_type.startswith("image/")
            and part.get_filename()
        )

        if is_attachment or is_inline_image:
            orig_name, local_path = save_attachment(part)
            if local_path:
                attachments.append((orig_name, local_path))

    return attachments


def process_email(msg, dry_run=False):
    """Process a single email message and import to second-brain DB."""
    from database import init_db, add_entry

    subject = msg.get("Subject", "")
    sender = msg.get("From", "")
    date = msg.get("Date", "")

    # Extract sender email address
    sender_match = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", sender)
    sender_email = sender_match.group(0).lower() if sender_match else ""

    if sender_email not in ALLOWED_SENDERS:
        log.info(f"  Skipping email from {sender_email} (not in allowed senders)")
        return False

    # Strip SB prefix and Re:/Fwd: from subject to get title
    title = re.sub(r"^(?:Re:\s*)*(?:Fwd?:\s*)*SB:?\s*", "", subject, flags=re.IGNORECASE).strip()
    if not title:
        title = f"Import {datetime.now().strftime('%Y-%m-%d %H:%M')}"

    log.info(f'Processing: "{title}" from {sender_email} ({date})')

    if dry_run:
        log.info(f"  [DRY RUN] Would import: {title}")
        return True

    init_db()

    # Extract body text
    content = get_body_text(msg)

    # Extract URLs from body
    urls = extract_urls(content)

    # Download attachments
    attachments = get_attachments(msg)

    entries_created = []

    # If there are attachments, create a document entry for each
    if attachments:
        for orig_name, local_path in attachments:
            ext = os.path.splitext(orig_name)[1].lower()
            att_title = f"{title} — {orig_name}" if len(attachments) > 1 else title

            if ext in IMAGE_EXTENSIONS:
                # Run OCR on screenshots/images to extract text
                full_path = os.path.join(BASE_DIR, local_path)
                ocr_text = ocr_image(full_path)
                att_content = f"[Image: {local_path}]"
                if ocr_text:
                    att_content += f"\n\n--- Extracted Text ---\n{ocr_text}"
                if content:
                    att_content += f"\n\n--- Email Notes ---\n{content}"
            elif ext in MEDIA_EXTENSIONS:
                att_content = f"[Media: {local_path}]"
                if content:
                    att_content += f"\n\n{content}"
            else:
                # Extract text from PDFs, DOCX, XLSX, etc.
                from extract_text import extract_text
                full_path = os.path.join(BASE_DIR, local_path)
                extracted = extract_text(full_path)
                att_content = f"[Document: {local_path}]"
                if extracted:
                    att_content += f"\n\n--- Extracted Text ---\n{extracted}"
                if content:
                    att_content += f"\n\n--- Email Notes ---\n{content}"

            entry_id = add_entry("document", att_title, att_content, "")
            entries_created.append(entry_id)
            log.info(f"  Added document #{entry_id}: {att_title}")

    # If body has URLs, fetch article content and create entries
    if urls and not attachments:
        for url in urls[:5]:
            page_title, article_text = fetch_article_content(url)

            # Use the fetched page title if our email title is generic
            entry_title = title
            if page_title and (not title or title == url[:100]):
                entry_title = page_title
            elif page_title and len(urls) > 1:
                entry_title = f"{title} — {page_title}"

            # Build rich content: article body + any email notes
            parts = []
            if article_text:
                parts.append(article_text)
            # Add email body as notes (but strip the URL itself)
            email_notes = content
            for u in urls:
                email_notes = email_notes.replace(u, "").strip()
            if email_notes:
                parts.append(f"--- Notes ---\n{email_notes}")
            if not parts:
                parts.append(f"[Could not fetch article content from {url}]")

            full_content = "\n\n".join(parts)
            entry_id = add_entry("link", entry_title, full_content, url)
            entries_created.append(entry_id)
            log.info(f"  Added link #{entry_id}: {entry_title} ({len(full_content)} chars)")

    # If no attachments and no URLs, create a note from the body
    elif not attachments and not urls and content:
        entry_id = add_entry("note", title, content, "")
        entries_created.append(entry_id)
        log.info(f"  Added note #{entry_id}: {title}")

    else:
        log.warning(f"  Email had no content or attachments — skipping")
        return False

    # Try Google Drive sync
    try:
        from drive_sync import sync_entry_to_drive, is_drive_configured, is_drive_authenticated

        if is_drive_configured() and is_drive_authenticated():
            for eid in entries_created:
                sync_entry_to_drive(eid)
            log.info(f"  Synced {len(entries_created)} entry(s) to Google Drive")
    except Exception as e:
        log.warning(f"  Drive sync skipped: {e}")

    return True


def poll_gmail(dry_run=False):
    """Connect to Gmail via IMAP, find SB emails, process and archive them."""
    log.info("Connecting to Gmail IMAP...")

    mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    mail.login(EMAIL_ADDR, EMAIL_PASS)
    mail.select("INBOX")

    # Search for emails with SB in the subject
    status, data = mail.search(None, '(SUBJECT "SB")')
    if status != "OK" or not data[0]:
        log.info("No SB emails found in inbox.")
        mail.logout()
        return 0

    msg_ids = data[0].split()
    log.info(f"Found {len(msg_ids)} email(s) with SB in subject.")

    # Load already-processed Message-IDs to prevent duplicates
    processed_ids = load_processed_ids()

    processed = 0
    for msg_id in msg_ids:
        try:
            # Fetch the email
            status, msg_data = mail.fetch(msg_id, "(RFC822)")
            if status != "OK":
                continue

            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email, policy=email.policy.default)

            subject = msg.get("Subject", "")

            # Verify subject actually starts with SB (not just contains it)
            clean_subject = re.sub(r"^(?:Re:\s*)*(?:Fwd?:\s*)*", "", subject, flags=re.IGNORECASE).strip()
            if not clean_subject.upper().startswith("SB"):
                log.info(f'  Skipping "{subject}" — SB not at start of subject')
                continue

            # Dedup: skip if this Message-ID was already processed
            message_id = msg.get("Message-ID", "")
            if message_id and message_id in processed_ids:
                log.info(f'  Skipping "{subject}" — already processed (Message-ID: {message_id})')
                # Still try to archive it since it's stuck in inbox
                if not dry_run:
                    mail.store(msg_id, "+FLAGS", "\\Seen")
                    mail.store(msg_id, "-FLAGS", "\\Flagged")
                    mail.copy(msg_id, '"[Gmail]/All Mail"')
                    mail.store(msg_id, "+FLAGS", "\\Deleted")
                continue

            if process_email(msg, dry_run=dry_run):
                if not dry_run:
                    # Archive: mark as read and remove from inbox
                    mail.store(msg_id, "+FLAGS", "\\Seen")
                    mail.store(msg_id, "-FLAGS", "\\Flagged")
                    mail.copy(msg_id, '"[Gmail]/All Mail"')
                    mail.store(msg_id, "+FLAGS", "\\Deleted")
                    # Record this Message-ID as processed
                    if message_id:
                        processed_ids.add(message_id)
                processed += 1

        except Exception as e:
            log.error(f"  Failed to process message {msg_id}: {e}", exc_info=True)

    # Expunge deleted messages
    if not dry_run and processed > 0:
        mail.expunge()

    # Save updated processed IDs
    if not dry_run:
        save_processed_ids(processed_ids)

    mail.logout()

    action = "Would process" if dry_run else "Processed"
    log.info(f"{action} {processed} email(s).")
    return processed


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Import SB emails from thechargerr2@gmail.com into Second Brain"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without importing")
    args = parser.parse_args()

    try:
        poll_gmail(dry_run=args.dry_run)
    except Exception as e:
        log.error(f"Gmail import failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
