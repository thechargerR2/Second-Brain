"""
Second Brain MCP Server
Exposes the Second Brain SQLite database to Claude via MCP protocol.
Supports search, browse, topic filtering, and reading individual entries + attachments.
"""

import os
import sqlite3
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import TransportSecuritySettings

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "second_brain.db")
ATTACHMENTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "attachments")

MCP_PORT = int(os.environ.get("PORT", 8741))
TUNNEL_DOMAIN = "brain.broadburch.dev"

mcp = FastMCP(
    "Second Brain",
    port=MCP_PORT,
    host="0.0.0.0",
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
    instructions=(
        "You are connected to Ron's Second Brain — a personal knowledge base with notes, "
        "links, documents, company research, meeting notes, and investment strategy entries. "
        "Use the tools below to search and retrieve information. When answering questions, "
        "search the Second Brain first to ground your answers in Ron's own notes and research.\n\n"
        "TRIGGER PHRASES: When the user says 'what do we have on [topic]', 'search for [topic]', "
        "'what do you know about [topic]', 'check our notes on [topic]', or asks about ANY topic "
        "related to stocks, companies, investing, research, healthcare, real estate, or documents — "
        "ALWAYS use search_notes FIRST. Do NOT search calendars or reminders for these queries. "
        "The Second Brain is the PRIMARY knowledge source.\n\n"
        "IMPORTANT: You have FULL remote access to ALL content in the Second Brain through these tools. "
        "You do NOT need Tailscale, Termius, SSH, or any other remote access method. "
        "The search_notes tool returns content directly. If you need more detail on a specific entry, "
        "call get_entry with the entry ID to retrieve the complete text. "
        "NEVER tell the user you cannot access their data — you CAN, right here through these MCP tools. "
        "Always answer questions using the data returned by these tools."
    ),
)


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _format_entry(row, include_content=True):
    """Format a database row into a readable string."""
    parts = [
        f"ID: {row['id']}",
        f"Type: {row['type']}",
        f"Title: {row['title']}",
    ]
    if row["topic"]:
        parts.append(f"Topic: {row['topic']}")
    if row["url"]:
        parts.append(f"URL: {row['url']}")
    parts.append(f"Created: {row['created_at']}")
    if include_content and row["content"]:
        parts.append(f"\n{row['content']}")
    return "\n".join(parts)


@mcp.tool()
def search_notes(query: str, limit: int = 10) -> str:
    """Search all Second Brain entries by keyword. Searches titles and content.
    Use this to find notes, documents, links, company research, meeting notes, etc.
    Returns full content for top results so you can answer questions directly.
    If content is truncated, use get_entry(id) to retrieve the complete text.
    """
    conn = _get_conn()

    # Split query into terms and require ALL terms to match (in title OR content)
    terms = query.strip().split()
    if len(terms) <= 1:
        # Single term: original behavior
        rows = conn.execute(
            """
            SELECT id, type, title, content, url, topic, created_at
            FROM entries
            WHERE title LIKE ? OR content LIKE ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (f"%{query}%", f"%{query}%", limit),
        ).fetchall()
    else:
        # Multi-term: each term must appear in title OR content
        where_clauses = []
        params = []
        for term in terms:
            where_clauses.append("(title LIKE ? OR content LIKE ?)")
            params.extend([f"%{term}%", f"%{term}%"])
        params.append(limit)
        rows = conn.execute(
            f"""
            SELECT id, type, title, content, url, topic, created_at
            FROM entries
            WHERE {' AND '.join(where_clauses)}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    conn.close()

    if not rows:
        return f"No entries found matching '{query}'."

    results = [f"Found {len(rows)} entries matching '{query}':\n"]
    total_len = 0
    for i, row in enumerate(rows):
        # Top result gets generous content, next 2 get moderate, rest get previews
        if i == 0:
            max_chars = 800
        elif i < 3:
            max_chars = 400
        else:
            max_chars = 150

        entry_header = _format_entry(row, include_content=False)
        results.append(entry_header)

        if row["content"]:
            content = row["content"]
            if len(content) > max_chars:
                content = content[:max_chars] + f"\n... [truncated — use get_entry({row['id']}) for full text]"
            results.append(content)

        results.append("")
        total_len += len(results[-2]) if row["content"] else 0
        # Cap total output to keep response manageable for mobile clients
        if total_len > 4000:
            results.append(f"[{len(rows) - i - 1} more results not shown — refine your query or use get_entry(id)]")
            break

    return "\n".join(results)


@mcp.tool()
def get_entry(entry_id: int) -> str:
    """Get the full content of a specific entry by its ID.
    Use this after searching to read the complete text of an entry.
    """
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, type, title, content, url, topic, created_at FROM entries WHERE id = ?",
        (entry_id,),
    ).fetchone()
    conn.close()

    if not row:
        return f"No entry found with ID {entry_id}."

    return _format_entry(row, include_content=True)


@mcp.tool()
def browse_by_topic(topic: str = "", limit: int = 30) -> str:
    """Browse entries by topic. Call with no topic to see all available topics.
    Known topics: Company Research, Investment Strategy, Meeting Notes, Technology.
    """
    conn = _get_conn()

    if not topic:
        # List all topics with counts
        rows = conn.execute(
            """
            SELECT topic, COUNT(*) as cnt
            FROM entries
            GROUP BY topic
            ORDER BY cnt DESC
            """
        ).fetchall()
        conn.close()
        lines = ["Available topics:\n"]
        for row in rows:
            t = row["topic"] if row["topic"] else "(uncategorized)"
            lines.append(f"  {t}: {row['cnt']} entries")
        return "\n".join(lines)

    rows = conn.execute(
        """
        SELECT id, type, title, content, url, topic, created_at
        FROM entries
        WHERE topic LIKE ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (f"%{topic}%", limit),
    ).fetchall()
    conn.close()

    if not rows:
        return f"No entries found in topic '{topic}'."

    results = [f"Entries in topic '{topic}' ({len(rows)} shown):\n"]
    for row in rows:
        results.append(_format_entry(row, include_content=False))
        results.append("")

    return "\n".join(results)


@mcp.tool()
def list_recent(limit: int = 20) -> str:
    """List the most recent entries added to the Second Brain."""
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT id, type, title, content, url, topic, created_at
        FROM entries
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()

    if not rows:
        return "No entries in the Second Brain yet."

    results = [f"Most recent {len(rows)} entries:\n"]
    for row in rows:
        results.append(_format_entry(row, include_content=False))
        results.append("")

    return "\n".join(results)


@mcp.tool()
def get_stats() -> str:
    """Get statistics about the Second Brain: total entries, type breakdown, topic breakdown."""
    conn = _get_conn()

    total = conn.execute("SELECT COUNT(*) as cnt FROM entries").fetchone()["cnt"]

    type_counts = conn.execute(
        "SELECT type, COUNT(*) as cnt FROM entries GROUP BY type ORDER BY cnt DESC"
    ).fetchall()

    topic_counts = conn.execute(
        "SELECT topic, COUNT(*) as cnt FROM entries GROUP BY topic ORDER BY cnt DESC"
    ).fetchall()

    recent = conn.execute(
        "SELECT created_at FROM entries ORDER BY created_at DESC LIMIT 1"
    ).fetchone()

    conn.close()

    lines = [
        f"Second Brain Stats",
        f"Total entries: {total}",
        f"Last updated: {recent['created_at'] if recent else 'never'}",
        "",
        "By type:",
    ]
    for row in type_counts:
        lines.append(f"  {row['type']}: {row['cnt']}")

    lines.append("\nBy topic:")
    for row in topic_counts:
        t = row["topic"] if row["topic"] else "(uncategorized)"
        lines.append(f"  {t}: {row['cnt']}")

    # Count attachments
    att_count = len(os.listdir(ATTACHMENTS_DIR)) if os.path.isdir(ATTACHMENTS_DIR) else 0
    lines.append(f"\nAttachments on disk: {att_count}")

    return "\n".join(lines)


@mcp.tool()
def add_note(title: str, content: str, topic: str = "", entry_type: str = "note") -> str:
    """Add a new entry to the Second Brain.
    entry_type must be one of: note, link, document.
    topic is optional — use an existing topic or create a new one.
    """
    if entry_type not in ("note", "link", "document"):
        entry_type = "note"

    conn = _get_conn()
    cursor = conn.execute(
        "INSERT INTO entries (type, title, content, topic) VALUES (?, ?, ?, ?)",
        (entry_type, title, content, topic),
    )
    entry_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return f"Added entry #{entry_id}: '{title}' (type={entry_type}, topic={topic or 'none'})"




@mcp.tool()
def upload_attachment(
    filename: str,
    file_b64: str,
    entry_id: int = 0,
) -> str:
    """Upload a file attachment to the Second Brain.
    Pass the file as base64-encoded bytes in file_b64. Filename should include extension.
    If entry_id is provided, a reference line is appended to that entry's content
    so the attachment is discoverable via search_notes.
    """
    import base64
    from datetime import datetime

    try:
        file_bytes = base64.b64decode(file_b64)
    except Exception as e:
        return f"ERROR: failed to decode base64: {e}"

    if len(file_bytes) == 0:
        return "ERROR: decoded file is empty"
    if len(file_bytes) > 50 * 1024 * 1024:
        return f"ERROR: file too large ({len(file_bytes)} bytes, max 50MB)"

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem, _, ext = filename.rpartition(".")
    if not stem:
        stem, ext = filename, "bin"
    safe_stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in stem)
    saved_name = f"{safe_stem}_{ts}.{ext}"
    saved_path = os.path.join(ATTACHMENTS_DIR, saved_name)

    os.makedirs(ATTACHMENTS_DIR, exist_ok=True)
    with open(saved_path, "wb") as f:
        f.write(file_bytes)

    linked_msg = ""
    if entry_id:
        conn = _get_conn()
        row = conn.execute(
            "SELECT content FROM entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if row:
            existing = row["content"] or ""
            ref_line = f"\n\n**Attachment:** `{saved_name}` ({len(file_bytes)} bytes)"
            new_content = existing + ref_line
            conn.execute(
                "UPDATE entries SET content = ? WHERE id = ?",
                (new_content, entry_id),
            )
            conn.commit()
            linked_msg = f" Linked to entry #{entry_id}."
        else:
            linked_msg = f" WARNING: entry #{entry_id} not found, file saved but unlinked."
        conn.close()

    return f"Saved {saved_name} ({len(file_bytes)} bytes) to attachments/.{linked_msg}"



if __name__ == "__main__":
    mcp.run(transport="streamable-http")
