# Second Brain

Personal knowledge base web app — Flask + SQLite + AI (Claude & Gemini).

## Quick Reference

- **Run:** `python3 app.py` → http://localhost:5001
- **Install deps:** `pip3 install -r requirements.txt`
- **Config:** Copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `SECRET_KEY`

## Architecture

- `app.py` — Flask routes: index (search/list), add, delete, chat (RAG), summarize
- `database.py` — SQLite CRUD via `second_brain.db`, single `entries` table (type, title, content, url, created_at)
- `ai_providers.py` — Claude (claude-sonnet-4-5-20250929) and Gemini (gemini-2.0-flash) for chat + summarization
- `drive_sync.py` — Google Drive sync: OAuth2 auth, uploads entries as Google Docs, manages "Second Brain" folder
- `templates/` — Jinja2 templates extending `base.html`
- `static/style.css` — Styles

## Key Patterns

- Entry types are constrained to: `note`, `link`, `document` (enforced via CHECK constraint)
- Chat uses simple RAG: searches entries by question text, passes top 10 as context to the AI
- All DB functions open/close their own connections (no connection pooling)
- Parameterized queries used throughout (SQL injection safe)
- AI provider is selectable per-request (claude or gemini)
- Google Drive sync uses `drive.file` scope (app can only access files it created)
- Entries uploaded as HTML, converted to native Google Docs for Gemini indexing
- Drive file IDs stored in `drive_file_id` column on entries table
- OAuth tokens stored in `token.json`, credentials in `credentials.json` (both gitignored)
- Auto-sync on add/delete, manual "Sync All" for full reconciliation

## Environment

- Python 3
- Dependencies: flask, anthropic, google-generativeai, python-dotenv, google-api-python-client, google-auth-httplib2, google-auth-oauthlib
- DB file (`second_brain.db`) and `.env` are gitignored
