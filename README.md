# Second Brain

A personal knowledge base web app that stores notes, links, and documents in SQLite with AI-powered chat and summarization using Claude and Gemini.

## Features

- **Content Management** — Add, search, and delete notes, links, and document snippets
- **AI Chat** — Ask questions about your stored knowledge using Claude or Gemini (RAG-style)
- **AI Summarize** — Summarize any entry with your choice of AI provider
- **Google Drive Sync** — Sync entries to Google Drive as Google Docs for access via Gemini Live

## Setup

1. Install dependencies:
   ```bash
   pip3 install -r requirements.txt
   ```

2. Create a `.env` file from the template and add your API keys:
   ```bash
   cp .env.example .env
   ```

3. Run the app:
   ```bash
   python3 app.py
   ```

4. Open http://localhost:5001

## Google Drive Sync Setup

Sync your entries to Google Drive as Google Docs so you can query them via Gemini Live on your phone.

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project and enable the **Google Drive API**
3. Create **OAuth client ID** credentials (Desktop app type)
4. Download the JSON and save as `credentials.json` in the project root
5. Restart the app and click **Connect Google Drive** on the dashboard
6. Complete the OAuth consent flow in your browser
7. Click **Sync All to Google Drive** to push all entries

Entries are synced as native Google Docs in a "Second Brain" folder. New entries auto-sync on add, and deleted entries are trashed in Drive.

## Tech Stack

- **Flask** — Web framework
- **SQLite** — Database
- **Anthropic SDK** — Claude API
- **Google Generative AI SDK** — Gemini API
- **Google Drive API** — Drive sync

## Project Structure

```
├── app.py              # Flask routes and main entry point
├── database.py         # SQLite CRUD operations
├── ai_providers.py     # Claude & Gemini integration
├── drive_sync.py       # Google Drive sync module
├── templates/          # HTML templates
│   ├── base.html
│   ├── index.html
│   ├── add.html
│   └── chat.html
└── static/
    └── style.css
```
