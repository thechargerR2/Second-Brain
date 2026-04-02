#!/usr/bin/env python3
"""
BBIngestBot Health Check — ensures the Telegram ingest bot is alive and responsive.

Checks:
  1. Process is running
  2. Bot can reach Telegram API (getMe)
  3. No prolonged gap since last successful message processing

If unhealthy, restarts the bot via launchctl and sends a Telegram notification.
Designed to run daily via LaunchAgent.
"""

import os
import sys
import subprocess
import logging
import re
from datetime import datetime, timedelta

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOG_DIR, "healthcheck_ingest.log")
INGEST_LOG = os.path.join(BASE_DIR, "telegram_ingest.log")
PLIST = os.path.expanduser("~/Library/LaunchAgents/com.broadburch.telegram-ingest.plist")

BOT_TOKEN = "8763635224:AAE4p-Hd5zUhypLtT8Idy3pev76nSOgGhgs"
CHAT_ID = 8620038671
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# If no messages processed in this many days, consider unhealthy
STALE_THRESHOLD_DAYS = 3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("healthcheck")


def send_notification(text: str):
    """Send a health check notification to Telegram."""
    try:
        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": True},
            timeout=10,
        )
    except Exception as e:
        log.error(f"Failed to send notification: {e}")


def is_process_running() -> bool:
    """Check if the ingest bot process is running."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "telegram_ingest.py"],
            capture_output=True, text=True,
        )
        return result.returncode == 0
    except Exception:
        return False


def can_reach_api() -> bool:
    """Check if the bot token is valid and API is reachable."""
    try:
        resp = requests.get(f"{TELEGRAM_API}/getMe", timeout=10)
        return resp.status_code == 200 and resp.json().get("ok")
    except Exception:
        return False


def last_processed_message() -> datetime | None:
    """Find the timestamp of the last successfully processed message from logs."""
    if not os.path.exists(INGEST_LOG):
        return None

    # Look for "Saved ..." entries which indicate successful processing
    last_ts = None
    pattern = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
    try:
        with open(INGEST_LOG, "r") as f:
            for line in f:
                if "Saved " in line:
                    match = pattern.match(line)
                    if match:
                        last_ts = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
    except Exception as e:
        log.error(f"Failed to read ingest log: {e}")
    return last_ts


def restart_bot():
    """Restart the ingest bot via launchctl."""
    log.info("Restarting ingest bot...")
    try:
        subprocess.run(["launchctl", "unload", PLIST], capture_output=True, timeout=10)
        subprocess.run(["launchctl", "load", PLIST], capture_output=True, timeout=10)
        log.info("Restart complete")
    except Exception as e:
        log.error(f"Restart failed: {e}")


def main():
    log.info("Running BBIngestBot health check...")
    issues = []
    restarted = False

    # Check 1: Process running
    if not is_process_running():
        issues.append("Process not running")
        log.warning("Ingest bot process is not running — restarting")
        restart_bot()
        restarted = True

    # Check 2: API reachable
    if not can_reach_api():
        issues.append("Cannot reach Telegram API")
        log.warning("Telegram API unreachable")

    # Check 3: Stale processing
    last_msg = last_processed_message()
    if last_msg:
        days_since = (datetime.now() - last_msg).days
        if days_since >= STALE_THRESHOLD_DAYS:
            issues.append(f"No messages processed in {days_since} days (last: {last_msg.strftime('%b %d')})")
            log.warning(f"Last processed message: {last_msg} ({days_since} days ago)")
            if not restarted:
                restart_bot()
                restarted = True
    else:
        issues.append("No processing history found in logs")

    # Report
    if issues:
        msg = "🔧 BBIngestBot Health Check\n"
        msg += "─" * 28 + "\n\n"
        for issue in issues:
            msg += f"⚠️ {issue}\n"
        if restarted:
            msg += "\n✅ Bot was restarted automatically."
        send_notification(msg)
        log.info(f"Health check: {len(issues)} issue(s) found")
    else:
        log.info("Health check: all good")

    return len(issues)


if __name__ == "__main__":
    sys.exit(main())
