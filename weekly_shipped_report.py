#!/usr/bin/env python3
"""
Weekly Shipped Report — Broadburch Partners
Emails Ron a summary of everything shipped since last Friday at 4 PM.
Covers: git commits (all repos), new Second Brain entries, new reports.
Runs Fridays at 4 PM via LaunchAgent.
"""

import os
import sys
import sqlite3
import subprocess
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(BASE_DIR), "stock_screener"))

from config import EMAIL_CONFIG

DB_PATH = os.path.join(BASE_DIR, "second_brain.db")

REPOS = [
    ("/Users/ronrusso/stock_screener", "Stock Screener"),
    ("/Users/ronrusso/second-brain", "Second Brain"),
    ("/Users/ronrusso/stock_dashboard", "Stock Dashboard"),
    ("/Users/ronrusso/productivity-system", "Productivity System"),
]

RECIPIENTS = ["thechargerr2@gmail.com", "ron@broadburch.com"]


def get_cutoff():
    """Last Friday at 4 PM ET."""
    now = datetime.now()
    days_since_friday = (now.weekday() - 4) % 7
    if days_since_friday == 0 and now.hour < 16:
        days_since_friday = 7
    elif days_since_friday == 0:
        days_since_friday = 7  # always look back a full week
    last_friday = now.replace(hour=16, minute=0, second=0, microsecond=0) - timedelta(days=days_since_friday)
    return last_friday


def get_git_commits(repo_path, since):
    """Get commits from a git repo since a datetime."""
    since_str = since.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        result = subprocess.run(
            ["git", "log", f"--since={since_str}", "--all",
             "--pretty=format:%h|%s|%an|%ai", "--no-merges"],
            cwd=repo_path, capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []
        commits = []
        for line in result.stdout.strip().split("\n"):
            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append({
                    "hash": parts[0],
                    "message": parts[1],
                    "author": parts[2],
                    "date": parts[3][:16],
                })
        return commits
    except Exception:
        return []


def get_new_entries(since):
    """Get new Second Brain entries since cutoff — only real work product, not bulk imports."""
    try:
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        since_str = since.strftime("%Y-%m-%d %H:%M:%S")
        rows = db.execute(
            """SELECT id, type, title, topic, created_at FROM entries
               WHERE created_at >= ?
                 AND type = 'document'
                 AND title NOT LIKE 'Import 20%%'
                 AND title NOT LIKE 'Skip to content%%'
               ORDER BY created_at""",
            (since_str,)
        ).fetchall()
        db.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_new_reports(since):
    """Get new report files generated since cutoff."""
    reports_dir = "/Users/ronrusso/stock_screener/reports"
    if not os.path.isdir(reports_dir):
        return []
    cutoff_ts = since.timestamp()
    reports = []
    for f in os.listdir(reports_dir):
        fpath = os.path.join(reports_dir, f)
        if os.path.isfile(fpath) and os.path.getmtime(fpath) >= cutoff_ts:
            reports.append({
                "name": f,
                "date": datetime.fromtimestamp(os.path.getmtime(fpath)).strftime("%Y-%m-%d %H:%M"),
            })
    reports.sort(key=lambda r: r["date"])
    return reports


def build_html(cutoff, entries):
    """Build the email HTML."""
    now = datetime.now()
    cutoff_str = cutoff.strftime("%b %d, %Y %I:%M %p")
    now_str = now.strftime("%b %d, %Y %I:%M %p")

    html = f"""
    <html>
    <head>
    <style>
        body {{ font-family: Arial, sans-serif; color: #1a1a1a; background: #f5f5f5; padding: 20px; }}
        .container {{ max-width: 700px; margin: 0 auto; background: #fff; border-radius: 8px; padding: 30px; }}
        h1 {{ color: #0a1628; font-size: 22px; border-bottom: 3px solid #1a365d; padding-bottom: 10px; }}
        h2 {{ color: #1a365d; font-size: 17px; margin-top: 28px; }}
        .period {{ color: #666; font-size: 13px; margin-bottom: 20px; }}
        .stat {{ display: inline-block; background: #edf2f7; border-radius: 6px; padding: 8px 16px; margin: 4px; font-size: 14px; }}
        .stat strong {{ color: #1a365d; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 8px; }}
        th {{ background: #0a1628; color: #fff; padding: 8px 10px; text-align: left; }}
        td {{ padding: 6px 10px; border-bottom: 1px solid #e2e8f0; }}
        tr:nth-child(even) {{ background: #f7fafc; }}
        .hash {{ font-family: monospace; color: #2b6cb0; }}
        .topic {{ background: #edf2f7; border-radius: 3px; padding: 2px 6px; font-size: 11px; color: #4a5568; }}
        .footer {{ margin-top: 30px; padding-top: 15px; border-top: 1px solid #e2e8f0; color: #999; font-size: 11px; }}
        .empty {{ color: #999; font-style: italic; }}
    </style>
    </head>
    <body>
    <div class="container">
        <h1>Weekly Shipped Report</h1>
        <div class="period">{cutoff_str} &rarr; {now_str}</div>
    """

    # Documents created this week, grouped by topic
    html += f"<p style='font-size:14px; color:#4a5568;'><strong>{len(entries)}</strong> documents created</p>"

    if entries:
        by_topic = {}
        for e in entries:
            t = e.get("topic") or "Uncategorized"
            by_topic.setdefault(t, []).append(e)
        for topic in sorted(by_topic.keys()):
            items = by_topic[topic]
            html += f"<h2 style='margin-bottom:6px;'>{topic} ({len(items)})</h2>"
            html += "<ul style='margin-top:4px; padding-left:20px;'>"
            for e in items:
                name = e['title'].replace('Import 2026-', '').strip()
                date = e['created_at'][:10]
                html += f"<li style='margin-bottom:3px; font-size:13px;'>{name} <span style='color:#999;'>({date})</span></li>"
            html += "</ul>"
    else:
        html += "<p class='empty'>No new documents this week.</p>"

    html += """
        <div class="footer">
            Broadburch Partners &bull; Weekly Shipped Report &bull; Auto-generated
        </div>
    </div>
    </body>
    </html>
    """
    return html


def send_email(html):
    """Send the report email."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Weekly Shipped Report — {datetime.now().strftime('%b %d, %Y')}"
    msg["From"] = EMAIL_CONFIG["sender_email"]
    msg["To"] = ", ".join(RECIPIENTS)
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(EMAIL_CONFIG["smtp_server"], EMAIL_CONFIG["smtp_port"]) as server:
        server.starttls()
        server.login(EMAIL_CONFIG["sender_email"], EMAIL_CONFIG["sender_password"])
        server.sendmail(EMAIL_CONFIG["sender_email"], RECIPIENTS, msg.as_string())

    print(f"Shipped report sent to {', '.join(RECIPIENTS)}")


def main():
    cutoff = get_cutoff()
    print(f"Generating shipped report since {cutoff}")

    # Collect data
    entries = get_new_entries(cutoff)

    # Build and send
    html = build_html(cutoff, entries)
    send_email(html)


if __name__ == "__main__":
    main()
