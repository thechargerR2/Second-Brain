#!/usr/bin/env python3
"""
Second Brain Topic Organizer
Runs weekly to auto-categorize entries into topic folders using Claude.
Entries remain searchable via the main DB — topics just add organization.

Usage:
    python3 organize_topics.py              # Organize uncategorized entries
    python3 organize_topics.py --all        # Re-organize all entries
    python3 organize_topics.py --dry-run    # Preview without saving
    python3 organize_topics.py --list       # List all topics and entry counts
"""

import os
import sys
import json
import sqlite3
import logging
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
sys.path.insert(0, BASE_DIR)

os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "organize_topics.log")),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("organize_topics")

# Load API key
from dotenv import load_dotenv
load_dotenv(os.path.join(BASE_DIR, ".env"))

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ── Predefined topics (will grow over time) ───────────────────────────
# The AI can suggest new topics; these are starting seeds.
SEED_TOPICS = [
    "Investment Strategy",
    "Market Analysis",
    "Annual Outlook",
    "Portfolio Management",
    "Economic Data",
    "Company Research",
    "Healthcare & CINQCARE",
    "Real Estate",
    "Technology",
    "Personal Finance",
    "Business Strategy",
    "Reading & Articles",
    "Meeting Notes",
    "Ideas & Brainstorms",
    "Reference Material",
]


def get_existing_topics():
    """Get all topics currently in use."""
    from database import get_connection
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT topic FROM entries WHERE topic IS NOT NULL AND topic != ''"
    ).fetchall()
    conn.close()
    return [r["topic"] for r in rows]


def get_uncategorized_entries():
    """Get entries without a topic assigned."""
    from database import get_connection
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, type, title, content, url FROM entries "
        "WHERE topic IS NULL OR topic = '' "
        "ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_entries_for_reorg():
    """Get all entries for re-organization."""
    from database import get_connection
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, type, title, content, url FROM entries ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def categorize_entries_with_claude(entries, existing_topics):
    """
    Use Claude to assign topics to a batch of entries.
    Returns dict of {entry_id: topic_name}.
    """
    import anthropic

    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY not set — cannot categorize entries")
        return {}

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # Build entry summaries for Claude (truncate content to save tokens)
    entry_summaries = []
    for e in entries:
        content_preview = (e.get("content") or "")[:500]
        entry_summaries.append({
            "id": e["id"],
            "type": e["type"],
            "title": e["title"],
            "content_preview": content_preview,
            "url": e.get("url", ""),
        })

    all_topics = list(set(SEED_TOPICS + existing_topics))

    prompt = f"""You are organizing a personal knowledge base ("second brain") into topic folders.

Here are the existing topic folders:
{json.dumps(all_topics, indent=2)}

Below are entries that need to be categorized. For each entry, assign it to the BEST matching topic folder. You may create a new topic if none of the existing ones fit well, but prefer existing topics when reasonable.

Keep topic names short (2-4 words), title case, and general enough to hold multiple entries.

Entries to categorize:
{json.dumps(entry_summaries, indent=2)}

Respond with ONLY a JSON object mapping entry IDs to topic names. Example:
{{"1": "Investment Strategy", "2": "Market Analysis", "3": "New Topic Name"}}"""

    # Process in batches of 30 to stay within token limits
    all_assignments = {}

    for i in range(0, len(entry_summaries), 30):
        batch = entry_summaries[i:i + 30]

        batch_prompt = f"""You are organizing a personal knowledge base ("second brain") into topic folders.

Here are the existing topic folders:
{json.dumps(all_topics, indent=2)}

Below are entries that need to be categorized. For each entry, assign it to the BEST matching topic folder. You may create a new topic if none of the existing ones fit well, but prefer existing topics when reasonable.

Keep topic names short (2-4 words), title case, and general enough to hold multiple entries.

Entries to categorize:
{json.dumps(batch, indent=2)}

Respond with ONLY a JSON object mapping entry IDs to topic names. No other text."""

        try:
            response = client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=2000,
                messages=[{"role": "user", "content": batch_prompt}],
            )
            result_text = response.content[0].text.strip()

            # Parse JSON from response (handle markdown code blocks)
            if result_text.startswith("```"):
                result_text = result_text.split("```")[1]
                if result_text.startswith("json"):
                    result_text = result_text[4:]

            assignments = json.loads(result_text)
            # Convert string IDs to ints
            for k, v in assignments.items():
                all_assignments[int(k)] = v
                # Track new topics for subsequent batches
                if v not in all_topics:
                    all_topics.append(v)

            log.info(f"  Categorized batch of {len(batch)} entries")

        except Exception as e:
            log.error(f"  Claude categorization failed for batch: {e}")

    return all_assignments


def save_topics(assignments):
    """Save topic assignments to the database."""
    from database import get_connection
    conn = get_connection()
    for entry_id, topic in assignments.items():
        conn.execute(
            "UPDATE entries SET topic = ? WHERE id = ?",
            (topic, entry_id),
        )
    conn.commit()
    conn.close()
    log.info(f"Saved {len(assignments)} topic assignments.")


def list_topics():
    """Print all topics and their entry counts."""
    from database import get_connection, init_db
    init_db()
    conn = get_connection()
    rows = conn.execute(
        "SELECT COALESCE(NULLIF(topic, ''), '(uncategorized)') as t, COUNT(*) as c "
        "FROM entries GROUP BY t ORDER BY c DESC"
    ).fetchall()
    conn.close()

    print("\nSecond Brain Topics:")
    print("-" * 45)
    for r in rows:
        print(f"  {r['t']:<35} {r['c']:>3} entries")
    print(f"\n  {'TOTAL':<35} {sum(r['c'] for r in rows):>3} entries")


def organize(reorg_all=False, dry_run=False):
    """Main organization flow."""
    from database import init_db
    init_db()

    existing_topics = get_existing_topics()

    if reorg_all:
        entries = get_all_entries_for_reorg()
        log.info(f"Re-organizing all {len(entries)} entries...")
    else:
        entries = get_uncategorized_entries()
        if not entries:
            log.info("All entries are already categorized.")
            return
        log.info(f"Found {len(entries)} uncategorized entries.")

    assignments = categorize_entries_with_claude(entries, existing_topics)

    if not assignments:
        log.warning("No categorizations returned.")
        return

    if dry_run:
        print("\n[DRY RUN] Would assign topics:")
        for eid, topic in sorted(assignments.items()):
            entry = next((e for e in entries if e["id"] == eid), None)
            title = entry["title"] if entry else f"#{eid}"
            print(f"  #{eid}: {title[:50]:<50} → {topic}")
        return

    save_topics(assignments)

    # Print summary
    topic_counts = {}
    for topic in assignments.values():
        topic_counts[topic] = topic_counts.get(topic, 0) + 1
    print("\nTopics assigned:")
    for topic, count in sorted(topic_counts.items(), key=lambda x: -x[1]):
        print(f"  {topic}: {count} entries")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Organize Second Brain entries into topic folders")
    parser.add_argument("--all", action="store_true", help="Re-organize all entries")
    parser.add_argument("--dry-run", action="store_true", help="Preview without saving")
    parser.add_argument("--list", action="store_true", help="List all topics")
    args = parser.parse_args()

    if args.list:
        list_topics()
        return

    try:
        organize(reorg_all=args.all, dry_run=args.dry_run)
    except Exception as e:
        log.error(f"Organization failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
