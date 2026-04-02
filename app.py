from google_integration import google_bp
import os
import sqlite3
import shutil
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from database import init_db, add_entry, get_all_entries, get_entry, delete_entry
from ai_providers import (
    chat_with_claude,
    chat_with_gemini,
    summarize_with_claude,
    summarize_with_gemini,
)
from drive_sync import (
    sync_entry_to_drive,
    delete_entry_from_drive,
    sync_all_entries,
    get_drive_service,
    is_drive_configured,
    is_drive_authenticated,
)

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")
app.register_blueprint(google_bp, url_prefix="/google")

init_db()


@app.context_processor
def inject_drive_status():
    return {
        "drive_configured": is_drive_configured(),
        "drive_authenticated": is_drive_authenticated(),
    }


@app.route("/")
def index():
    search = request.args.get("search", "")
    entries = get_all_entries(search if search else None)
    return render_template("index.html", entries=entries, search=search)


@app.route("/add", methods=["GET", "POST"])
def add():
    if request.method == "POST":
        entry_type = request.form["type"]
        title = request.form["title"]
        content = request.form.get("content", "")
        url = request.form.get("url", "")
        if not title:
            flash("Title is required.", "error")
            return render_template("add.html")
        entry_id = add_entry(entry_type, title, content, url)
        sync_entry_to_drive(entry_id)
        flash("Entry added.", "success")
        return redirect(url_for("index"))
    return render_template("add.html")


@app.route("/delete/<int:entry_id>", methods=["POST"])
def delete(entry_id):
    delete_entry_from_drive(entry_id)
    delete_entry(entry_id)
    flash("Entry deleted.", "success")
    return redirect(url_for("index"))


@app.route("/chat", methods=["GET", "POST"])
def chat():
    answer = None
    question = ""
    provider = "claude"
    if request.method == "POST":
        question = request.form["question"]
        provider = request.form.get("provider", "claude")
        entries = get_all_entries(question)
        if not entries:
            entries = get_all_entries()
        context = "\n\n".join(
            f"[{e['type'].upper()}] {e['title']}\n{e['content']}"
            + (f"\nURL: {e['url']}" if e["url"] else "")
            for e in entries[:10]
        )
        try:
            if provider == "gemini":
                answer = chat_with_gemini(context, question)
            else:
                answer = chat_with_claude(context, question)
        except Exception as e:
            answer = f"Error from {provider}: {e}"
    return render_template(
        "chat.html", answer=answer, question=question, provider=provider
    )


@app.route("/voice", methods=["GET"])
def voice_chat():
    return render_template("voice.html")


def _smart_search(question):
    """Search entries by splitting question into keywords, scoring by match count and recency."""
    import re as _re
    from datetime import datetime
    from database import get_connection

    # Common words to ignore
    stop_words = {
        "what", "does", "the", "say", "about", "tell", "me", "our", "my",
        "is", "are", "was", "were", "do", "did", "have", "has", "a", "an",
        "in", "on", "for", "to", "of", "and", "or", "can", "you", "how",
        "show", "find", "get", "give", "list", "all", "any", "from", "with",
        "this", "that", "it", "its", "we", "i", "summarize", "summary",
        "explain", "describe", "whats", "what's",
    }

    # Extract meaningful keywords
    words = _re.findall(r"[a-zA-Z0-9]+", question.lower())
    keywords = [w for w in words if w not in stop_words and len(w) > 1]

    if not keywords:
        keywords = words[:3]

    conn = get_connection()
    conn.row_factory = sqlite3.Row

    # Search for each keyword and score entries by how many keywords they match
    entry_scores = {}
    entry_data = {}

    for kw in keywords:
        pattern = f"%{kw}%"
        rows = conn.execute(
            """SELECT id, type, title, content, url, topic, created_at
               FROM entries
               WHERE title LIKE ? OR content LIKE ? OR topic LIKE ?
               ORDER BY created_at DESC""",
            (pattern, pattern, pattern),
        ).fetchall()
        for row in rows:
            eid = row["id"]
            if eid not in entry_scores:
                entry_scores[eid] = 0
                entry_data[eid] = row
            entry_scores[eid] += 1
            # Bonus for title match
            if kw.lower() in row["title"].lower():
                entry_scores[eid] += 2

    # Add recency bonus — newer entries score higher
    now = datetime.utcnow()
    for eid, row in entry_data.items():
        try:
            created = datetime.strptime(row["created_at"][:19], "%Y-%m-%d %H:%M:%S")
            age_days = (now - created).days
            if age_days <= 7:
                entry_scores[eid] += 3
            elif age_days <= 30:
                entry_scores[eid] += 2
            elif age_days <= 90:
                entry_scores[eid] += 1
        except (ValueError, TypeError):
            pass

    conn.close()

    # Sort by score descending, return top 25
    ranked = sorted(entry_scores.keys(), key=lambda eid: entry_scores[eid], reverse=True)
    return [entry_data[eid] for eid in ranked[:25]]


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """JSON API for voice chat — accepts {question, provider} returns {answer}."""
    data = request.get_json()
    question = data.get("question", "").strip()
    provider = data.get("provider", "claude")

    if not question:
        return jsonify({"error": "question is required"}), 400

    entries = _smart_search(question)
    if not entries:
        entries = get_all_entries()

    # Build context — give top results full content, cap the rest
    context_parts = []
    total_len = 0
    for i, e in enumerate(entries[:20]):
        content = e['content']
        # Top 5 results get full content (up to 10000 chars), rest get 3000
        max_chars = 10000 if i < 5 else 3000
        if len(content) > max_chars:
            content = content[:max_chars] + "\n[... truncated ...]"
        part = (
            f"[{e['type'].upper()}] {e['title']}"
            + (f" (Topic: {e['topic']})" if e["topic"] else "")
            + f"\n{content}"
            + (f"\nURL: {e['url']}" if e["url"] else "")
        )
        if total_len + len(part) > 60000:
            break
        context_parts.append(part)
        total_len += len(part)
    context = "\n\n".join(context_parts)
    try:
        if provider == "gemini":
            answer = chat_with_gemini(context, question)
        else:
            answer = chat_with_claude(context, question)
        return jsonify({"answer": answer})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/search", methods=["GET"])
def search():
    """Flexible search endpoint — returns matching entries as JSON.

    Query params:
        q       — keywords or topic to search for (required)
        limit   — max results to return (default 25)
    """
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "q parameter is required"}), 400

    limit = min(int(request.args.get("limit", 25)), 100)
    results = _smart_search(query)[:limit]

    return jsonify({
        "query": query,
        "count": len(results),
        "results": [
            {
                "id": r["id"],
                "title": r["title"],
                "content": r["content"],
                "tags": r["topic"] or "",
            }
            for r in results
        ],
    })


@app.route("/siri", methods=["GET"])
def siri_setup():
    return render_template("siri_setup.html")


@app.route("/summarize/<int:entry_id>", methods=["POST"])
def summarize(entry_id):
    entry = get_entry(entry_id)
    if not entry:
        flash("Entry not found.", "error")
        return redirect(url_for("index"))
    provider = request.form.get("provider", "claude")
    text = f"{entry['title']}\n\n{entry['content']}"
    if entry["url"]:
        text += f"\n\nURL: {entry['url']}"
    try:
        if provider == "gemini":
            summary = summarize_with_gemini(text)
        else:
            summary = summarize_with_claude(text)
        flash(f"Summary ({provider}): {summary}", "info")
    except Exception as e:
        flash(f"Error from {provider}: {e}", "error")
    return redirect(url_for("index"))


@app.route("/sync", methods=["POST"])
def sync():
    try:
        synced, errors = sync_all_entries()
        flash(f"Synced {synced} entries to Google Drive. {errors} errors.", "success" if errors == 0 else "info")
    except Exception as e:
        flash(f"Drive sync failed: {e}", "error")
    return redirect(url_for("index"))


@app.route("/drive-setup", methods=["POST"])
def drive_setup():
    try:
        get_drive_service()
        flash("Google Drive connected.", "success")
    except Exception as e:
        flash(f"Drive setup failed: {e}", "error")
    return redirect(url_for("index"))


# ── API Endpoint for iOS Shortcuts ────────────────────────────────────
ATTACHMENTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "attachments")
os.makedirs(ATTACHMENTS_DIR, exist_ok=True)

API_TOKEN = os.environ.get("SECOND_BRAIN_API_TOKEN", "")


def _check_api_token():
    """Validate bearer token if one is configured."""
    if not API_TOKEN:
        return True  # No token configured = local-only use
    auth = request.headers.get("Authorization", "")
    return auth == f"Bearer {API_TOKEN}"


@app.route("/api/add", methods=["POST"])
def api_add():
    """
    API endpoint for iOS Shortcuts to drop items into Second Brain.

    Accepts multipart/form-data or JSON:
      - title (required): entry title
      - type: note | link | document (default: note)
      - content: text content
      - url: URL for links
      - file: uploaded file (images, PDFs, docs, media, etc.)

    Returns JSON: {id, type, title, status}
    """
    if not _check_api_token():
        return jsonify({"error": "Unauthorized"}), 401

    # Handle both JSON and form data
    if request.is_json:
        data = request.get_json()
        title = data.get("title", "")
        entry_type = data.get("type", "note")
        content = data.get("content", "")
        url = data.get("url", "")
        uploaded_file = None
    else:
        title = request.form.get("title", "")
        entry_type = request.form.get("type", "note")
        content = request.form.get("content", "")
        url = request.form.get("url", "")
        uploaded_file = request.files.get("file")

    # Auto-detect type from URL
    if url and not title:
        title = url[:100]
        entry_type = "link"

    if not title and uploaded_file:
        title = uploaded_file.filename or "Untitled"
        entry_type = "document"

    if not title:
        return jsonify({"error": "title is required"}), 400

    if entry_type not in ("note", "link", "document"):
        entry_type = "note"

    # Handle file attachment
    if uploaded_file and uploaded_file.filename:
        filename = secure_filename(uploaded_file.filename)
        base, ext = os.path.splitext(filename)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        stored_name = f"{base}_{ts}{ext}"
        dest = os.path.join(ATTACHMENTS_DIR, stored_name)
        uploaded_file.save(dest)
        attachment_ref = f"attachments/{stored_name}"

        ext_lower = ext.lower()
        if ext_lower in {".png", ".jpg", ".jpeg", ".heic", ".gif", ".webp", ".svg", ".tiff"}:
            # OCR images/screenshots for searchable text
            try:
                from gmail_inbox_importer import ocr_image
                ocr_text = ocr_image(dest)
            except Exception:
                ocr_text = ""
            content = f"[Image: {attachment_ref}]"
            if ocr_text:
                content += f"\n\n--- Extracted Text ---\n{ocr_text}"
            if request.form.get("content", "").strip():
                content += f"\n\n{request.form.get('content', '').strip()}"
        elif ext_lower in {".mp4", ".mov", ".m4a", ".mp3", ".wav"}:
            content = f"[Media: {attachment_ref}]" + (f"\n\n{content}" if content else "")
        else:
            # Extract text from PDFs, DOCX, XLSX, etc.
            from extract_text import extract_text
            extracted = extract_text(dest)
            content = f"[Document: {attachment_ref}]"
            if extracted:
                content += f"\n\n--- Extracted Text ---\n{extracted}"
            if request.form.get("content", "").strip():
                content += f"\n\n{request.form.get('content', '').strip()}"
        entry_type = "document"

    entry_id = add_entry(entry_type, title, content, url)

    # Try Drive sync
    try:
        sync_entry_to_drive(entry_id)
    except Exception:
        pass

    return jsonify({"id": entry_id, "type": entry_type, "title": title, "status": "ok"}), 201


# ── Action Items API ─────────────────────────────────────────────────

@app.route("/api/actions/open", methods=["GET"])
def open_actions():
    """Return all unresolved action items."""
    from database import get_connection
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, title, content, action_source, created_at FROM entries "
        "WHERE topic='action_required' AND action_status='open' ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/actions/<int:entry_id>/resolve", methods=["POST"])
def resolve_action(entry_id):
    """Mark an action item as acted_on or dismissed."""
    data = request.get_json() or {}
    status = data.get("status", "acted_on")
    if status not in ("acted_on", "dismissed"):
        return jsonify({"error": "status must be acted_on or dismissed"}), 400
    from database import get_connection
    conn = get_connection()
    conn.execute("UPDATE entries SET action_status=? WHERE id=?", (status, entry_id))
    conn.commit()
    conn.close()
    return jsonify({"id": entry_id, "status": status})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5001)
