#!/usr/bin/env python3
"""
Backfill existing DD memos and industry reports into the Second Brain DB.
Run once to import all historical reports, then the auto-hook handles future ones.

Usage:
    python3 backfill_reports.py              # Import all reports
    python3 backfill_reports.py --dry-run    # Preview without importing
"""

import os
import sys
import re
import logging
from pathlib import Path

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.expanduser("~/stock_screener/reports")
sys.path.insert(0, BASE_DIR)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("backfill")


def already_imported(title):
    """Check if an entry with this title already exists."""
    from database import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM entries WHERE title = ?", (title,)
    ).fetchone()
    conn.close()
    return row is not None


def import_dd_memos(dry_run=False):
    """Import all DD memo .txt files."""
    from database import init_db, add_entry
    init_db()

    pattern = re.compile(r"Broadburch_Partners_DD_(\w+)_(\d{4}-\d{2}-\d{2})\.txt")
    imported = 0

    for filename in sorted(os.listdir(REPORTS_DIR)):
        match = pattern.match(filename)
        if not match:
            continue

        ticker = match.group(1)
        date = match.group(2)
        filepath = os.path.join(REPORTS_DIR, filename)

        title = f"DD Memo: {ticker} ({date})"

        if already_imported(title):
            log.info(f"  Already imported: {title}")
            continue

        content = Path(filepath).read_text(encoding="utf-8", errors="replace")

        if dry_run:
            log.info(f"  [DRY RUN] Would import: {title} ({len(content)} chars)")
        else:
            entry_id = add_entry("document", title, content, "")
            log.info(f"  Imported #{entry_id}: {title} ({len(content)} chars)")
        imported += 1

    return imported


def import_industry_reports(dry_run=False):
    """Import industry overview/sector memo .txt files."""
    from database import init_db, add_entry
    init_db()

    # Match known industry report text files
    report_patterns = [
        (r"Robotics_Industry_Investment_Memo_(\d{4}-\d{2}-\d{2})\.txt", "Robotics Industry Investment Memo"),
        (r"space_infrastructure_sector_memo_(\d{4}-\d{2}-\d{2})\.txt", "Space Infrastructure Sector Memo"),
        (r"space_telecom_satellite_memo_(\d{8})\.txt", "Space Telecom & Satellite Memo"),
    ]

    imported = 0

    for filename in sorted(os.listdir(REPORTS_DIR)):
        for pattern_str, report_name in report_patterns:
            match = re.match(pattern_str, filename)
            if not match:
                continue

            date_str = match.group(1)
            # Normalize date format
            if len(date_str) == 8:  # YYYYMMDD
                date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

            filepath = os.path.join(REPORTS_DIR, filename)
            title = f"{report_name} ({date_str})"

            if already_imported(title):
                log.info(f"  Already imported: {title}")
                continue

            content = Path(filepath).read_text(encoding="utf-8", errors="replace")

            if dry_run:
                log.info(f"  [DRY RUN] Would import: {title} ({len(content)} chars)")
            else:
                entry_id = add_entry("document", title, content, "")
                log.info(f"  Imported #{entry_id}: {title} ({len(content)} chars)")
            imported += 1

    return imported


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Backfill reports into Second Brain")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    log.info("=== Importing DD Memos ===")
    dd_count = import_dd_memos(dry_run=args.dry_run)

    log.info("=== Importing Industry Reports ===")
    industry_count = import_industry_reports(dry_run=args.dry_run)

    action = "Would import" if args.dry_run else "Imported"
    log.info(f"\n{action} {dd_count} DD memos + {industry_count} industry reports = {dd_count + industry_count} total")


if __name__ == "__main__":
    main()
