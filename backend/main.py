import os
import tempfile
import uuid

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import cache
import config
import faq
import intent
import llm
import router
import spellcheck
import vector_store

app = FastAPI(title="UPICON Chatbot API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# very small in-memory per-session history store (swap for redis/db in prod)
_SESSIONS: dict[str, list[dict]] = {}
# locks the language chosen at the start of each session so replies don't
# drift mid-conversation even if a later message is typed in the other language
_SESSION_LANG: dict[str, str] = {}


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    lang: str | None = None  # "en" | "hi" — optional hint from the frontend toggle


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    intent: str  # "chitchat" | "answer"


def _detect_lang(text: str, hint: str | None) -> str:
    if hint in ("en", "hi"):
        return hint
    # crude Devanagari check as a fallback when no explicit toggle is sent
    if any("\u0900" <= ch <= "\u097F" for ch in text):
        return "hi"
    return "en"


def _resolve_session_lang(session_id: str, message: str, hint: str | None) -> str:
    """The frontend's header toggle always sends an explicit "en"/"hi" hint,
    so that always wins and updates the session (letting a visitor switch
    languages mid-conversation). Only when no hint is sent at all do we fall
    back to whatever was locked in earlier, or detect it from the message."""
    if hint in ("en", "hi"):
        _SESSION_LANG[session_id] = hint
        return hint
    if session_id in _SESSION_LANG:
        return _SESSION_LANG[session_id]
    lang = _detect_lang(message, hint)
    _SESSION_LANG[session_id] = lang
    return lang


@app.get("/health")
async def health():
    return {"status": "ok", "index_ready": vector_store.is_index_ready()}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    print("STEP 1: got request:", req.message, flush=True)

    session_id = req.session_id or str(uuid.uuid4())
    history = _SESSIONS.setdefault(session_id, [])
    lang = _resolve_session_lang(session_id, req.message, req.lang)

    # 1) chit-chat / stopword short-circuit — no retrieval, no LLM call
    label = intent.detect(req.message)
    if label is not None:
        print("STEP chitchat matched:", label, flush=True)
        reply = intent.canned_reply(label, lang)
        history.append({"role": "user", "content": req.message})
        history.append({"role": "assistant", "content": reply})
        return ChatResponse(reply=reply, session_id=session_id, intent="chitchat")

    # 2) correct likely typos against real site vocabulary before matching -
    # visitors often mistype scheme/initiative names. Only the corrected
    # copy is used for matching/the LLM; what they actually typed is still
    # what gets stored in history below.
    corrected = spellcheck.correct_query(req.message)
    if corrected != req.message:
        print(f"STEP spellcheck: {req.message!r} -> {corrected!r}", flush=True)

    # 3) common static FAQ (e.g. "what is UPICON", "contact details") — a
    # canned answer, no retrieval, no Groq call
    faq_answer = faq.match(corrected)
    if faq_answer is not None:
        print("STEP faq matched", flush=True)
        reply = faq.canned_reply(faq_answer, lang)
        history.append({"role": "user", "content": req.message})
        history.append({"role": "assistant", "content": reply})
        return ChatResponse(reply=reply, session_id=session_id, intent="faq")

    # 4) real question — gather static + dynamic context, then ask the LLM
    print("STEP 2: calling router.gather_context", flush=True)
    context_snippets = await router.gather_context(corrected)
    print("STEP 3: router done, got", len(context_snippets), "snippets", flush=True)

    print("STEP 4: calling llm.generate_answer", flush=True)
    reply = llm.generate_answer(corrected, context_snippets, history, lang)
    print("STEP 5: llm done", flush=True)

    history.append({"role": "user", "content": req.message})
    history.append({"role": "assistant", "content": reply})

    print("STEP 6: returning response", flush=True)
    return ChatResponse(reply=reply, session_id=session_id, intent="answer")


@app.post("/voice")
async def voice(
    audio: UploadFile = File(...),
    session_id: str | None = Form(None),
    lang: str | None = Form(None),
):
    """Optional server-side transcription path (Groq Whisper) for clients
    that can't use the browser's built-in speech recognition."""
    suffix = os.path.splitext(audio.filename or "audio.webm")[1] or ".webm"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await audio.read())
        tmp_path = tmp.name

    try:
        # once a session already has a locked language, transcribe in that
        # language rather than whatever hint this particular call sent
        effective_session = session_id or ""
        locked = _SESSION_LANG.get(effective_session)
        language_hint = locked or (lang if lang in ("en", "hi") else None)
        transcript = llm.transcribe_audio(tmp_path, language_hint)
    finally:
        os.unlink(tmp_path)

    chat_result = await chat(
        ChatRequest(message=transcript, session_id=session_id, lang=lang)
    )
    return {"transcript": transcript, **chat_result.model_dump()}


@app.post("/admin/reindex")
async def reindex():
    """Convenience endpoint to re-crawl + re-embed without shelling in.
    Protect this behind auth / an internal network in production."""
    import asyncio

    from crawler import run_full_crawl

    await run_full_crawl()
    await asyncio.to_thread(vector_store.build_index)
    spellcheck.refresh_vocab()
    # Only drop the dynamic-API cache (jobs/schemes/news/...) - those are
    # keyed by action name, so a stale entry could otherwise outlive a real
    # content change. Chat-answer cache is left alone: its key already
    # includes the retrieved context text, so unchanged pages keep hitting
    # cache (no wasted Groq calls) and only entries for genuinely changed
    # content stop matching and quietly expire via their own TTL.
    cache.clear(namespace="dynamic")
    return {"status": "reindexed"}