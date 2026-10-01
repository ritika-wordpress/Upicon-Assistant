import json
import os
import re
import tempfile
import uuid

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import cache
import config
import faq
import intent
import llm
import moderation
import query_rewrite
import router
import sections
import spotlight
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
    extra_replies: list[str] = []  # follow-up messages (top item, link) for section questions


async def _get_context(rewritten: str, corrected: str) -> list[str]:
    """Retrieve with the cleaned-up question; if that finds nothing, retry
    with the spell-corrected original wording."""
    ctx = await router.gather_context(rewritten)
    if not ctx and rewritten != corrected:
        ctx = await router.gather_context(corrected)
    return ctx


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

    # 0) abusive / offensive language - politely ask for appropriate language
    if moderation.is_abusive(req.message):
        print("STEP moderation: abusive message", flush=True)
        return ChatResponse(reply=moderation.reply(lang), session_id=session_id, intent="moderated")

    # 0b) Spotlight chip - one headline each from ODOP Product of the Month,
    # Success Stories and UP Ke Karigar, straight from the API (no LLM)
    if spotlight.is_spotlight_query(req.message):
        spot = await spotlight.build_messages(lang)
        if spot:
            print("STEP spotlight answered", flush=True)
            history.append({"role": "user", "content": req.message})
            history.append({"role": "assistant", "content": "\n\n".join(spot)})
            return ChatResponse(
                reply=spot[0], session_id=session_id, intent="spotlight", extra_replies=spot[1:]
            )

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
    rewritten = await query_rewrite.rewrite(corrected, history)
    section_key = sections.match(corrected) or sections.match(req.message)
    if rewritten == query_rewrite.OFF_TOPIC and section_key:
        rewritten = corrected  # a known site section is never off-topic
    if rewritten == query_rewrite.OFF_TOPIC:
        # clearly not about UPICON - politely say what this assistant is for
        reply = llm.out_of_scope_reply(lang)
        history.append({"role": "user", "content": req.message})
        history.append({"role": "assistant", "content": reply})
        return ChatResponse(reply=reply, session_id=session_id, intent="off_topic")
    print("STEP 2: calling router.gather_context", flush=True)
    context_snippets = await _get_context(rewritten, corrected)
    print("STEP 3: router done, got", len(context_snippets), "snippets", flush=True)

    print("STEP 4: calling llm.generate_answer", flush=True)
    reply = llm.generate_answer(rewritten, context_snippets, history, lang, original=req.message)
    print("STEP 5: llm done", flush=True)

    chunks = _split_parts(reply)
    reply, extras = chunks[0], chunks[1:]
    if section_key:
        extras += await sections.follow_up_messages(section_key, lang)

    history.append({"role": "user", "content": req.message})
    history.append({"role": "assistant", "content": "\n\n".join([reply] + extras)})

    print("STEP 6: returning response", flush=True)
    return ChatResponse(reply=reply, session_id=session_id, intent="answer", extra_replies=extras)


_BLANK_LINE_RE = re.compile(r"\n[ \t]*\n")


def _split_parts(text: str) -> list[str]:
    """Split a finished reply into short chat messages at blank lines."""
    parts = [p.strip() for p in _BLANK_LINE_RE.split(text or "")]
    return [p for p in parts if p] or [(text or "").strip()]


class _PartSplitter:
    """Turns a streamed reply into several short chat messages: text arrives
    in arbitrary chunks, and every blank line ends one message and starts the
    next. feed() returns ("delta", text) / ("break",) events, so the first
    part still streams live and the rest follow as their own bubbles."""

    def __init__(self):
        self.buf = ""
        self.at_start = True  # nothing shown yet in the current part

    def feed(self, delta: str) -> list[tuple]:
        self.buf += delta
        # hold back trailing whitespace: it may be one half of a blank line
        # that is split across two network chunks
        tail = re.search(r"[ \t\n]*$", self.buf)
        text, self.buf = self.buf[: tail.start()], tail.group(0)
        events: list[tuple] = []
        pieces = _BLANK_LINE_RE.split(text)
        for i, piece in enumerate(pieces):
            if self.at_start:
                piece = piece.lstrip()
            if piece:
                events.append(("delta", piece))
                self.at_start = False
            if i < len(pieces) - 1 and not self.at_start:
                events.append(("break",))
                self.at_start = True
        return events


def _ndjson(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False) + "\n"


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """Same pipeline as /chat, but the reply is streamed as newline-delimited
    JSON: {"session_id"}, then {"delta": "..."} pieces, then {"done": true}."""
    session_id = req.session_id or str(uuid.uuid4())
    history = _SESSIONS.setdefault(session_id, [])
    lang = _resolve_session_lang(session_id, req.message, req.lang)

    async def gen():
        yield _ndjson({"session_id": session_id})
        parts: list[str] = []
        store = True  # abusive messages are not kept in the conversation history
        try:
            if moderation.is_abusive(req.message):
                store = False
                text = moderation.reply(lang)
                parts.append(text)
                yield _ndjson({"delta": text})
                label = "__abusive__"  # skip the rest of the pipeline
            else:
                label = None
                if spotlight.is_spotlight_query(req.message):
                    spot = await spotlight.build_messages(lang)
                    if spot:
                        # one chat bubble per spotlight item, each with its link
                        for i, text in enumerate(spot):
                            if i:
                                yield _ndjson({"new_message": True})
                                parts.append("\n\n")
                            parts.append(text)
                            yield _ndjson({"delta": text})
                        label = "__spotlight__"  # answered; skip the rest
                if label is None:
                    label = intent.detect(req.message)
            if label in ("__abusive__", "__spotlight__"):
                pass
            elif label is not None:
                text = intent.canned_reply(label, lang)
                parts.append(text)
                yield _ndjson({"delta": text})
            else:
                corrected = spellcheck.correct_query(req.message)
                faq_answer = faq.match(corrected)
                if faq_answer is not None:
                    text = faq.canned_reply(faq_answer, lang)
                    parts.append(text)
                    yield _ndjson({"delta": text})
                else:
                    rewritten = await query_rewrite.rewrite(corrected, history)
                    section_key = sections.match(corrected) or sections.match(req.message)
                    if rewritten == query_rewrite.OFF_TOPIC and section_key:
                        rewritten = corrected  # a known site section is never off-topic
                    if rewritten == query_rewrite.OFF_TOPIC:
                        text = llm.out_of_scope_reply(lang)
                        parts.append(text)
                        yield _ndjson({"delta": text})
                    else:
                        context_snippets = await _get_context(rewritten, corrected)
                        splitter = _PartSplitter()
                        async for delta in llm.stream_answer(
                            rewritten, context_snippets, history, lang, original=req.message
                        ):
                            # short messages: every blank line in the reply
                            # starts a new chat bubble (no links added)
                            for ev in splitter.feed(delta):
                                if ev[0] == "break":
                                    parts.append("\n\n")
                                    yield _ndjson({"new_message": True})
                                else:
                                    parts.append(ev[1])
                                    yield _ndjson({"delta": ev[1]})
                        # Section question ("articles and research papers"):
                        # message 1 (above) = what it is, then message 2 = its
                        # top item, message 3 = the link. The client starts a
                        # fresh chat bubble at every "new_message" event.
                        if section_key and parts:
                            for extra in await sections.follow_up_messages(section_key, lang):
                                parts.append("\n\n" + extra)
                                yield _ndjson({"new_message": True})
                                yield _ndjson({"delta": extra})
        except Exception as exc:  # noqa: BLE001 - tell the client, don't just drop the stream
            print(f"[chat_stream] failed: {exc}", flush=True)
            if not parts:
                yield _ndjson({"error": True})
        finally:
            reply = "".join(parts).strip()
            if reply and store:
                history.append({"role": "user", "content": req.message})
                history.append({"role": "assistant", "content": reply})
        yield _ndjson({"done": True})

    return StreamingResponse(
        gen(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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