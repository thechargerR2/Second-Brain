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
    conn.commit()
    conn.close()


def add_entry(entry_type, title, content, url=""):
    conn = get_connection()
    conn.execute(
        "INSERT INTO entries (type, title, content, url) VALUES (?, ?, ?, ?)",
        (entry_type, title, content, url),
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
