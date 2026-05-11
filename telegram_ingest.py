#!/usr/bin/env python3
"""
Telegram → Second Brain Ingest Bot
Listens for links, screenshots, and /save commands from Ron's Telegram
and adds them to the Second Brain database.

Protocol:
  - Link message    → fetched, title + content saved as 'link' entry
  - Photo/screenshot → saved to documents/, OCR'd, saved as 'note' entry
  - /save <text>    → saved as 'note' entry
  - Plain text      → ignored
"""

import os
import sys
import re
import logging
import asyncio
from datetime import datetime

# Add second-brain to path for imports
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
import requests
from bs4 import BeautifulSoup
from auto_ingest import add_to_second_brain
from extract_text import extract_text

# --- Config ---
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_USER_ID = 8620038671
DOCUMENTS_DIR = os.path.join(BASE_DIR, "documents", "Telegram Captures")
LOG_FILE = os.path.join(BASE_DIR, "telegram_ingest.log")

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
# Silence libraries that log full request URLs (token appears in path).
for noisy in ("httpx", "httpcore", "telegram.request", "telegram._network"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger(__name__)

# Ensure documents directory exists
os.makedirs(DOCUMENTS_DIR, exist_ok=True)


def is_authorized(update: Update) -> bool:
    return update.effective_user and update.effective_user.id == ALLOWED_USER_ID


def extract_urls(text: str) -> list[str]:
    """Pull URLs from message text."""
    return re.findall(r'https?://[^\s<>"]+', text or "")


THIN_CONTENT_THRESHOLD = 500  # chars — below this, try Jina fallback


def fetch_page(url: str) -> tuple[str, str]:
    """Fetch a web page and return (title, readable text).

    Strategy:
      1. Try BeautifulSoup (fast, works for most sites)
      2. If content is thin (<500 chars), fall back to Jina Reader (renders JS)
    """
    title, text = _fetch_with_bs4(url)

    # If BeautifulSoup got thin content, try Jina Reader as fallback
    if len(text) < THIN_CONTENT_THRESHOLD:
        log.info(f"Thin content ({len(text)} chars) from BS4 for {url} — trying Jina Reader")
        jina_title, jina_text = _fetch_with_jina(url)
        if len(jina_text) > len(text):
            title = jina_title or title
            text = jina_text
            log.info(f"Jina Reader got {len(text)} chars for {url}")

    return title, text


def _fetch_with_bs4(url: str) -> tuple[str, str]:
    """Fetch with BeautifulSoup — fast, works for server-rendered HTML."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove script/style noise
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        title = soup.title.string.strip() if soup.title and soup.title.string else url
        # Get article body or fall back to all paragraph text
        article = soup.find("article")
        if article:
            text = article.get_text(separator="\n", strip=True)
        else:
            paragraphs = soup.find_all("p")
            text = "\n".join(p.get_text(strip=True) for p in paragraphs)

        # Truncate very long pages
        if len(text) > 15000:
            text = text[:15000] + "\n\n[Truncated]"

        return title, text
    except Exception as e:
        log.warning(f"BS4 fetch failed for {url}: {e}")
        return url, ""


def _fetch_with_jina(url: str) -> tuple[str, str]:
    """Fetch with Jina Reader — renders JS, handles SPAs and paywalled previews."""
    try:
        jina_url = f"https://r.jina.ai/{url}"
        headers = {
            "Accept": "text/plain",
            "X-Return-Format": "text",
        }
        resp = requests.get(jina_url, headers=headers, timeout=30)
        resp.raise_for_status()

        content = resp.text.strip()
        if not content:
            return "", ""

        # Jina returns markdown — first line is usually the title
        lines = content.split("\n", 1)
        title = lines[0].lstrip("# ").strip() if lines else url
        text = lines[1].strip() if len(lines) > 1 else content

        # Truncate very long pages
        if len(text) > 15000:
            text = text[:15000] + "\n\n[Truncated]"

        return title, text
    except Exception as e:
        log.warning(f"Jina Reader failed for {url}: {e}")
        return "", ""


# --- Handlers ---

CONNECTOR_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daily_connector", "connector.py")
PNL_RE = re.compile(r"p&l|pnl|day p.?l|how.*(doing|performing|portfolio)|what.*portfolio", re.IGNORECASE)


async def handle_synthesize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /synthesize command — run the Daily Connector on demand."""
    if not is_authorized(update):
        return

    await update.message.reply_text("Running synthesis... give me ~30s ⏳")

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, CONNECTOR_PATH,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
        output = stdout.decode(errors="replace")

        if proc.returncode == 0:
            # Extract new entry ID from log output
            entry_id = ""
            for line in output.splitlines():
                if "New entry ID:" in line:
                    entry_id = line.split("New entry ID:")[-1].strip()
                    break
            reply = f"✅ Synthesis complete — entry #{entry_id} written to Second Brain."
        else:
            last_lines = "\n".join(output.splitlines()[-10:])
            reply = f"❌ Connector failed (exit {proc.returncode}):\n```\n{last_lines}\n```"

        await update.message.reply_text(reply, parse_mode="Markdown")

    except asyncio.TimeoutError:
        await update.message.reply_text("❌ Timed out after 120s — check logs on the mini.")
    except Exception as exc:
        log.exception("handle_synthesize error")
        await update.message.reply_text(f"❌ Error: {exc}")


async def handle_pnl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Respond to P&L queries by reading the latest Portfolio_Tracker XLSX."""
    if not is_authorized(update):
        return
    try:
        import glob as _glob
        import openpyxl

        reports_dir = "/Users/ron/stock_screener/reports"
        files = sorted(_glob.glob(os.path.join(reports_dir, "Portfolio_Tracker_*.xlsx")), reverse=True)
        if not files:
            await update.message.reply_text("No portfolio report found. Run portfolio_refresh.py first.")
            return

        latest = files[0]
        report_date = os.path.basename(latest).replace("Portfolio_Tracker_", "").replace(".xlsx", "")

        wb = openpyxl.load_workbook(latest, data_only=True)
        ws = wb.active
        tot_val, tot_pnl = None, None
        for row in ws.iter_rows(values_only=True):
            if row[0] == "TOTAL":
                tot_pnl = row[3]   # P/L ($)
                tot_val = row[4]   # Value ($)
                break
        wb.close()

        if tot_val is None:
            await update.message.reply_text("Couldn't parse TOTAL row from report.")
            return

        pct = tot_pnl / tot_val if tot_val else 0
        arrow = "🟢" if tot_pnl >= 0 else "🔴"
        sign = "+" if tot_pnl >= 0 else ""
        msg = (f"{arrow} *Portfolio — {report_date}*\n"
               f"Value: ${tot_val:,.0f}\n"
               f"Day P&L: {sign}${tot_pnl:,.0f} ({sign}{pct:.2%})")
        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        log.exception("handle_pnl error")
        await update.message.reply_text(f"Error: {e}")


async def handle_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /save command — save text as a note."""
    if not is_authorized(update):
        return

    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text("Usage: /save your note text here")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    title = f"Note: {text[:60]}{'...' if len(text) > 60 else ''} ({today})"

    entry_id = add_to_second_brain(title, text, entry_type="note")
    if entry_id:
        log.info(f"Saved note #{entry_id}: {title}")
        await update.message.reply_text(f"Saved to Second Brain.\n#{entry_id}: {title}")
    else:
        await update.message.reply_text("Failed to save. Check logs.")


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle messages containing URLs."""
    if not is_authorized(update):
        return

    urls = extract_urls(update.message.text or update.message.caption or "")
    if not urls:
        return  # Plain text, no links — ignore

    for url in urls:
        title, content = fetch_page(url)
        today = datetime.now().strftime("%Y-%m-%d")
        full_title = f"{title} ({today})"

        entry_id = add_to_second_brain(
            full_title, content, url=url, entry_type="link", topic=""
        )
        if entry_id:
            log.info(f"Saved link #{entry_id}: {full_title} ({len(content)} chars)")
            reply = f"Saved to Second Brain.\n#{entry_id}: {title}"
            if len(content) < THIN_CONTENT_THRESHOLD:
                reply += f"\n\n⚠️ Low content ({len(content)} chars) — site may require login or uses heavy JS."
            else:
                reply += f"\n📄 {len(content):,} chars captured"
            await update.message.reply_text(reply)
        else:
            await update.message.reply_text(f"Already saved or failed: {title}")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photos/screenshots — save image and extract text."""
    if not is_authorized(update):
        return

    photo = update.message.photo[-1]  # Highest resolution
    file = await context.bot.get_file(photo.file_id)

    today = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    filename = f"telegram_{today}.jpg"
    filepath = os.path.join(DOCUMENTS_DIR, filename)
    await file.download_to_drive(filepath)

    # Build note content
    caption = update.message.caption or ""
    content = f"Screenshot captured from Telegram.\n"
    content += f"File: {filepath}\n"
    if caption:
        content += f"Caption: {caption}\n"

    # Check for URLs in caption
    urls = extract_urls(caption)
    url = urls[0] if urls else ""

    title_text = caption[:60] if caption else "Screenshot"
    title = f"{title_text} ({datetime.now().strftime('%Y-%m-%d')})"

    entry_id = add_to_second_brain(
        title, content, url=url, entry_type="note", topic=""
    )
    if entry_id:
        log.info(f"Saved photo #{entry_id}: {title}")
        await update.message.reply_text(f"Saved to Second Brain.\n#{entry_id}: {title}")
    else:
        await update.message.reply_text("Failed to save photo. Check logs.")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle files sent as documents (PDFs, full-res images, etc.)."""
    if not is_authorized(update):
        return

    doc = update.message.document
    file = await context.bot.get_file(doc.file_id)

    today = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    ext = os.path.splitext(doc.file_name or "file")[1] or ".bin"
    filename = f"telegram_{today}{ext}"
    filepath = os.path.join(DOCUMENTS_DIR, filename)
    await file.download_to_drive(filepath)

    caption = update.message.caption or doc.file_name or "Document"

    # Extract text content from supported document types (PDF, DOCX, XLSX, etc.)
    extracted = extract_text(filepath)

    content = f"[Document: {filepath}]\n"
    content += f"Original filename: {doc.file_name}\n"
    if caption and caption != doc.file_name:
        content += f"Caption: {caption}\n"
    if extracted:
        content += f"\n--- Extracted Text ---\n{extracted}\n"
    else:
        content += "\n[No text could be extracted from this file]\n"

    title_text = caption[:60] if caption else doc.file_name
    title = f"{title_text} ({datetime.now().strftime('%Y-%m-%d')})"

    entry_id = add_to_second_brain(
        title, content, entry_type="document", topic=""
    )
    if entry_id:
        log.info(f"Saved document #{entry_id}: {title}")
        await update.message.reply_text(f"Saved to Second Brain.\n#{entry_id}: {title}")
    else:
        await update.message.reply_text("Failed to save document. Check logs.")



async def handle_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /market TICKER — live quote via OpenBB."""
    if not is_authorized(update):
        return

    tickers = [t.upper() for t in context.args] if context.args else []
    if not tickers:
        await update.message.reply_text("Usage: /market RKLB\n/market RKLB ASTS CCJ")
        return

    symbol = ",".join(tickers)
    try:
        import requests as _req
        resp = _req.get(
            "http://127.0.0.1:6900/api/v1/equity/price/quote",
            params={"symbol": symbol, "provider": "yfinance"},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception as e:
        await update.message.reply_text(f"❌ OpenBB unavailable: {e}")
        return

    if not results:
        await update.message.reply_text(f"❌ No data for {symbol}")
        return

    lines = []
    for r in results:
        ticker  = r.get("symbol", "?")
        name    = r.get("name", "")
        price   = r.get("last_price")
        prev    = r.get("prev_close")
        low     = r.get("low")
        high    = r.get("high")
        yr_low  = r.get("year_low")
        yr_high = r.get("year_high")
        ma50    = r.get("ma_50d")
        ma200   = r.get("ma_200d")
        volume  = r.get("volume")

        if price and prev:
            chg = price - prev
            pct = chg / prev * 100
            arrow = "🟢" if chg >= 0 else "🔴"
            chg_str = f"{arrow} {'+' if chg>=0 else ''}{chg:.2f} ({'+' if pct>=0 else ''}{pct:.2f}%)"
        else:
            chg_str = "N/A"

        vol_str  = f"{volume/1e6:.1f}M" if volume else "N/A"
        ma50_str  = f"${ma50:,.2f}" if ma50 else "N/A"
        ma200_str = f"${ma200:,.2f}" if ma200 else "N/A"

        block = (
            f"*{ticker}* — {name}\n"
            f"💵 *${price:,.2f}*  {chg_str}\n"
            f"📊 Day: ${low:,.2f} – ${high:,.2f}\n"
            f"📅 52W: ${yr_low:,.2f} – ${yr_high:,.2f}\n"
            f"📈 50DMA: {ma50_str}  |  200DMA: {ma200_str}\n"
            f"📦 Vol: {vol_str}"
        )
        lines.append(block)

    await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")


async def handle_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    tickers = [t.upper() for t in context.args] if context.args else []
    if not tickers:
        await update.message.reply_text("Usage: /market RKLB or /market RKLB ASTS CCJ")
        return
    await update.message.reply_text("Fetching...")
    import requests as _req, sqlite3 as _sq, json as _json, anthropic as _ant
    blocks = []
    for ticker in tickers:
        try:
            r = _req.get("http://127.0.0.1:6900/api/v1/equity/price/quote",
                params={"symbol": ticker, "provider": "yfinance"}, timeout=10)
            quote = r.json().get("results", [{}])[0]
        except:
            quote = {}
        shares, avg_cost = None, None
        try:
            conn = _sq.connect("/Users/ron/stock_dashboard/backend/data/portfolio.db")
            row = conn.execute("SELECT SUM(shares), SUM(shares*avg_cost)/SUM(shares) FROM positions WHERE ticker=? AND shares>0", (ticker,)).fetchone()
            conn.close()
            if row and row[0]:
                shares = float(row[0])
                avg_cost = float(row[1]) if row[1] else None
        except:
            pass
        thesis = ""
        try:
            sb = _req.get("http://localhost:5001/search", params={"q": ticker, "limit": 2}, timeout=5)
            for e in sb.json().get("results", []):
                thesis += e.get("title","") + ": " + e.get("content","")[:300] + "\n"
        except:
            pass
        try:
            with open("/Users/ron/stock_screener/investment_theses.json") as f:
                for t in _json.load(f).get("theses", []):
                    if ticker in [w.upper() for w in t.get("watchlist",[])] or ticker in t.get("name","").upper():
                        thesis += "Thesis: " + t["name"] + " - " + t.get("summary","")[:400]
                        break
        except:
            pass
        price = quote.get("last_price")
        prev = quote.get("prev_close")
        chg = (price - prev) if price and prev else None
        pct = (chg / prev * 100) if chg and prev else None
        pos_str = "No position on file."
        if shares and shares > 0:
            pos_str = str(int(shares)) + " shares"
            if avg_cost and price:
                mv = shares * price
                cb = shares * avg_cost
                pl = mv - cb
                plp = pl / cb * 100
                sign = "+" if pl >= 0 else ""
                pos_str += " @ $" + f"{avg_cost:.2f}" + " avg\nMkt val: $" + f"{mv:,.0f}" + "\nP&L: " + sign + "$" + f"{pl:,.0f}" + " (" + sign + f"{plp:.1f}%)"
        mkt = "Ticker: " + ticker + "\nName: " + quote.get("name", ticker)
        if price: mkt += "\nPrice: $" + f"{price:,.2f}"
        if chg is not None: mkt += "\nChange: " + ("+" if chg>=0 else "") + f"{chg:.2f} (" + ("+" if pct>=0 else "") + f"{pct:.2f}%)"
        if quote.get("low"): mkt += "\nDay: $" + f"{quote['low']:,.2f}" + " - $" + f"{quote['high']:,.2f}"
        if quote.get("year_low"): mkt += "\n52W: $" + f"{quote['year_low']:,.2f}" + " - $" + f"{quote['year_high']:,.2f}"
        if quote.get("ma_50d"): mkt += "\n50DMA: $" + f"{quote['ma_50d']:,.2f}" + " | 200DMA: $" + f"{quote.get('ma_200d',0):,.2f}"
        if quote.get("volume"): mkt += "\nVol: " + f"{quote['volume']/1e6:.1f}M"
        try:
            client = _ant.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY",""))
            prompt = ("You are a concise investment assistant for Ron at Broadburch Capital.\n\n"
                "MARKET DATA:\n" + mkt + "\n\nPOSITION:\n" + pos_str + "\n\nTHESIS:\n" + (thesis or "None on file.") + "\n\n"
                "Write a SHORT Telegram message. Include: price + day change with arrow emoji, "
                "position and P&L if held, one-line technical read vs MAs, "
                "2-3 sentence thesis reminder and near-term catalysts. "
                "Under 200 words. Plain text, minimal formatting.")
            resp = client.messages.create(model="claude-sonnet-4-5", max_tokens=400,
                messages=[{"role":"user","content":prompt}])
            blocks.append(resp.content[0].text)
        except Exception as e:
            blocks.append(ticker + ": Claude error - " + str(e))
    await update.message.reply_text("\n\n---\n\n".join(blocks))

def main():
    log.info("Starting Telegram → Second Brain ingest bot...")
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # /synthesize command — run Daily Connector on demand
    app.add_handler(CommandHandler("synthesize", handle_synthesize))

    # /save command
    app.add_handler(CommandHandler("save", handle_save))
    app.add_handler(CommandHandler("market", handle_market))

    # Photos
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # Documents (PDFs, files sent as documents)
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # P&L queries — must come before handle_link
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(PNL_RE), handle_pnl))

    # Text messages with links (must come after command handlers)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))

    log.info("Bot is running. Listening for messages...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
