"""
Groq wrapper: final answer generation (chat) + optional server-side speech
to text (Whisper, hosted by Groq).
"""

import asyncio
import re

from groq import AsyncGroq, Groq, GroqError
from rapidfuzz import fuzz

import cache
import config

_client: Groq | None = None


def get_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=config.GROQ_API_KEY)
    return _client


_async_client: AsyncGroq | None = None


def get_async_client() -> AsyncGroq:
    global _async_client
    if _async_client is None:
        _async_client = AsyncGroq(api_key=config.GROQ_API_KEY)
    return _async_client


SYSTEM_PROMPT = """You are the official virtual assistant and UPICON expert for the UPICON website (upicon.in).

Rules you must always follow:
1. Answer ONLY using the information given to you in the "Context" section below.
   Never invent facts. Before saying you don't have something, re-read the
   ENTIRE context block carefully — the answer is often present but buried
   inside a longer snippet alongside other details; extract and use it even
   if it's a small part of a larger block. If part of the question isn't
   covered, still share everything the context DOES say about the topic
   first (what it is, who it's for, benefits, how to join/apply, etc.) —
   EXCEPT for specific-detail questions, which follow rule 10.
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
   If the question is not about UPICON - general knowledge, definitions of
   ordinary words, coding help, other companies, or anything else - OR the
   context only covers a generic topic (a product, a market, a business
   idea) that the context does not tie to a UPICON program, scheme,
   initiative, service or page, then output exactly [[OUT_OF_SCOPE]] and
   nothing else. Never answer from general knowledge. BUT misspelled, badly
   typed, very short or Hinglish questions are NOT out of scope: if the
   question could plausibly be about UPICON, treat it as such and answer.
5. Reply in the language given to you as SESSION LANGUAGE below — every
   reply in this conversation must stay in that language, even if the
   user's message itself is typed in the other language or in Hinglish.
   Only switch if the user explicitly asks you to change languages.
6. Keep answers concise, friendly, and well-formatted (short paragraphs or
   bullet points for lists like job openings or schemes).
7. Hard limit: your entire reply must be at most 150 words. If the context
   has more than that, summarize the most relevant/recent points rather than
   listing everything. Never pad a short answer — shorter is better when the
   question is simple.
9. Never greet or introduce yourself unless the user's message is itself a
   greeting. If the user names a topic, answer about that topic.
8. Always write numbers as English/Western digits (0-9) — in every language,
   including Hindi replies. Never use Devanagari digits (०१२३४५६७८९). This
   applies to dates, amounts, phone numbers, percentages and counts.
10. SPECIFIC-DETAIL QUESTIONS: if the user asks for one simple detail (phone
   number, email, address, office timings, a date, a fee, etc.), reply with
   ONLY that detail in one short line. Do not add an overview, related
   details, other contact channels, or follow-up offers. Give more only if
   the user explicitly asks for more.
11. Use ONLY facts, figures and statistics that appear in the context. Never
   add market sizes, projections, industry trends, generic business advice
   or explanations of your own, even to make an answer fuller.
12. Many visitors have limited literacy and type with wrong spellings or
   mixed Hindi/English (e.g. "odop k h" means "what is ODOP"). Work out what
   they mean and answer like a helpful local expert: short, simple
   sentences, everyday words, no jargon.
13. If the visitor's message contains abusive, rude or offensive language, do
   not answer it: politely ask them, in the session language, to use
   appropriate language and say you are happy to help with UPICON.
14. CONTACT DETAILS: copy every email address, phone number, address and
   timing EXACTLY as written in the context. Never guess, combine, round or
   complete them. Only give a contact line a label (e.g. "Enquiry", "Banking
   support") or opening hours if that exact label/time is written next to that
   value in the context. If a value is not in the context, leave it out.
15. SHORT MESSAGES: the reply is shown as separate short chat messages. Any
   answer longer than about 30 words must be written as 2-5 short parts - 1 to
   3 sentences each (or 2-3 list items each) - with a BLANK LINE between parts.
   First part = the direct answer; later parts = supporting details. A one-line
   answer stays a single part. Never number or label the parts.
"""

_LANG_NAMES = {"en": "English", "hi": "Hindi (Devanagari script)"}

def out_of_scope_reply(lang: str = "en") -> str:
    return _OUT_OF_SCOPE_REPLY.get(lang, _OUT_OF_SCOPE_REPLY["en"])


def _extra_kwargs(model: str | None) -> dict:
    """gpt-oss models burn max_tokens on hidden reasoning before writing the
    visible reply - worst in Hindi, where text is also token-heavy. That
    left an EMPTY reply (-> the 'sorry, trouble responding' message). Low
    reasoning effort keeps the budget for the actual answer."""
    return {"reasoning_effort": "low"} if model and "gpt-oss" in model else {}


_OOS_TOKEN = "[[OUT_OF_SCOPE]]"
_OOS_HOLD = len(_OOS_TOKEN)  # streaming: hold back this many chars to spot the token

_OUT_OF_SCOPE_REPLY = {
    "en": "I'm designed to help only with questions about UPICON — its initiatives, schemes, careers, spotlight and more. Please ask me something about UPICON and I'll be glad to help.",
    "hi": "मैं केवल UPICON से जुड़े सवालों में मदद करने के लिए बनाया गया हूँ — इसकी पहलें, योजनाएँ, करियर, स्पॉटलाइट आदि। कृपया UPICON के बारे में कुछ पूछें, मुझे खुशी होगी।",
}

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


_DETAIL_RE = re.compile(
    r"phone|mobile|helpline|whatsapp|toll\s*-?\s*free|"
    r"contact\s*(no|num|number|details?|info)|"
    r"e-?mail|mail\s*id|address|office\s*(hours|timings?)|timings?|"
    r"फोन|फ़ोन|मोबाइल|नंबर|नम्बर|ईमेल|पता|संपर्क\s*(नंबर|विवरण)|हेल्पलाइन",
    re.I,
)

_DETAIL_INSTRUCTION = (
    "\n(This asks for one specific detail. Reply with ONLY that detail in one "
    "short line - e.g. just the number, email or address. No overview, no "
    "related information, no follow-up offer.)"
)


def is_detail_query(user_message: str) -> bool:
    return bool(_DETAIL_RE.search(user_message))


_CONTACT_RE = re.compile(
    r"contact|reach|call|phone|mobile|helpline|e-?mail|mail\s*id|address|"
    r"timings?|office\s*hours|संपर्क|फोन|फ़ोन|मोबाइल|नंबर|नम्बर|ईमेल|पता",
    re.I,
)


def is_contact_query(*messages: str | None) -> bool:
    return any(m and _CONTACT_RE.search(m) for m in messages)


# ---- grounding check: contact details in a reply must exist in the context ----
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<!\d)\+?\d[\d\s\-().]{7,}\d(?!\d)")
_TIME_RE = re.compile(r"(?<!\d)(\d{1,2})[:.](\d{2})(?!\d)")


def _ground_contact_details(reply: str, context_snippets: list[str], check_times: bool, lang: str) -> str:
    """The model sometimes 'completes' contact info: an email or phone that is
    not on the site, a made-up label, or opening hours it never saw. Drop any
    reply LINE whose email / 10+ digit phone / time does not literally appear
    in the context it was given."""
    ctx = "\n".join(_clean_context(c) for c in context_snippets)
    ctx_lower = ctx.lower()
    ctx_digits = re.sub(r"\D", "", ctx)
    ctx_times = {f"{int(h)}:{m}" for h, m in _TIME_RE.findall(ctx)}

    def ok(line: str) -> bool:
        for e in _EMAIL_RE.findall(line):
            if e.lower() not in ctx_lower:
                return False
        for ph in _PHONE_RE.findall(line):
            d = re.sub(r"\D", "", ph)
            if len(d) >= 10 and d[-10:] not in ctx_digits:
                return False
        if check_times:
            for h, m in _TIME_RE.findall(line):
                if f"{int(h)}:{m}" not in ctx_times:
                    return False
        return True

    lines = reply.split("\n")
    kept = [ln for ln in lines if ok(ln)]
    if len(kept) != len(lines):
        print(f"[llm] dropped {len(lines) - len(kept)} ungrounded contact line(s)", flush=True)
    out = "\n".join(kept).strip()
    return out or _NO_INFO_REPLY.get(lang, _NO_INFO_REPLY["en"])


def build_messages(
    user_message: str,
    context_snippets: list[str],
    history: list[dict],
    lang: str = "en",
    original: str | None = None,
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
    if original and original.strip().lower() != user_message.strip().lower():
        question += (
            f"\n(The visitor actually typed: {original.strip()!r}. They may be low-literacy "
            "and misspell or mix Hindi and English - understand what they mean.)"
        )
    if is_detail_query(user_message):
        question += _DETAIL_INSTRUCTION
    elif len(user_message.split()) <= 3:
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
            "content": (
                f"Context:\n{context_block}\n\nUser question: {question}\n\n"
                "(Answer only if this is about UPICON and the context supports it; "
                "otherwise output exactly [[OUT_OF_SCOPE]].)"
            ),
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
    original: str | None = None,
    boost: bool = False,
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
            "chat_answer_v3",
            lang,
            user_message.strip().lower(),
            "\n".join(sorted(context_snippets)),
        )
        cached_reply = cache.get(cache_key)
        if cached_reply is not None:
            return _ascii_digits(cached_reply)

    client = get_client()
    contact = is_contact_query(user_message, original)
    messages = build_messages(user_message, context_snippets, history, lang, original)

    # Devanagari script uses far more tokens per word than English, so a
    # fixed low cap risks hard-truncating a Hindi reply mid-sentence before
    # it's even finished generating (which _trim_to_word_limit can't fix -
    # that only trims already-complete text). Give Hindi more headroom.
    # Reasoning models (e.g. gpt-oss) spend part of max_tokens on hidden
    # reasoning before the visible reply, so leave generous headroom.
    max_tokens = 2500 if lang == "hi" else 1500
    if boost:
        max_tokens = int(max_tokens * 1.5)

    try:
        completion = client.chat.completions.create(
            model=config.GROQ_CHAT_MODEL,
            messages=messages,
            temperature=0 if contact else 0.3,
            max_tokens=max_tokens,
            **_extra_kwargs(config.GROQ_CHAT_MODEL),
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

    reply = (completion.choices[0].message.content or "").strip()
    if not reply:
        print(f"[llm] empty reply (finish_reason={completion.choices[0].finish_reason}, lang={lang})", flush=True)
        return _FALLBACK_REPLY.get(lang, _FALLBACK_REPLY["en"])
    reply = _ascii_digits(reply)
    if _OOS_TOKEN in reply:
        reply = _OUT_OF_SCOPE_REPLY.get(lang, _OUT_OF_SCOPE_REPLY["en"])
    else:
        reply = _ground_contact_details(reply, context_snippets, contact, lang)
    reply = _trim_to_word_limit(reply, config.MAX_REPLY_WORDS)

    if cache_key is not None:
        cache.set(cache_key, reply, config.CACHE_TTL_CHAT_SECONDS, namespace="chat_answer")

    return reply


async def stream_answer(
    user_message: str,
    context_snippets: list[str],
    history: list[dict] | None = None,
    lang: str = "en",
    original: str | None = None,
):
    """Async generator yielding the reply as text deltas (for /chat/stream).
    Same rules as generate_answer: no context -> fixed reply, cache hit ->
    served whole, Groq failure -> fallback text, out-of-scope token -> fixed
    out-of-scope reply. The first few characters are held back just long
    enough to spot the [[OUT_OF_SCOPE]] token before anything is shown."""
    history = history or []

    if not context_snippets:
        yield _NO_INFO_REPLY.get(lang, _NO_INFO_REPLY["en"])
        return

    cache_key = None
    if not history:
        cache_key = cache.build_key(
            "chat_answer_v3", lang, user_message.strip().lower(),
            "\n".join(sorted(context_snippets)),
        )
        cached_reply = cache.get(cache_key)
        if cached_reply is not None:
            yield _ascii_digits(cached_reply)
            return

    if is_contact_query(user_message, original):
        # Contact details are checked against the context before anything is
        # shown, so these replies are generated whole instead of streamed.
        yield await asyncio.to_thread(
            generate_answer, user_message, context_snippets, history, lang, original
        )
        return

    messages = build_messages(user_message, context_snippets, history, lang, original)
    max_tokens = 2500 if lang == "hi" else 1500
    finish = None
    parts: list[str] = []
    head = ""
    sent_any = False
    oos = False

    try:
        stream = await get_async_client().chat.completions.create(
            model=config.GROQ_CHAT_MODEL,
            messages=messages,
            temperature=0.3,
            max_tokens=max_tokens,
            stream=True,
            **_extra_kwargs(config.GROQ_CHAT_MODEL),
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            if chunk.choices[0].finish_reason:
                finish = chunk.choices[0].finish_reason
            delta = chunk.choices[0].delta.content
            if not delta:
                continue  # reasoning tokens arrive separately - never shown
            delta = _ascii_digits(delta)
            if not sent_any:
                head += delta
                if len(head.strip()) < _OOS_HOLD:
                    continue
                if _OOS_TOKEN in head:
                    oos = True
                    break
                delta = head.lstrip()
                head = ""
                sent_any = True
            parts.append(delta)
            yield delta

        if not oos and not sent_any and head.strip():
            if _OOS_TOKEN in head:
                oos = True
            else:
                parts.append(head.strip())
                yield head.strip()
    except GroqError as exc:
        print(f"[llm] Groq stream failed: {exc}", flush=True)
        if not parts:
            yield _FALLBACK_REPLY.get(lang, _FALLBACK_REPLY["en"])
        return  # never cache a failed/partial reply

    if not oos and not parts:
        # Stream ended with no visible text (reasoning used up the budget).
        # Retry once, non-streaming, with a bigger budget instead of showing
        # the visitor an error.
        print(f"[llm] stream gave no text (finish_reason={finish}, lang={lang}) - retrying once", flush=True)
        retry = await asyncio.to_thread(
            generate_answer, user_message, context_snippets, history, lang, original, True
        )
        yield retry
        return

    if oos:
        reply = _OUT_OF_SCOPE_REPLY.get(lang, _OUT_OF_SCOPE_REPLY["en"])
        yield reply
        if cache_key is not None:
            cache.set(cache_key, reply, config.CACHE_TTL_CHAT_SECONDS, namespace="chat_answer")
        return

    reply = "".join(parts).strip()
    if cache_key is not None and reply:
        cache.set(cache_key, reply, config.CACHE_TTL_CHAT_SECONDS, namespace="chat_answer")


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