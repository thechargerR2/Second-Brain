# Second Brain

A personal knowledge base web app that stores notes, links, and documents in SQLite with AI-powered chat and summarization using Claude and Gemini.

## Features

- **Content Management** — Add, search, and delete notes, links, and document snippets
- **AI Chat** — Ask questions about your stored knowledge using Claude or Gemini (RAG-style)
- **AI Summarize** — Summarize any entry with your choice of AI provider

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

## Tech Stack

- **Flask** — Web framework
- **SQLite** — Database
- **Anthropic SDK** — Claude API
- **Google Generative AI SDK** — Gemini API

## Project Structure

```
├── app.py              # Flask routes and main entry point
├── database.py         # SQLite CRUD operations
├── ai_providers.py     # Claude & Gemini integration
├── templates/          # HTML templates
│   ├── base.html
│   ├── index.html
│   ├── add.html
│   └── chat.html
└── static/
    └── style.css
```
