"""
Groq wrapper: final answer generation (chat) + optional server-side speech
to text (Whisper, hosted by Groq).
"""

import re

from groq import Groq, GroqError
from rapidfuzz import fuzz

import cache
import config

_client: Groq | None = None


def get_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=config.GROQ_API_KEY)
    return _client


SYSTEM_PROMPT = """You are the official virtual assistant for the UPICON website (upicon.in).

Rules you must always follow:
1. Answer ONLY using the information given to you in the "Context" section below.
   Never invent facts. Before saying you don't have something, re-read the
   ENTIRE context block carefully — the answer is often present but buried
   inside a longer snippet alongside other details; extract and use it even
   if it's a small part of a larger block. If part of the question isn't
   covered, still share everything the context DOES say about the topic
   first (what it is, who it's for, benefits, how to join/apply, etc.).
   Only if the context has nothing useful at all, reply in ONE short
   sentence in the session language, e.g. "I don't have those details right
   now — please reach out through the Contact page for the latest
   information." Never explain what the context contains or lacks, and never
   describe menus, links or navigation items.
2. Never mention, hint at, or reveal HOW you know something — never say
   "according to the crawled page", "the API returned", "based on the
   database", "source", "static content", "dynamic content", or similar.
   Just answer naturally, as if you simply know about UPICON.
3. Never output URLs, links, file paths, or JSON — even if one appears in the
   context. The ONLY exception is a link given to you in a "TOPIC LINK"
   instruction at the very end of these rules. Never tell the user to "visit", "open", or "explore" a page,
   section, menu or the site's search instead of answering. Give the actual
   details the user asked for, taken from the context. Never say "the
   provided text/excerpt/context/snippet" either — just answer directly.
4. Stay strictly on topic: only answer questions about UPICON (its
   initiatives, schemes, news, careers, programs, contact details, etc.).
   If asked something unrelated to the website (general knowledge, coding
   help, other companies, etc.), politely decline and steer back to what you
   can help with regarding UPICON.
5. Reply in the language given to you as SESSION LANGUAGE below — every
   reply in this conversation must stay in that language, even if the
   user's message itself is typed in the other language or in Hinglish.
   Only switch if the user explicitly asks you to change languages.
6. Keep answers concise, friendly, and well-formatted (short paragraphs or
   bullet points for lists like job openings or schemes).
7. Hard limit: your entire reply must be between 100 and 150 words. If the
   context has more than that, summarize the most relevant/recent points
   rather than listing everything — don't pad a short answer to reach 100
   words either; be naturally concise.
9. Never greet or introduce yourself unless the user's message is itself a
   greeting. If the user names a topic, answer about that topic.
8. Always write numbers as English/Western digits (0-9) — in every language,
   including Hindi replies. Never use Devanagari digits (०१२३४५६७८९). This
   applies to dates, amounts, phone numbers, percentages and counts.
"""

_LANG_NAMES = {"en": "English", "hi": "Hindi (Devanagari script)"}

# Shown when Groq itself fails (rate limited, payload too large, connection
# issue, etc.) so a provider hiccup never turns into a 500 for the visitor -
# mirrors the frontend's own network-error fallback text.
_NO_INFO_REPLY = {
    "en": "I don't have those details right now — please reach out through the Contact page for the latest information.",
    "hi": "अभी मेरे पास ये जानकारी उपलब्ध नहीं है — ताज़ा जानकारी के लिए कृपया संपर्क (Contact) पेज से जुड़ें।",
}

_FALLBACK_REPLY = {
    "en": "Sorry, I'm having trouble responding right now. Please try again in a moment.",
    "hi": "माफ़ कीजिए, अभी जवाब देने में दिक्कत आ रही है। कृपया थोड़ी देर बाद पुनः प्रयास करें।",
}


_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL_RE = re.compile(r"https?://\S+|www\.\S+")


def _clean_context(snippet: str) -> str:
    """Remove links/URLs from context before the model sees it, so it can
    only answer with real details and has nothing to point the user to."""
    snippet = _MD_LINK_RE.sub(r"\1", snippet)
    snippet = _URL_RE.sub("", snippet)
    return re.sub(r"[ \t]{2,}", " ", snippet)


# Topics where the reply should give a few details AND point the visitor to
# an external website for the rest. Add more entries here if needed.
_TOPIC_LINKS = [
    {
        "name": "Youth Adda",
        "url": "https://cmyouthadda.in/",
        "compact": "youthadda",  # matched fuzzily against the query with spaces/punctuation removed
        "regex": re.compile(r"youth\s*-?\s*adda|yuva\s*-?\s*adda|युवा\s*अड्डा|यूथ\s*अड्डा", re.I),
    },
]


def _topic_link_instruction(user_message: str) -> str:
    compact = re.sub(r"[^a-z0-9]", "", user_message.lower())
    for t in _TOPIC_LINKS:
        if t["regex"].search(user_message) or (
            compact and fuzz.partial_ratio(t["compact"], compact) >= 88
        ):
            return (
                f"\n\nTOPIC LINK: The user is asking about {t['name']}. Give a few key "
                f"details about it from the context (2-4 short sentences), then end with "
                f"one short line, in the session language, inviting them to check more on "
                f"the {t['name']} website, writing exactly this link: {t['url']}"
            )
    return ""


def build_messages(
    user_message: str,
    context_snippets: list[str],
    history: list[dict],
    lang: str = "en",
):
    context_snippets = [_clean_context(c) for c in context_snippets]
    context_block = (
        "\n---\n".join(context_snippets) if context_snippets else "(no matching information found)"
    )
    lang_name = _LANG_NAMES.get(lang, "English")
    system_content = f"{SYSTEM_PROMPT}\n\nSESSION LANGUAGE: {lang_name}"
    system_content += _topic_link_instruction(user_message)
    messages = [{"role": "system", "content": system_content}]
    # keep a short rolling history for follow-up questions
    for turn in history[-6:]:
        messages.append(turn)
    question = user_message
    if len(user_message.split()) <= 3:
        # A bare topic like "cm yuva" - answer about the topic itself instead
        # of drifting into whatever the previous turn was about.
        question += (
            "\n(This is a short topic query. Give a helpful overview of this topic "
            "itself - what it is, who it is for, key benefits, and how to join or "
            "apply - using only the context. Do not assume it continues an earlier "
            "question.)"
        )
    messages.append(
        {
            "role": "user",
            "content": f"Context:\n{context_block}\n\nUser question: {question}",
        }
    )
    return messages


# Devanagari (०-९) and Arabic-Indic (٠-٩, ۰-۹) digits -> ASCII 0-9.
_DIGIT_MAP = str.maketrans("०१२३४५६७८९٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "0123456789" * 3)


def _ascii_digits(text: str) -> str:
    """Enforce English digits even if the model slips into Devanagari ones
    (or the site content itself contains them)."""
    return text.translate(_DIGIT_MAP)


def _trim_to_word_limit(text: str, max_words: int) -> str:
    """Safety net in case the model overshoots the prompted word limit.
    Cuts at the last full sentence within the limit when possible, instead
    of chopping mid-sentence."""
    words = text.split()
    if len(words) <= max_words:
        return text
    truncated = " ".join(words[:max_words])
    last_stop = max(
        truncated.rfind(". "),
        truncated.rfind("। "),  # Hindi purna viram
        truncated.rfind("? "),
        truncated.rfind("! "),
    )
    if last_stop > 0:
        return truncated[: last_stop + 1].strip()
    return truncated.strip() + "..."


def generate_answer(
    user_message: str,
    context_snippets: list[str],
    history: list[dict] | None = None,
    lang: str = "en",
) -> str:
    history = history or []

    # Nothing retrieved at all: don't let the model improvise (it can drift
    # into greetings or made-up text) - give a fixed, honest reply instead.
    if not context_snippets:
        print(f"[llm] no context for {user_message!r} - returning no-info reply", flush=True)
        return _NO_INFO_REPLY.get(lang, _NO_INFO_REPLY["en"])

    # Cache only when there's no rolling history yet - follow-up questions
    # depend on prior turns, so those always go to the model fresh. A fresh
    # question with identical context (nothing on the site changed) is safe
    # to serve from cache instead of calling the LLM again.
    cache_key = None
    if not history:
        cache_key = cache.build_key(
            "chat_answer",
            lang,
            user_message.strip().lower(),
            "\n".join(sorted(context_snippets)),
        )
        cached_reply = cache.get(cache_key)
        if cached_reply is not None:
            return _ascii_digits(cached_reply)

    client = get_client()
    messages = build_messages(user_message, context_snippets, history, lang)

    # Devanagari script uses far more tokens per word than English, so a
    # fixed low cap risks hard-truncating a Hindi reply mid-sentence before
    # it's even finished generating (which _trim_to_word_limit can't fix -
    # that only trims already-complete text). Give Hindi more headroom.
    # Reasoning models (e.g. gpt-oss) spend part of max_tokens on hidden
    # reasoning before the visible reply, so leave generous headroom.
    max_tokens = 2000 if lang == "hi" else 1500

    try:
        completion = client.chat.completions.create(
            model=config.GROQ_CHAT_MODEL,
            messages=messages,
            temperature=0.3,
            max_tokens=max_tokens,
        )
    except GroqError as exc:
        # Payload too large (413), rate limited (429), a Groq outage (5xx),
        # or a connection error - never let a provider hiccup crash the
        # /chat endpoint. Not cached, so the very next try goes to the model
        # fresh instead of repeating the same failure.
        print(f"[llm] Groq request failed: {exc}", flush=True)
        return _FALLBACK_REPLY.get(lang, _FALLBACK_REPLY["en"])

    if completion.choices[0].finish_reason == "length":
        # Model ran out of tokens mid-reply even with the raised cap above -
        # log it so you can see if max_tokens needs raising further.
        print(f"[llm] reply hit max_tokens={max_tokens} and was cut off (lang={lang})", flush=True)

    reply = completion.choices[0].message.content.strip()
    reply = _ascii_digits(reply)
    reply = _trim_to_word_limit(reply, config.MAX_REPLY_WORDS)

    if cache_key is not None:
        cache.set(cache_key, reply, config.CACHE_TTL_CHAT_SECONDS, namespace="chat_answer")

    return reply


def transcribe_audio(file_path: str, language_hint: str | None = None) -> str:
    """Server-side speech-to-text via Groq's hosted Whisper. Optional —
    the reference frontend uses the browser's built-in Web Speech API
    instead, but this is here if you'd rather transcribe on the backend
    (e.g. for non-Chrome browsers or mobile apps)."""
    client = get_client()
    with open(file_path, "rb") as f:
        result = client.audio.transcriptions.create(
            file=f,
            model=config.GROQ_WHISPER_MODEL,
            language=language_hint,  # "en" or "hi", or None to auto-detect
        )
    return result.text.strip()