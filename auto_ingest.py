"""
Second Brain Auto-Ingest Helper
Call from any report script to add content to the second brain DB.

Usage from other scripts:
    from auto_ingest import add_to_second_brain
    add_to_second_brain("DD Memo: AAPL (2026-03-10)", memo_text)
    add_to_second_brain("Robotics Industry Memo (2026-02-19)", memo_text, topic="Company Research")
    add_to_second_brain_from_file("Space Infrastructure Memo", "/path/to/memo.txt")
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)


def add_to_second_brain(title, content, url="", entry_type="document", topic=""):
    """Add an entry directly to the second brain DB."""
    try:
        from database import init_db, add_entry, get_connection
        init_db()

        # Skip if already imported (by title, case-insensitive)
        conn = get_connection()
        existing = conn.execute(
            "SELECT id FROM entries WHERE LOWER(title) = LOWER(?)", (title,)
        ).fetchone()
        conn.close()
        if existing:
            return existing["id"]

        entry_id = add_entry(entry_type, title, content, url)

        # Set topic if provided
        if topic:
            conn = get_connection()
            conn.execute("UPDATE entries SET topic = ? WHERE id = ?", (topic, entry_id))
            conn.commit()
            conn.close()

        return entry_id
    except Exception as e:
        print(f"Second Brain auto-add failed: {e}")
        return None


def add_action_item(title, content, source=""):
    """Create an action-required entry in the Second Brain with open status."""
    try:
        from database import init_db, get_connection
        init_db()

        # Deduplicate by title
        conn = get_connection()
        existing = conn.execute(
            "SELECT id FROM entries WHERE LOWER(title) = LOWER(?)", (title,)
        ).fetchone()
        if existing:
            conn.close()
            return existing["id"]

        conn.execute(
            "INSERT INTO entries (type, title, content, topic, action_status, action_source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("note", title, content, "action_required", "open", source),
        )
        entry_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()
        return entry_id
    except Exception as e:
        print(f"Second Brain action item failed: {e}")
        return None


def add_to_second_brain_from_file(title, filepath, topic=""):
    """Read a text file and add its contents to the second brain."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return add_to_second_brain(title, content, topic=topic)
    except Exception as e:
        print(f"Second Brain auto-add from file failed: {e}")
        return None
