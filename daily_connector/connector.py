#!/usr/bin/env python3
"""
Second Brain Daily Connector
Reads recent entries, finds non-obvious connections via Claude, writes a
synthesis entry back to the DB, and pings Telegram.
"""

import os
import sys
import time
import sqlite3
import traceback
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── bootstrap ──────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
dotenv_path = SCRIPT_DIR / ".env"

from dotenv import load_dotenv
load_dotenv(dotenv_path)

import anthropic
import requests

# ── config ─────────────────────────────────────────────────────────────────────
DB_PATH          = Path.home() / "second-brain" / "second_brain.db"
PROMPT_PATH      = SCRIPT_DIR / "prompt.txt"
ANTHROPIC_KEY    = os.environ["ANTHROPIC_API_KEY"]
TELEGRAM_TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
SMTP_USER        = os.environ["SMTP_USER"]
SMTP_PASS        = os.environ["SMTP_PASS"]
EMAIL_TO         = os.environ["EMAIL_TO"]
MODEL            = "claude-opus-4-6"
MAX_TOKENS       = 1500

STOPWORDS = {
    "the","a","an","and","or","but","in","on","at","to","for","of","with",
    "is","was","are","were","be","been","being","have","has","had","do",
    "does","did","will","would","could","should","may","might","shall",
    "that","this","these","those","it","its","we","our","you","your",
    "he","she","they","their","from","by","as","into","about","i","my",
    "not","no","so","if","than","then","when","which","who","what","how",
    "all","also","more","some","can","one","new","up","out","there","over",
}


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def tokenize(text: str, top_n: int = 8) -> list[str]:
    """Return top_n distinctive tokens (length >= 4, not stopwords)."""
    words = re.findall(r"[a-zA-Z]{4,}", text.lower())
    freq: dict[str, int] = {}
    for w in words:
        if w not in STOPWORDS:
            freq[w] = freq.get(w, 0) + 1
    ranked = sorted(freq, key=lambda w: -freq[w])
    return ranked[:top_n]


def fetch_recent_entries(conn: sqlite3.Connection) -> list[dict]:
    """
    Pull the 5 most recent entries from the last 24 h.
    Fall back to the 5 most recent overall if fewer than 5 qualify.
    Exclude topic='Daily Synthesis' entries.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    cur = conn.execute(
        """
        SELECT id, type, title, content, topic, created_at
        FROM entries
        WHERE topic != 'Daily Synthesis'
          AND created_at >= ?
        ORDER BY created_at DESC
        LIMIT 5
        """,
        (cutoff,),
    )
    rows = cur.fetchall()
    if len(rows) < 5:
        log(f"  Only {len(rows)} entries in last 24 h — falling back to 5 most recent overall")
        cur = conn.execute(
            """
            SELECT id, type, title, content, topic, created_at
            FROM entries
            WHERE topic != 'Daily Synthesis'
            ORDER BY created_at DESC
            LIMIT 5
            """
        )
        rows = cur.fetchall()
    cols = ["id", "type", "title", "content", "topic", "created_at"]
    return [dict(zip(cols, r)) for r in rows]


def find_candidates(conn: sqlite3.Connection, entry: dict, exclude_ids: list[int]) -> list[dict]:
    """
    For a single entry, find up to 3 older entries that share keyword overlap.
    Excludes: the entry itself, all recent-entry IDs, and Daily Synthesis entries.
    """
    text = entry["title"] + " " + entry["content"][:500]
    tokens = tokenize(text)
    if not tokens:
        return []

    placeholders_excl = ",".join("?" * len(exclude_ids))
    like_clauses = " OR ".join(
        ["content LIKE ? OR topic LIKE ? OR title LIKE ?"] * len(tokens)
    )
    like_params = []
    for t in tokens:
        pat = f"%{t}%"
        like_params.extend([pat, pat, pat])

    sql = f"""
        SELECT id, type, title, content, topic, created_at
        FROM entries
        WHERE topic != 'Daily Synthesis'
          AND id NOT IN ({placeholders_excl})
          AND ({like_clauses})
        ORDER BY created_at DESC
        LIMIT 3
    """
    params = exclude_ids + like_params
    cur = conn.execute(sql, params)
    cols = ["id", "type", "title", "content", "topic", "created_at"]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def build_prompt(system_prompt: str, recent: list[dict], candidates_map: dict) -> str:
    parts = [system_prompt.strip(), "\n\n---\n\n## RECENT ENTRIES (last 24 h)\n"]
    for e in recent:
        body = e["content"][:800].replace("\n", " ")
        parts.append(
            f"### [{e['id']}] {e['title']}\n"
            f"**Topic:** {e['topic'] or 'N/A'} | **Type:** {e['type']} | **Created:** {e['created_at']}\n"
            f"{body}\n"
        )
        cands = candidates_map.get(e["id"], [])
        if cands:
            parts.append("#### Linked older entries:\n")
            for c in cands:
                cbody = c["content"][:400].replace("\n", " ")
                parts.append(
                    f"- **[{c['id']}] {c['title']}** (topic: {c['topic'] or 'N/A'}, {c['created_at']}): {cbody}\n"
                )
        parts.append("")
    return "\n".join(parts)


def call_claude(prompt_text: str) -> tuple[str, float]:
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    t0 = time.time()
    message = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt_text}],
    )
    latency = time.time() - t0
    return message.content[0].text, latency


def insert_synthesis(conn: sqlite3.Connection, synthesis: str, date_str: str) -> int:
    title = f"Daily Synthesis {date_str}"
    cur = conn.execute(
        """
        INSERT INTO entries (type, title, content, topic, created_at)
        VALUES ('note', ?, ?, 'Daily Synthesis', ?)
        """,
        (title, synthesis, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    return cur.lastrowid


def send_telegram(synthesis: str, entry_id: int) -> bool:
    snippet = synthesis[:800]
    body = f"{snippet}\n\n📖 Full entry in Second Brain (#{entry_id})"
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": body,
                "parse_mode": "Markdown",
            },
            timeout=15,
        )
        resp.raise_for_status()
        log(f"  Telegram OK (status {resp.status_code})")
        return True
    except Exception as exc:
        log(f"  Telegram FAILED: {exc}")
        return False


def send_email(synthesis: str, entry_id: int, date_str: str) -> bool:
    subject = f"Daily Synthesis {date_str} (#{entry_id})"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SMTP_USER
    msg["To"]      = EMAIL_TO
    msg.attach(MIMEText(synthesis, "plain"))
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, EMAIL_TO, msg.as_string())
        log(f"  Email OK → {EMAIL_TO}")
        return True
    except Exception as exc:
        log(f"  Email FAILED: {exc}")
        return False


# ── main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    start = datetime.now()
    log(f"=== Daily Connector starting ({start.strftime('%Y-%m-%d %H:%M:%S')}) ===")
    log(f"DB: {DB_PATH}")

    system_prompt = PROMPT_PATH.read_text()
    today = start.strftime("%Y-%m-%d")

    conn = sqlite3.connect(str(DB_PATH))
    try:
        # Step 1 — recent entries
        log("Fetching recent entries...")
        recent = fetch_recent_entries(conn)
        log(f"  Found {len(recent)} recent entries: {[e['id'] for e in recent]}")

        # Step 2 — candidates
        all_recent_ids = [e["id"] for e in recent]
        candidates_map: dict[int, list[dict]] = {}
        for e in recent:
            exclude = list(set(all_recent_ids))  # exclude all recent entries
            cands = find_candidates(conn, e, exclude)
            candidates_map[e["id"]] = cands
            log(f"  Entry {e['id']} → {len(cands)} candidate(s): {[c['id'] for c in cands]}")

        # Step 3 — build prompt & call Claude
        full_prompt = build_prompt(system_prompt, recent, candidates_map)
        log(f"Calling Claude ({MODEL}, max_tokens={MAX_TOKENS})...")
        synthesis, latency = call_claude(full_prompt)
        log(f"  API latency: {latency:.1f}s | response length: {len(synthesis)} chars")

        # Step 4 — insert into DB
        log("Inserting synthesis entry...")
        new_id = insert_synthesis(conn, synthesis, today)
        log(f"  New entry ID: {new_id}")

        # Step 5 — Telegram
        log("Sending Telegram notification...")
        tg_ok = send_telegram(synthesis, new_id)
        log(f"  Telegram: {'OK' if tg_ok else 'FAILED (DB write succeeded)'}")

        # Step 6 — Email
        log("Sending email...")
        email_ok = send_email(synthesis, new_id, today)
        log(f"  Email: {'OK' if email_ok else 'FAILED (DB write succeeded)'}")

        elapsed = (datetime.now() - start).total_seconds()
        log(f"=== Done in {elapsed:.1f}s ===")

    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] FATAL ERROR:", flush=True)
        traceback.print_exc()
        sys.exit(1)
