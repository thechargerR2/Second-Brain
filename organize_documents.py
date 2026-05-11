"""
Organize documents in second-brain/documents/ into topic folders.
Also syncs any new files from stock_screener/reports/ that aren't already present.
Run weekly via LaunchAgent or manually: python3 organize_documents.py
"""

import os
import re
import shutil
from pathlib import Path

DOCUMENTS_DIR = Path.home() / "second-brain" / "documents"
REPORTS_DIR = Path.home() / "stock_screener" / "reports"

# Recurring auto-generated files to skip when syncing from reports
SKIP_PATTERNS = [
    r"^screener_",
    r"^hotlist_",
    r"^daily_report_",
    r"^morning_briefing_",
    r"^portfolio_performance_",
    r"^~\$",
    r"_latest\.",
]

VALID_EXTENSIONS = {".txt", ".html", ".pdf", ".docx", ".xlsx"}

# ── Topic classification rules ──
# Order matters: first match wins

RULES = [
    # CINQCARE / Healthcare
    (r"cinqcare|cinqiq|access.*sales|access.*executive|access.*detailed|mentorship|employee.commitment|access.payments",
     "CINQCARE & Healthcare"),

    # Due Diligence memos
    (r"due.diligence|broadburch.partners.dd|^dd.memo",
     "Due Diligence Memos"),

    # Robotics & AI
    (r"robot|figure.ai",
     "Robotics & AI"),

    # Space & Infrastructure
    (r"space|satellite|dc.picks|infrastructure",
     "Space & Infrastructure"),

    # Real Estate & Government
    (r"claremont|detention|185.west.gale|govt.programs|general.information.185|fresno",
     "Real Estate & Government"),

    # Portfolio & Strategy
    (r"portfolio|rebalanc|dividend|investment.memo.10|growth.stock|broadburch.capital|expansion|investment.philosophy",
     "Portfolio & Strategy"),

    # Investment Research
    (r"brad.smith|function.health|private.credit|rwr.dashboard|investment.memo|sector.memo",
     "Investment Research"),

    # Primers & Thematic Research (ML reports, primers, deep dives)
    (r"primer|thematic|deep.dive|ML_|photonics|bottleneck",
     "Primers & Thematic Research"),

    # Software & Technology
    (r"software|ai.software",
     "Software & Technology"),
]


def classify(filename):
    fn = filename.lower()
    for pattern, topic in RULES:
        if re.search(pattern, fn, re.IGNORECASE):
            return topic
    return "Other Research"


def should_skip(filename):
    for pat in SKIP_PATTERNS:
        if re.match(pat, filename, re.IGNORECASE):
            return True
    return False


def sync_new_files():
    """Copy new files from reports/ that aren't already in documents/."""
    existing = {f.name for f in DOCUMENTS_DIR.rglob("*") if f.is_file()}
    synced = 0

    for f in REPORTS_DIR.iterdir():
        if not f.is_file():
            continue
        if f.suffix.lower() not in VALID_EXTENSIONS:
            continue
        if should_skip(f.name):
            continue
        if f.name in existing:
            continue

        shutil.copy2(f, DOCUMENTS_DIR / f.name)
        synced += 1
        print(f"  SYNCED: {f.name}")

    return synced


def organize():
    """Move files in documents/ root into topic subfolders."""
    moved = 0

    for f in sorted(DOCUMENTS_DIR.iterdir()):
        if not f.is_file():
            continue
        if f.suffix.lower() not in VALID_EXTENSIONS:
            continue

        topic = classify(f.name)
        topic_dir = DOCUMENTS_DIR / topic

        topic_dir.mkdir(exist_ok=True)

        dest = topic_dir / f.name
        if dest.exists():
            # Already organized
            continue

        shutil.move(str(f), str(dest))
        moved += 1
        print(f"  {f.name} -> {topic}/")

    return moved


def main():
    print("=== Syncing new files from reports/ ===")
    synced = sync_new_files()
    print(f"Synced {synced} new files\n")

    print("=== Organizing into topic folders ===")
    moved = organize()
    print(f"Organized {moved} files\n")

    # Summary
    print("=== Folder summary ===")
    for d in sorted(DOCUMENTS_DIR.iterdir()):
        if d.is_dir():
            count = sum(1 for f in d.iterdir() if f.is_file())
            print(f"  {d.name}: {count} files")

    root_files = sum(1 for f in DOCUMENTS_DIR.iterdir() if f.is_file())
    if root_files:
        print(f"  (uncategorized): {root_files} files")


if __name__ == "__main__":
    main()
