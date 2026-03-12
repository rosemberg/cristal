# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Cristal** is "Transparência Chat" — an AI-powered chatbot for the TRE-PI (Tribunal Regional Eleitoral do Piauí) transparency portal. It allows citizens to query public transparency data via natural language, returning relevant links, extracted page content, and follow-up suggestions.

- **LLM:** Gemini 2.5 Flash Lite via Vertex AI
- **Backend:** Python + FastAPI
- **Frontend:** Vanilla HTML5/CSS3/JS (no CSS frameworks)
- **Deploy:** OpenShift 4.x (on-premise), containerized with `uvicorn`
- **GitLab remote:** `https://gitlab2.tre-pi.jus.br/rosemberg.maia/cristal.git`

## Architecture

```
Frontend (single-page chat UI)
  → POST /api/chat, GET /api/health, GET /api/suggest
    → Backend (FastAPI)
      → KnowledgeBase (in-memory index from SKILL.md/knowledge.md, keyword search)
      → ContentFetcher (httpx + BeautifulSoup4, scrapes tre-pi.jus.br pages)
      → VertexClient (google-cloud-aiplatform SDK → Gemini)
      → ChatEngine (orchestrates: KB search → content fetch → prompt build → LLM call → format response)
```

LLM responses are structured JSON with `text`, `links`, `extracted_content`, and `suggestions` fields. The frontend renders these as styled cards with accordions, link chips, and suggestion buttons.

## Project Structure (planned)

```
app/
├── main.py              # FastAPI app, CORS, static files
├── config.py            # Env vars, GCP project config
├── routers/chat.py      # API endpoints
├── services/
│   ├── knowledge_base.py    # Loads/indexes knowledge.md, keyword search
│   ├── chat_engine.py       # Orchestration: KB → fetcher → LLM → response
│   ├── content_fetcher.py   # Web scraping with 1h cache, domain-restricted
│   └── vertex_client.py     # Vertex AI / Gemini client
├── models/schemas.py    # Pydantic models (ChatRequest, ChatResponse, LinkItem)
├── prompts/system_prompt.py  # System prompt template
└── data/knowledge.md    # Knowledge base (copy of SKILL.md)
static/                  # Frontend: index.html, css/style.css, js/chat.js, assets/
```

## Build & Run

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Container build:
```bash
podman build -f Containerfile -t cristal .
```

## Key Environment Variables

See `.env.example`. Critical ones: `VERTEX_PROJECT_ID`, `VERTEX_LOCATION`, `VERTEX_MODEL`, `GOOGLE_APPLICATION_CREDENTIALS`, `ALLOWED_ORIGINS`.

## Development Rules

- All backend code uses `async/await`
- Content Fetcher only scrapes `tre-pi.jus.br` domain, with 10s timeout and 1h cache
- Rate limit: 10 requests/min per IP on `/api/chat`
- CORS: `*` in dev, TRE-PI domain only in production
- Frontend: pure CSS following the institutional color palette (`#006B5F` primary, `#00897B` accent, `#F1F8E9` content background)
- Gemini responses expected as JSON; implement regex fallback for malformed JSON
- HTML content ≤3000 chars: extract and display inline. Longer content, PDFs, videos: show link only with access instructions
- Implementation order: backend modules first (config → schemas → knowledge_base → vertex_client → content_fetcher → chat_engine → router → main), then frontend, then deploy artifacts

## Testing

```bash
pytest
```

Unit tests with mocks for `knowledge_base.py` and `content_fetcher.py`. Integration tests against Vertex AI in staging. E2E with Cypress or Playwright.
