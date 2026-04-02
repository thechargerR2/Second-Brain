# Second Brain

Personal knowledge base — Flask + SQLite + AI (Claude & Gemini) + MCP server.

## Quick Reference

| Command | What it does |
|---------|-------------|
| `.venv/bin/python3 server.py` | Start combined Flask + MCP server on port 5001 |
| `./start_mcp.sh` | Start server + Cloudflare tunnel |
| `.venv/bin/python3 organize_documents.py` | Sync & categorize files from stock_screener/reports/ |
| `.venv/bin/python3 organize_topics.py` | Categorize DB entries by topic |
| `.venv/bin/python3 gmail_inbox_importer.py` | Import emails into knowledge base |

## Architecture

- **Combined server** (`server.py`): Flask web UI + MCP on single port 5001 via uvicorn/Starlette
- **Permanent URL**: `https://brain.broadburch.dev`
  - Web UI: `/` | Voice: `/voice` | Siri: `/siri` | MCP: `/mcp` | API: `/api/chat`, `/api/add`
- **DB**: `second_brain.db` — SQLite, single `entries` table (type, title, content, url, created_at)
- **MCP Tools**: search_notes, get_entry, browse_by_topic, list_recent, get_stats, add_note
- **Venv**: `.venv/` (Python 3.12)

## Key Patterns

- Entry types: `note`, `link`, `document` (CHECK constraint)
- Chat uses simple RAG: search by question text, top 10 as context to AI
- AI provider selectable per-request (claude or gemini)
- All DB functions open/close own connections (no pooling)
- Parameterized queries throughout (SQL injection safe)

## Documents Folder

`documents/` contains all co-created files organized into topic subfolders:
- CINQCARE & Healthcare
- Due Diligence Memos
- Portfolio & Strategy
- Space & Infrastructure
- Robotics & AI
- Real Estate & Government
- Investment Research

**Convention:** Always copy new files here when creating documents anywhere on the system.

## LaunchAgents

| Plist | Schedule | Script |
|-------|----------|--------|
| `com.broadburch.secondbrain-mcp.plist` | Boot | `start_mcp.sh` (server + Cloudflare tunnel) |
| `com.broadburch.organize-documents.plist` | Sundays 7 AM | `organize_documents.py` |
