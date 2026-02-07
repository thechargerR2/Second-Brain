import os
import anthropic
import google.generativeai as genai


def _get_claude_client():
    return anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


def _configure_gemini():
    genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))


def chat_with_claude(context, question):
    client = _get_claude_client()
    system_prompt = (
        "You are a helpful assistant for a personal knowledge base. "
        "Answer the user's question based on the following stored content. "
        "If the content doesn't contain relevant information, say so.\n\n"
        f"--- Stored Content ---\n{context}"
    )
    message = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": question}],
    )
    return message.content[0].text


def chat_with_gemini(context, question):
    _configure_gemini()
    model = genai.GenerativeModel("gemini-2.0-flash")
    prompt = (
        "You are a helpful assistant for a personal knowledge base. "
        "Answer the user's question based on the following stored content. "
        "If the content doesn't contain relevant information, say so.\n\n"
        f"--- Stored Content ---\n{context}\n\n"
        f"--- Question ---\n{question}"
    )
    response = model.generate_content(prompt)
    return response.text


def summarize_with_claude(text):
    client = _get_claude_client()
    message = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": (
                    "Summarize the following content concisely:\n\n" + text
                ),
            }
        ],
    )
    return message.content[0].text


def summarize_with_gemini(text):
    _configure_gemini()
    model = genai.GenerativeModel("gemini-2.0-flash")
    response = model.generate_content(
        "Summarize the following content concisely:\n\n" + text
    )
    return response.text
