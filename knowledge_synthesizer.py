#!/usr/bin/env python3
"""
Weekly Knowledge Synthesizer — Broadburch Partners

Synthesizes the past 7 days of *manually ingested* material in Second Brain
into a narrative weekly report:
  - What's emerging (themes forming across multiple sources)
  - What's surprising (contradictions / new positions)
  - What connects (cross-domain links, esp. CINQCARE x investing)
  - What's drifting (topics that used to be active but went quiet)

This is NOT a market-analysis pipeline (that's analyst_agent.py + the crews).
This is a synthesis layer over Ron's own knowledge corpus.

Usage:
    python3 knowledge_synthesizer.py              # run + email + file to SB
    python3 knowledge_synthesizer.py --no-email   # run, skip email
    python3 knowledge_synthesizer.py --dry-run    # print prompt + skip LLM/email/file
    python3 knowledge_synthesizer.py --stdout     # run, print to stdout, skip email/file
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

import anthropic

sys.path.insert(0, os.path.expanduser("~/stock_screener"))
from send_email import send  # default recipients: thechargerr2 + ron@broadburch

DB_PATH = os.path.join(BASE_DIR, "second_brain.db")
MODEL = "claude-sonnet-4-5"
RECENT_DAYS = 7
PRIOR_DAYS = 60   # for drift detection: 8–60d ago
MAX_CONTENT_CHARS = 1500  # truncate per-entry content to keep prompt size bounded

# Patterns to exclude — these are entries auto-filed by other pipelines, not
# things Ron actually ingested. The synthesizer should reflect on his own
# inputs, not regurgitate analyst briefs back at him.
AUTO_TITLE_PREFIXES = (
    "Analyst Brief",
    "Buy/Sell Alert",
    "Catalyst Calendar",
    "Thesis Monitor",
    "Weekend Review",
    "Weekly Partner Digest",
    "Weekly Shipped",
    "Morning Briefing",
    "Investment Crew Weekly",
    "Investment Crew —",
    "Cross-Domain Intel",
    "Weekly Knowledge Synthesis",   # don't let the synthesizer read its own prior outputs
    "Crew Idea:",       # auto-filed by investment-crew
    "CDI Company:",     # auto-filed by cross-domain-intel
    "Daily Synthesis",  # auto-generated daily summary
    "Import 2026-",     # Drive-sync duplicate imports
    "[ACTION]",
)


def fetch_entries(cutoff_iso, exclude_after_iso=None):
    """Pull entries created after cutoff_iso (and optionally before exclude_after_iso),
    filtering out auto-generated content."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    sql = """
        SELECT id, type, title, content, url, topic, created_at
        FROM entries
        WHERE created_at >= ?
          AND coalesce(action_source, '') = ''
          AND coalesce(topic, '') != 'action_required'
    """
    params = [cutoff_iso]
    if exclude_after_iso:
        sql += " AND created_at < ?"
        params.append(exclude_after_iso)
    sql += " ORDER BY created_at DESC"

    rows = conn.execute(sql, params).fetchall()
    conn.close()

    kept = []
    for r in rows:
        title = r["title"] or ""
        if any(title.startswith(p) for p in AUTO_TITLE_PREFIXES):
            continue
        kept.append(dict(r))
    return kept


def format_recent_block(entries):
    """Format recent entries with truncated content for the prompt."""
    if not entries:
        return "No manually ingested entries in the last 7 days."
    lines = []
    for e in entries:
        date = e["created_at"][:10]
        topic = e["topic"] or "—"
        url = f"  URL: {e['url']}" if e.get("url") else ""
        content = (e["content"] or "").strip()
        if len(content) > MAX_CONTENT_CHARS:
            content = content[:MAX_CONTENT_CHARS] + " [...truncated]"
        lines.append(
            f"[{date}] [{e['type']}] [{topic}]\n"
            f"  Title: {e['title']}\n"
            f"{url}\n"
            f"  Content: {content}\n"
        )
    return "\n".join(lines)


def format_prior_block(entries, recent_entries):
    """For drift detection: topic-frequency comparison + sample titles per topic.

    The point is to let the LLM see which topics were active before vs now,
    not to dump 60 days of titles. We give counts + a few sample titles per
    topic, capped tight for prompt size.
    """
    if not entries:
        return "No prior entries available."

    # Count by topic in both windows
    prior_counts = {}
    prior_titles = {}
    for e in entries:
        topic = e["topic"] or "—"
        prior_counts[topic] = prior_counts.get(topic, 0) + 1
        prior_titles.setdefault(topic, []).append(f"[{e['created_at'][:10]}] {e['title']}")

    recent_topics = {(e["topic"] or "—") for e in recent_entries}

    lines = ["TOPIC | PRIOR (8-60d) | RECENT (7d) | STATUS"]
    for topic, count in sorted(prior_counts.items(), key=lambda x: -x[1]):
        recent_count = sum(1 for e in recent_entries if (e["topic"] or "—") == topic)
        status = "active" if recent_count > 0 else ("DRIFTED" if count >= 3 else "—")
        lines.append(f"{topic} | {count} | {recent_count} | {status}")

    # New-topics-this-week (in recent but not prior)
    new_topics = recent_topics - set(prior_counts.keys())
    if new_topics:
        lines.append("\nNew topics this week (not seen in prior 60d): " + ", ".join(sorted(new_topics)))

    # Sample titles for the top-3 prior topics so the LLM has concrete material
    lines.append("\nSample prior titles (top-3 most active topics):")
    for topic, _ in sorted(prior_counts.items(), key=lambda x: -x[1])[:3]:
        lines.append(f"\n## {topic}")
        for t in prior_titles[topic][:5]:
            lines.append(f"  {t}")

    return "\n".join(lines)


SYSTEM_PROMPT = """You are Ron Russo's Knowledge Synthesizer.

Ron runs Broadburch Partners (family office) and is also operationally involved at CINQCARE
(value-based healthcare for medically underserved populations). His Second Brain captures
material from BOTH worlds: investment research, CINQCARE/healthcare work, runbooks,
infrastructure notes, links, daily syntheses, and DD memos he writes.

Your job is NOT to re-analyze markets — he has separate market-analysis pipelines for that.
Your job is to be a thoughtful weekly mirror of HIS OWN knowledge intake. Read what he
saved this week, compare to prior weeks, and tell him what HE is learning, what's
surprising in HIS thinking, and what connects across the things HE saved.

Write 600-900 words. Be substantive and direct. No filler, no preamble, no disclaimers.
If a section has nothing real to say, say so in one sentence and move on — empty content
is honest signal.

Structure your response EXACTLY as follows:

## EMERGING THEMES
2-4 themes forming across multiple entries this week. For each theme, name it and cite
the specific entries (by title) that converge on it. A theme requires at least 2 entries
pointing the same direction. If you can't find 2+ entries on a theme, don't invent one.

## SURPRISES & CONTRADICTIONS
What did Ron save this week that surprised you given his prior thinking? What contradicts
something he wrote before? Cite specific entries on both sides. If nothing surprises,
say "Nothing materially surprising — this week's intake was consistent with prior weeks."

## CROSS-DOMAIN CONNECTIONS
Look for non-obvious connections across ANY of his material — not just CINQCARE x investing.
Things that LOOK unrelated but share an underlying mechanic, principle, structural pattern,
or downstream implication. Examples of what counts:
  - A space/defense article and an AI infrastructure note that both hinge on rare-earth supply
  - A real estate piece and a healthcare-policy piece that both turn on demographic shifts
  - A productivity/automation note and an investment thesis that both rest on the same unit-economics assumption
  - A CINQCARE operational insight that informs an investment view (or vice versa) — still valuable, just not the only axis
Be specific: name the connecting mechanism, cite both entries, and explain what the connection
implies (an opportunity, a risk, a question to investigate). Do not stretch — if material truly
doesn't connect, say so. The goal is genuine pattern recognition across his whole intake, not
manufactured links.

## WHAT'S DRIFTING
Compare last 7 days to the prior 8-60 day window. Which topics that he was actively
tracking before have gone quiet this week? Which topics newly appeared? Be specific:
"You wrote three CINQCARE community-health notes in mid-April and zero this week"
is useful. "Things changed" is not.

## QUESTIONS HE SHOULD BE ASKING
3-5 sharp questions that emerge from this week's material. Not generic ("what about risk?")
but specific to what he saved. The point is to surface lines of inquiry he might not have
articulated to himself yet.
"""


def synthesize(recent, prior, dry_run=False):
    user_content = (
        f"DATE: {datetime.now().strftime('%A, %B %d, %Y')}\n"
        f"WEEK COVERED: last {RECENT_DAYS} days\n"
        f"PRIOR WINDOW (for drift): days 8-{PRIOR_DAYS}\n\n"
        f"--- RECENT ENTRIES ({len(recent)} entries, last 7 days) ---\n"
        f"{format_recent_block(recent)}\n\n"
        f"--- PRIOR ACTIVITY (topic-frequency, for drift comparison) ---\n"
        f"{format_prior_block(prior, recent)}\n"
    )

    if dry_run:
        print("=" * 60)
        print("SYSTEM PROMPT:")
        print("=" * 60)
        print(SYSTEM_PROMPT)
        print()
        print("=" * 60)
        print("USER CONTENT:")
        print("=" * 60)
        print(user_content)
        return None

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=2500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    return response.content[0].text


def to_html(markdown_body, recent_count, prior_count):
    """Wrap the synthesis in a minimal Broadburch-style HTML email."""
    # crude markdown -> HTML for headers and paragraphs (no tables/lists needed for this output)
    lines = markdown_body.split("\n")
    out = []
    in_para = False
    for line in lines:
        line = line.rstrip()
        if line.startswith("## "):
            if in_para:
                out.append("</p>")
                in_para = False
            out.append(f'<h2 style="color:#1a3a5c;border-bottom:1px solid #ddd;padding-bottom:4px;margin-top:24px">{line[3:]}</h2>')
        elif not line:
            if in_para:
                out.append("</p>")
                in_para = False
        else:
            if not in_para:
                out.append('<p style="margin:8px 0;line-height:1.55">')
                in_para = True
            else:
                out.append("<br>")
            out.append(line)
    if in_para:
        out.append("</p>")
    body = "\n".join(out)

    today = datetime.now().strftime("%B %d, %Y")
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,Helvetica,Arial,sans-serif;color:#222;max-width:720px;margin:0 auto;padding:24px">
  <div style="border-bottom:2px solid #1a3a5c;padding-bottom:8px;margin-bottom:16px">
    <div style="font-size:11px;letter-spacing:1px;color:#1a3a5c">BROADBURCH PARTNERS</div>
    <div style="font-size:22px;font-weight:600">Weekly Knowledge Synthesis</div>
    <div style="font-size:13px;color:#777">{today} · {recent_count} new entries · {prior_count} prior in window</div>
  </div>
  {body}
  <div style="margin-top:32px;padding-top:12px;border-top:1px solid #eee;font-size:11px;color:#888">
    Source: ~/second-brain/second_brain.db · Generated by knowledge_synthesizer.py
  </div>
</body></html>"""


def file_to_second_brain(html, recent_count):
    """File the synthesis itself back into Second Brain as a document entry."""
    today = datetime.now().strftime("%Y-%m-%d")
    title = f"Weekly Knowledge Synthesis — {today}"
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO entries (type, title, content, topic) VALUES ('document', ?, ?, ?)",
        (title, html, "Knowledge Synthesis"),
    )
    conn.commit()
    conn.close()
    print(f"  Filed to Second Brain: {title}")


def main():
    parser = argparse.ArgumentParser(description="Weekly Knowledge Synthesizer")
    parser.add_argument("--no-email", action="store_true", help="Skip email delivery")
    parser.add_argument("--dry-run", action="store_true", help="Print prompt only, no LLM call")
    parser.add_argument("--stdout", action="store_true", help="Print synthesis to stdout, skip email/file")
    args = parser.parse_args()

    now = datetime.now()
    recent_cutoff = (now - timedelta(days=RECENT_DAYS)).isoformat(sep=" ")
    prior_cutoff = (now - timedelta(days=PRIOR_DAYS)).isoformat(sep=" ")

    recent = fetch_entries(recent_cutoff)
    prior = fetch_entries(prior_cutoff, exclude_after_iso=recent_cutoff)
    print(f"  Recent (last {RECENT_DAYS}d, post-filter): {len(recent)} entries")
    print(f"  Prior (8-{PRIOR_DAYS}d, post-filter): {len(prior)} entries")

    synthesis = synthesize(recent, prior, dry_run=args.dry_run)
    if synthesis is None:
        return  # dry-run

    if args.stdout:
        print(synthesis)
        return

    html = to_html(synthesis, len(recent), len(prior))

    if not args.no_email:
        subject = f"Weekly Knowledge Synthesis — {now.strftime('%B %d, %Y')}"
        send(to=None, subject=subject, html=html)
        print(f"  Email sent: {subject}")

    file_to_second_brain(html, len(recent))


if __name__ == "__main__":
    main()
