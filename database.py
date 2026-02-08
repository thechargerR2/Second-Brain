import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "second_brain.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL CHECK(type IN ('note', 'link', 'document')),
            title TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            url TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    # Add drive_file_id column if it doesn't exist (migration)
    try:
        conn.execute("ALTER TABLE entries ADD COLUMN drive_file_id TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists

    conn.commit()
    conn.close()


def add_entry(entry_type, title, content, url=""):
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO entries (type, title, content, url) VALUES (?, ?, ?, ?)",
        (entry_type, title, content, url),
    )
    entry_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return entry_id


def update_drive_file_id(entry_id, file_id):
    conn = get_connection()
    conn.execute(
        "UPDATE entries SET drive_file_id = ? WHERE id = ?",
        (file_id, entry_id),
    )
    conn.commit()
    conn.close()


def get_all_entries(search_query=None):
    conn = get_connection()
    if search_query:
        rows = conn.execute(
            """
            SELECT * FROM entries
            WHERE title LIKE ? OR content LIKE ?
            ORDER BY created_at DESC
            """,
            (f"%{search_query}%", f"%{search_query}%"),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM entries ORDER BY created_at DESC"
        ).fetchall()
    conn.close()
    return rows


def get_entry(entry_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    conn.close()
    return row


def delete_entry(entry_id):
    conn = get_connection()
    conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
    conn.commit()
    conn.close()
