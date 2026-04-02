#!/usr/bin/env python3
"""
Second Brain Inbox Watcher
Monitors ~/iCloud Drive/Second Brain Inbox/ for new files dropped from iOS.
Imports them into the second-brain SQLite DB, syncs to Google Drive, then
moves the original to a "processed" subfolder.

Supported file types:
  - .txt, .md → note (content = file text)
  - .url, .webloc → link (extracts URL)
  - .json → structured entry ({type, title, content, url})
  - .pdf, .docx, .doc, .xlsx, .xls, .pptx, .csv → document (stored in attachments/)
  - .png, .jpg, .jpeg, .heic, .gif, .webp, .svg → document (screenshot/image, stored in attachments/)
  - .mp4, .mov, .m4a, .mp3, .wav → document (media, stored in attachments/)
  - .html, .mhtml, .webarchive → document (web article, content extracted if possible)
  - Everything else → document (stored in attachments/)

Runs via LaunchAgent with WatchPaths on the inbox folder.
"""

import os
import sys
import json
import plistlib
import logging
import shutil
import time
from datetime import datetime
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ICLOUD_INBOX = os.path.expanduser(
    "~/Library/Mobile Documents/com~apple~CloudDocs/Second Brain Inbox"
)
LOCAL_INBOX = os.path.join(BASE_DIR, "inbox")
INBOX_DIR = ICLOUD_INBOX  # Will fallback to LOCAL_INBOX if iCloud not accessible
PROCESSED_DIR = None  # Set per-inbox in process_inbox()
ATTACHMENTS_DIR = os.path.join(BASE_DIR, "attachments")
LOG_DIR = os.path.join(BASE_DIR, "logs")

sys.path.insert(0, BASE_DIR)

# ── Logging ───────────────────────────────────────────────────────────
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(ATTACHMENTS_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "inbox_watcher.log")),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("inbox_watcher")

# ── File type categories ──────────────────────────────────────────────
TEXT_EXTENSIONS = {".txt", ".md"}
URL_EXTENSIONS = {".url", ".webloc"}
WEB_EXTENSIONS = {".html", ".mhtml", ".webarchive"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".heic", ".gif", ".webp", ".svg", ".tiff"}
DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".csv", ".rtf", ".pages", ".numbers", ".key"}
MEDIA_EXTENSIONS = {".mp4", ".mov", ".m4a", ".mp3", ".wav", ".aac", ".m4v"}


def _ocr_image(filepath):
    """
    Extract text from an image using macOS Vision framework (native OCR).
    Returns the extracted text or empty string.
    """
    try:
        import Vision
        import Quartz

        img_url = Quartz.CFURLCreateWithFileSystemPath(
            None, str(filepath), Quartz.kCFURLPOSIXPathStyle, False
        )
        img_source = Quartz.CGImageSourceCreateWithURL(img_url, None)
        if not img_source:
            return ""

        cg_image = Quartz.CGImageSourceCreateImageAtIndex(img_source, 0, None)
        if not cg_image:
            return ""

        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(True)

        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(
            cg_image, None
        )
        success = handler.performRequests_error_([request], None)

        if not success[0]:
            return ""

        results = request.results()
        if not results:
            return ""

        lines = []
        for observation in results:
            candidate = observation.topCandidates_(1)
            if candidate:
                lines.append(candidate[0].string())

        extracted = "\n".join(lines)
        if extracted:
            log.info(f"  OCR extracted {len(extracted)} chars from image")
        return extracted

    except Exception as e:
        log.warning(f"  OCR failed for {filepath}: {e}")
        return ""


def store_attachment(filepath):
    """
    Copy a file into the attachments/ directory with a timestamped name.
    Returns the relative path from the second-brain root.
    """
    filename = Path(filepath).name
    base, ext = os.path.splitext(filename)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    stored_name = f"{base}_{ts}{ext}"
    dest = os.path.join(ATTACHMENTS_DIR, stored_name)
    shutil.copy2(filepath, dest)
    return f"attachments/{stored_name}"


def import_txt(filepath):
    """Import a .txt or .md file as a note."""
    title = Path(filepath).stem
    content = Path(filepath).read_text(encoding="utf-8", errors="replace")
    return "note", title, content, ""


def import_url_file(filepath):
    """Import a .webloc (plist) or .url (INI-style) file as a link."""
    title = Path(filepath).stem
    ext = Path(filepath).suffix.lower()

    if ext == ".webloc":
        with open(filepath, "rb") as f:
            plist = plistlib.load(f)
        url = plist.get("URL", "")
    else:
        url = ""
        for line in Path(filepath).read_text(errors="replace").splitlines():
            if line.strip().upper().startswith("URL="):
                url = line.strip().split("=", 1)[1]
                break

    return "link", title, "", url


def import_json(filepath):
    """Import a structured JSON entry: {type, title, content, url}."""
    data = json.loads(Path(filepath).read_text(encoding="utf-8"))
    entry_type = data.get("type", "note")
    if entry_type not in ("note", "link", "document"):
        entry_type = "note"
    title = data.get("title", Path(filepath).stem)
    content = data.get("content", "")
    url = data.get("url", "")
    return entry_type, title, content, url


def import_html(filepath):
    """Import an HTML/web archive file as a document with content extracted."""
    title = Path(filepath).stem
    attachment_path = store_attachment(filepath)

    # Try to extract readable text from HTML
    content = ""
    ext = Path(filepath).suffix.lower()
    if ext == ".html":
        try:
            raw = Path(filepath).read_text(encoding="utf-8", errors="replace")
            # Simple tag stripping for plain text extraction
            import re
            text = re.sub(r"<script[^>]*>.*?</script>", "", raw, flags=re.DOTALL)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            content = text[:5000]
        except Exception:
            pass

    if not content:
        content = f"[Web article saved: {attachment_path}]"

    return "document", title, content, ""


def import_image(filepath):
    """Import a screenshot or image as a document with attachment + OCR text."""
    title = Path(filepath).stem
    attachment_path = store_attachment(filepath)

    # Run OCR to extract text from the image
    ocr_text = _ocr_image(filepath)

    content = f"[Image: {attachment_path}]"
    if ocr_text:
        content += f"\n\n--- Extracted Text ---\n{ocr_text}"
    return "document", title, content, ""


def import_document(filepath):
    """Import a PDF, Office doc, or other document with attachment + extracted text."""
    title = Path(filepath).stem
    attachment_path = store_attachment(filepath)

    # Extract readable text from the document
    from extract_text import extract_text
    extracted = extract_text(filepath)

    content = f"[Document: {attachment_path}]"
    if extracted:
        content += f"\n\n--- Extracted Text ---\n{extracted}"
    return "document", title, content, ""


def import_media(filepath):
    """Import a video or audio file with attachment."""
    title = Path(filepath).stem
    attachment_path = store_attachment(filepath)
    content = f"[Media: {attachment_path}]"
    return "document", title, content, ""


def import_other(filepath):
    """Import any unrecognized file with attachment."""
    title = Path(filepath).name
    attachment_path = store_attachment(filepath)
    content = f"[File: {attachment_path}]"
    return "document", title, content, ""


def get_importer(filepath):
    """Select the right importer based on file extension."""
    ext = Path(filepath).suffix.lower()
    if ext in TEXT_EXTENSIONS:
        return import_txt
    if ext in URL_EXTENSIONS:
        return import_url_file
    if ext == ".json":
        return import_json
    if ext in WEB_EXTENSIONS:
        return import_html
    if ext in IMAGE_EXTENSIONS:
        return import_image
    if ext in DOCUMENT_EXTENSIONS:
        return import_document
    if ext in MEDIA_EXTENSIONS:
        return import_media
    return import_other


def process_file(filepath):
    """Import a single file into the second-brain DB."""
    importer = get_importer(filepath)
    entry_type, title, content, url = importer(filepath)

    from database import init_db, add_entry

    init_db()
    entry_id = add_entry(entry_type, title, content, url)
    log.info(f"Added entry #{entry_id}: [{entry_type}] {title}")

    # Try Google Drive sync (non-fatal if not configured)
    try:
        from drive_sync import sync_entry_to_drive, is_drive_configured, is_drive_authenticated

        if is_drive_configured() and is_drive_authenticated():
            sync_entry_to_drive(entry_id)
            log.info(f"  Synced entry #{entry_id} to Google Drive")
    except Exception as e:
        log.warning(f"  Drive sync skipped: {e}")

    return entry_id


def _scan_folder(inbox_dir):
    """Scan a single inbox folder and process all new files."""
    processed_dir = os.path.join(inbox_dir, "processed")
    os.makedirs(processed_dir, exist_ok=True)

    processed_count = 0

    for filename in sorted(os.listdir(inbox_dir)):
        filepath = os.path.join(inbox_dir, filename)

        # Skip directories, hidden files, and the processed subfolder
        if not os.path.isfile(filepath):
            continue
        if filename.startswith("."):
            continue

        # Brief pause to let iCloud finish syncing the file
        try:
            size1 = os.path.getsize(filepath)
            time.sleep(1)
            size2 = os.path.getsize(filepath)
            if size1 != size2:
                log.info(f"  Skipping {filename} — still syncing from iCloud")
                continue
        except OSError:
            continue

        try:
            process_file(filepath)

            # Move to processed
            dest = os.path.join(processed_dir, filename)
            if os.path.exists(dest):
                base, ext = os.path.splitext(filename)
                dest = os.path.join(
                    processed_dir,
                    f"{base}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}",
                )
            os.rename(filepath, dest)
            log.info(f"  Moved to processed: {filename}")
            processed_count += 1

        except Exception as e:
            log.error(f"  Failed to process {filename}: {e}", exc_info=True)

    return processed_count


def process_inbox():
    """Scan all inbox folders (iCloud + local fallback) and process new files."""
    total = 0

    # Always check local inbox
    os.makedirs(LOCAL_INBOX, exist_ok=True)
    try:
        count = _scan_folder(LOCAL_INBOX)
        total += count
    except Exception as e:
        log.error(f"Local inbox error: {e}")

    # Try iCloud inbox (may fail without Full Disk Access)
    try:
        if os.path.isdir(ICLOUD_INBOX):
            count = _scan_folder(ICLOUD_INBOX)
            total += count
    except PermissionError:
        log.warning("iCloud inbox not accessible — grant Full Disk Access to python3, or use the API endpoint.")
    except Exception as e:
        log.error(f"iCloud inbox error: {e}")

    if total:
        log.info(f"Processed {total} file(s) total.")
    else:
        log.info("No new files in any inbox.")

    return total


if __name__ == "__main__":
    process_inbox()
