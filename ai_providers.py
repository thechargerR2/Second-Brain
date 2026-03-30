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


DD_MEMO_PROMPT = """\
You are a senior equity research analyst. Write a comprehensive investment due diligence memo on {ticker} ({company_name}).

Use the following structure with markdown formatting:

# Investment DD Memo: {ticker} ({company_name})

## Executive Summary
A 2-3 sentence investment thesis summary with a clear BUY / HOLD / SELL recommendation.

## Company Overview
- Business description, industry, headquarters, key segments
- Market cap, stock price, exchange/ticker

## Investment Thesis
- 3-5 bullet points on why this is (or isn't) a compelling investment

## Business Model & Competitive Position
- Revenue streams and segments
- Competitive advantages (moat)
- Key customers, contracts, market share

## Financial Analysis
- Revenue, earnings, margins (recent trends)
- Balance sheet strength (cash, debt)
- Cash flow generation
- Key financial ratios (P/E, P/S, EV/EBITDA)

## Growth Drivers & Catalysts
- Near-term catalysts (next 6-12 months)
- Long-term secular tailwinds
- Expansion plans, new markets, capacity growth

## Risk Factors
- Company-specific risks
- Industry/macro risks
- Regulatory, geopolitical, or ESG risks

## Valuation
- Current valuation vs. peers and historical range
- Price target rationale
- Analyst consensus

## Conclusion & Recommendation
- Final verdict with conviction level (High / Medium / Low)
- Key metrics to monitor going forward

---
*Memo generated on {date}. This is for informational purposes only and does not constitute investment advice.*

Be thorough, data-driven, and specific with numbers. Use the latest available information you have.
"""


def generate_dd_memo_claude(ticker, company_name, date):
    client = _get_claude_client()
    prompt = DD_MEMO_PROMPT.format(ticker=ticker, company_name=company_name, date=date)
    message = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def generate_dd_memo_gemini(ticker, company_name, date):
    _configure_gemini()
    model = genai.GenerativeModel("gemini-2.0-flash")
    prompt = DD_MEMO_PROMPT.format(ticker=ticker, company_name=company_name, date=date)
    response = model.generate_content(prompt)
    return response.text
