# UPICON Website Chatbot

A bilingual (Hindi + English), voice-enabled chatbot for upicon.in that answers
questions from two content sources without ever telling the user which one it
used:

- **Static content** — every page in `sitemap.xml`, crawled with `crawl4ai`,
  chunked, embedded, and searched semantically (typo-tolerant, meaning-based
  search — not keyword matching).
- **Dynamic content** — live data from the UPICON API (`openings`,
  `initiatives`, `schemes`, `news`, `contents`, etc.), fetched on demand
  by keyword routing, *and* a snapshot of the same API data folded into
  the static index on every crawl (see `api_crawler.py`) so it's also
  reachable through ordinary semantic search even when a question's
  wording doesn't hit a routing keyword.

## How it decides static vs. dynamic

For every message:

1. **Chit-chat filter** (`intent.py`) — "hi", "thanks", "bye", "no/nothing"
   and their Hindi equivalents (with typo tolerance via `rapidfuzz`) are
   answered instantly with a canned bilingual reply. No retrieval, no LLM
   call — keeps the bot fast and avoids wasted API calls on non-questions.
2. **Routing** (`router.py`) — keyword rules decide whether the question
   likely needs a *live* API call (jobs, schemes, news, initiatives), and it
   **always** also runs a semantic search over the crawled static index. Both
   sets of results are merged into one flat list of plain-text snippets —
   with no labels, no URLs, no indication of where each snippet came from.
3. **Answer generation** (`llm.py`) — Groq's LLM receives only that
   plain-text context plus a system prompt that explicitly forbids citing
   sources, forbids answering off-topic questions, and instructs it to reply
   in whichever language (English/Hindi) the user just used.

This is why the bot can never accidentally reveal "I got this from the API"
or "this is from the crawled page" — the source label simply never reaches
the LLM or the user.

## Semantic search & typo tolerance

`vector_store.py` embeds every crawled chunk with a **multilingual**
sentence-transformer (`paraphrase-multilingual-MiniLM-L12-v2`), so:

- Misspelled or oddly-phrased queries still match the right content, because
  matching happens on meaning (vector similarity), not exact text.
- The same index serves both Hindi and English queries.
- Matches below `MIN_SIMILARITY` (see `config.py`) are dropped, so
  off-website questions correctly come back with "I don't have that
  information" instead of a hallucinated answer.

## Project layout

```
upicon-chatbot/
├── backend/
│   ├── main.py            FastAPI app — /chat, /voice, /health, /admin/reindex
│   ├── config.py          all settings (reads .env)
│   ├── crawler.py         sitemap.xml -> crawl4ai -> data/static_pages.json
│   │                      (also folds in api_crawler.py's snapshot)
│   ├── api_crawler.py     flattens every UPICON listing API action into
│   │                      chunk records, merged into static_pages.json
│   ├── build_index.py     embeds static_pages.json -> FAISS index
│   ├── vector_store.py    semantic search over the static index
│   ├── api_client.py      wraps every UPICON dynamic API action
│   ├── router.py          decides static vs dynamic vs both per query
│   ├── intent.py          greeting/thanks/goodbye/stopword detection
│   ├── llm.py             Groq chat completion + optional Whisper STT
│   ├── requirements.txt
│   ├── .env.example
│   └── data/              generated: static_pages.json, faiss.index, faiss_meta.pkl
└── frontend/
    ├── index.html         chat widget markup
    ├── style.css           navy + marigold bilingual UI
    └── script.js          chat logic, Web Speech API voice in/out
```

## Setup

### 1. Backend

```bash
cd backend
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium   # crawl4ai uses a headless browser to crawl

cp .env.example .env
# edit .env and set GROQ_API_KEY (free key: https://console.groq.com/keys)
```

Build the knowledge base (do this once, then re-run whenever the site
content changes — or hit `POST /admin/reindex` instead):

```bash
python crawler.py       # crawls sitemap.xml -> data/static_pages.json
python build_index.py   # embeds chunks -> data/faiss.index
```

Run the API:

```bash
uvicorn main:app --reload --port 8000
```

### 2. Frontend

Just open `frontend/index.html` in a browser, or serve it statically:

```bash
cd frontend
python -m http.server 5500
```

If your backend isn't on `localhost:8000`, update `API_BASE` at the top of
`script.js`. Also add your real frontend origin to `ALLOWED_ORIGINS` in
`backend/.env` for production (CORS).

To embed the widget on the live upicon.in site, drop the `frontend/` folder's
three files onto any page (or inline them into the site's template) — the
widget is self-contained and floats in the bottom-right corner.

## Voice input/output

- **Input**: the frontend uses the browser's native `SpeechRecognition` (Web
  Speech API) — works out of the box in Chrome/Edge, no audio upload needed,
  and supports both `en-IN` and `hi-IN` based on the language toggle.
- **Output**: replies are read aloud with `SpeechSynthesis` when the speaker
  icon is toggled on.
- **Optional server-side alternative**: `POST /voice` accepts an audio file
  and transcribes it with Groq's hosted Whisper (`llm.transcribe_audio`) —
  useful for browsers without Web Speech API support, or for a native mobile
  app instead of a web widget.

## Keeping content fresh

Static content only updates when you re-crawl. Either:
- schedule `python crawler.py && python build_index.py` on a cron (e.g.
  nightly), or
- call `POST /admin/reindex` (put this behind auth/internal network in
  production — it's unauthenticated as shipped).

Dynamic content (jobs, schemes, news, initiatives) is always fetched live
from the UPICON API on each relevant question, so it's never stale. A
snapshot of that same data is also baked into the static index on every
crawl/reindex (via `api_crawler.py`), purely so it's reachable through
semantic search too — the live fetch in `router.py` is still what keeps
answers current between re-crawls.

## Notes & things to double check before going live

- The UPICON API token in the reference doc is embedded in `config.py`'s
  default — move it to `.env` in any real deployment and treat it as a
  secret even though it was shared in plain text.
- `router.py`'s keyword lists are a starting point — extend them as you see
  which real user questions aren't triggering the right dynamic route.
- `_SESSIONS` in `main.py` is in-memory (per-process) — swap for Redis or a
  database before running multiple backend workers/instances.
- `/admin/reindex` and CORS `*` are convenient for development; lock both
  down before deploying publicly.
