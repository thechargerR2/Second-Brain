import os
from flask import Flask, render_template, request, redirect, url_for, flash
from dotenv import load_dotenv
from database import init_db, add_entry, get_all_entries, get_entry, delete_entry
from ai_providers import (
    chat_with_claude,
    chat_with_gemini,
    summarize_with_claude,
    summarize_with_gemini,
)

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")

init_db()


@app.route("/")
def index():
    search = request.args.get("search", "")
    entries = get_all_entries(search if search else None)
    return render_template("index.html", entries=entries, search=search)


@app.route("/add", methods=["GET", "POST"])
def add():
    if request.method == "POST":
        entry_type = request.form["type"]
        title = request.form["title"]
        content = request.form.get("content", "")
        url = request.form.get("url", "")
        if not title:
            flash("Title is required.", "error")
            return render_template("add.html")
        add_entry(entry_type, title, content, url)
        flash("Entry added.", "success")
        return redirect(url_for("index"))
    return render_template("add.html")


@app.route("/delete/<int:entry_id>", methods=["POST"])
def delete(entry_id):
    delete_entry(entry_id)
    flash("Entry deleted.", "success")
    return redirect(url_for("index"))


@app.route("/chat", methods=["GET", "POST"])
def chat():
    answer = None
    question = ""
    provider = "claude"
    if request.method == "POST":
        question = request.form["question"]
        provider = request.form.get("provider", "claude")
        entries = get_all_entries(question)
        if not entries:
            entries = get_all_entries()
        context = "\n\n".join(
            f"[{e['type'].upper()}] {e['title']}\n{e['content']}"
            + (f"\nURL: {e['url']}" if e["url"] else "")
            for e in entries[:10]
        )
        try:
            if provider == "gemini":
                answer = chat_with_gemini(context, question)
            else:
                answer = chat_with_claude(context, question)
        except Exception as e:
            answer = f"Error from {provider}: {e}"
    return render_template(
        "chat.html", answer=answer, question=question, provider=provider
    )


@app.route("/summarize/<int:entry_id>", methods=["POST"])
def summarize(entry_id):
    entry = get_entry(entry_id)
    if not entry:
        flash("Entry not found.", "error")
        return redirect(url_for("index"))
    provider = request.form.get("provider", "claude")
    text = f"{entry['title']}\n\n{entry['content']}"
    if entry["url"]:
        text += f"\n\nURL: {entry['url']}"
    try:
        if provider == "gemini":
            summary = summarize_with_gemini(text)
        else:
            summary = summarize_with_claude(text)
        flash(f"Summary ({provider}): {summary}", "info")
    except Exception as e:
        flash(f"Error from {provider}: {e}", "error")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True, port=5001)
