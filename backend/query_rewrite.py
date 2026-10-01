"""
Query normaliser for low-literacy / badly-typed messages.

Visitors often type things like "odop k h", "cm yuva kese milega", "nokri
btao", "number kya h" - misspelled, Hinglish, fragments. A small fast model
rewrites that into ONE clear English question about UPICON before retrieval
and answering, so search and the answer model both see what the person
actually means.

  - uses a small/fast model (GROQ_QUERY_MODEL) - not the big answer model
  - results are cached (same messy phrase = no second call)
  - never raises: on any failure the original text is used unchanged
"""

import asyncio
import re

from groq import AsyncGroq, GroqError, NotFoundError

import cache
import config

_client: AsyncGroq | None = None
_REWRITE_TTL_SECONDS = 7 * 24 * 3600
OFF_TOPIC = "[[OFF_TOPIC]]"  # returned when the message is clearly not about UPICON
_bad_models: set[str] = set()  # models Groq said don't exist for this account

_SYSTEM = """You clean up messages typed to the UPICON website chatbot.
UPICON is a Uttar Pradesh body for MSMEs/industry: initiatives, schemes
(e.g. CM Yuva), ODOP (One District One Product), Youth Adda, Karigar,
business units, careers, news, contact details.

Many visitors have low literacy: wrong spellings, Hindi written in English
letters (Hinglish), Devanagari, half sentences, SMS short forms. Rewrite the
message as ONE clear English question about UPICON.

Hints: "k h", "kya h", "kya hai" = what is; "kese", "kaise" = how;
"kb", "kab" = when; "kha", "kaha", "kahan" = where; "kon", "kaun" = who;
"btao", "bataiye", "samjhao" = tell/explain; "milega", "milegi" = can I get;
"nokri", "naukri", "bharti" = job/vacancy; "yojna", "yojana" = scheme;
"number", "no", "phone", "mobile" (about UPICON) = contact number;
"pta", "pata" = address; "mail", "email" = email; "time", "timing" = office
timings; "loan", "subsidy" = keep as is. Names: odop, cmyuva/cm yuva,
youth adda, karigar, upicon, msme - keep as proper names. "Bihar Elderline"
(also typed "Elderine", number 14567) is a UPICON initiative too - never
off-topic.

Rules:
- If the message is a follow-up ("uska number", "aur batao", "and how to
  apply") use the PREVIOUS question to complete it.
- BUT a message that is just a topic name ("youth adda", "odop", "cm yuva",
  "karigar", "schemes") is complete on its own: rewrite it as "What is
  <topic>?" and do NOT combine it with the previous question.
- For phone/email/address/timings asks, use exactly these words: "contact
  number", "email", "address", "office timings".
- Do NOT answer. Do NOT add facts. Keep the meaning.
- If the message is CLEARLY unrelated to UPICON - e.g. weather, cricket
  scores, movies, celebrities, politics of other places, coding help, math,
  recipes, health advice, other companies, general knowledge - output
  exactly [[OFF_TOPIC]] and nothing else.
- But if it could plausibly relate to UPICON, Uttar Pradesh government
  schemes, MSME, business, jobs, training, products or contact details -
  even if badly spelled or vague - rewrite it normally. When unsure, do NOT
  use [[OFF_TOPIC]].
- Output only the rewritten question, one line, nothing else."""


# ---------------------------------------------------------------------------
# Deterministic guards (run BEFORE the small LLM, so the result never depends
# on how that model happens to feel about a phrasing)
#
# Problem these fix: "upicon office address" was answered, but "office address
# of upicon" and "upicon location" came back as OFF_TOPIC. Same meaning, only
# the wording/order differed - and the LLM's OFF_TOPIC verdict was then cached
# for 7 days, so it kept repeating.
# ---------------------------------------------------------------------------
_UPICON_RE = re.compile(r"upicon|upikon|यूपीकॉन|उपिकॉन|यूपी\s*आइकॉन", re.I)

# "where / address / location" style words (Latin ones need word boundaries;
# Devanagari ones are plain substrings because \b misbehaves on matras)
_ADDRESS_LATIN_RE = re.compile(
    r"\b(address|addr|location|located|locate|where|whereabouts|map|directions?|"
    r"pata|pta|kahan|kaha|kha|kidhar|lucknow|gomti\s*nagar)\b", re.I)
_ADDRESS_DEVA = ("पता", "पते", "कहाँ", "कहां", "कहा है", "स्थान", "लोकेशन", "ठिकाना", "किधर")

_OFFICE_RE = re.compile(r"\b(office|offices|headquarters?|hq|daftar)\b|कार्यालय|दफ्तर|दफ़्तर|ऑफिस|ऑफ़िस", re.I)

# If the message is clearly about one of these, it is NOT an address question
_NOT_ADDRESS_RE = re.compile(
    r"\b(job|jobs|career|careers|vacanc\w*|opening|openings|apply|naukri|nokri|bharti|"
    r"odop|scheme|schemes|yojana|yojna|yuva|cmyuva|karigar|adda|news|article|"
    r"initiative|initiatives|msme|loan|subsidy|spotlight)\b|नौकरी|भर्ती|योजना|करियर|पहल|समाचार",
    re.I)

ADDRESS_QUESTION = "What is the UPICON office address?"


def mentions_upicon(text: str) -> bool:
    return bool(_UPICON_RE.search(text or ""))


def names_known_topic(text: str) -> bool:
    """True if the message says UPICON OR names a known UPICON page/programme
    (config.PAGE_ALIASES: cm yuva, odop, bihar elderline, ...). Such a message
    is never 'off-topic', even without the word UPICON in it."""
    t = (text or "").lower()
    if mentions_upicon(t):
        return True
    for aliases in getattr(config, "PAGE_ALIASES", {}).values():
        if any(a.lower() in t for a in aliases):
            return True
    return False


def is_address_question(text: str) -> bool:
    """True for any phrasing of 'where is the UPICON office / its address /
    its location' - English, Hinglish or Hindi - as long as it is not really
    about jobs, schemes, ODOP, etc."""
    t = (text or "").strip()
    if not t or _NOT_ADDRESS_RE.search(t):
        return False
    asks_where = bool(_ADDRESS_LATIN_RE.search(t)) or any(w in t for w in _ADDRESS_DEVA)
    if not asks_where:
        return False
    # must be tied to UPICON / its office, so "where can I buy X" isn't hijacked
    return mentions_upicon(t) or bool(_OFFICE_RE.search(t))


_FOLLOWUP_RE = re.compile(
    r"\b(its|it|his|her|their|that|this|those|these|same|also|more|and|aur|"
    r"uska|uski|uske|iska|iski|iske|unka|unki|unke|wahan|waha|wahi|isme|usme)\b"
    r"|उसका|उसकी|उसके|इसका|इसकी|इसके|उनका|उनकी|उनके|और|वहां|वहाँ",
    re.I,
)


def is_standalone_topic(text: str) -> bool:
    """A short message that itself names a UPICON topic ("youth adda", "odop")
    is NOT a follow-up - it must not be glued onto the previous question
    (that is how "youth adda" turned into "contact details of Youth Adda"
    right after someone asked how to contact UPICON)."""
    t = (text or "").strip().lower()
    if not t or len(t.split()) > 4 or _FOLLOWUP_RE.search(t):
        return False
    known = list(getattr(config, "DOMAIN_TERMS", []))
    for aliases in getattr(config, "PAGE_ALIASES", {}).values():
        known.extend(a.lower() for a in aliases)
    return any(k and k in t for k in known)


def _get_client() -> AsyncGroq:
    global _client
    if _client is None:
        _client = AsyncGroq(api_key=config.GROQ_API_KEY)
    return _client


async def rewrite(message: str, history: list[dict] | None = None) -> str:
    text = (message or "").strip()
    if not text:
        return message

    # Any wording of "UPICON office address / location" -> ONE canonical
    # question, so all of them retrieve and answer identically. Done before
    # the cache lookup so an old cached OFF_TOPIC can't get in the way.
    if is_address_question(text):
        print(f"[query_rewrite] {text!r} -> {ADDRESS_QUESTION!r} (address rule)", flush=True)
        return ADDRESS_QUESTION

    prev = ""
    for turn in reversed(history or []):
        if turn.get("role") == "user":
            prev = str(turn.get("content", ""))[:200]
            break

    if is_standalone_topic(text):
        prev = ""  # a bare topic name stands on its own

    key = cache.build_key("rewrite", text.lower(), prev.lower())
    hit = await asyncio.to_thread(cache.get, key)
    if hit and not (hit == OFF_TOPIC and names_known_topic(text)):
        return hit

    user_block = f"Message: {text}"
    if prev:
        user_block = f"Previous question: {prev}\n{user_block}"

    # Try the dedicated query model (if set), then the main chat model.
    candidates = [m for m in (config.GROQ_QUERY_MODEL, config.GROQ_CHAT_MODEL) if m]
    candidates = [m for i, m in enumerate(candidates) if m not in _bad_models and m not in candidates[:i]]

    out = ""
    for model in candidates:
        kwargs = {"max_tokens": 80}
        if "gpt-oss" in model:
            # reasoning model: keep thinking short, leave room for the answer
            kwargs = {"max_tokens": 400, "reasoning_effort": "low"}
        try:
            resp = await _get_client().chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": user_block},
                ],
                temperature=0,
                **kwargs,
            )
            out = (resp.choices[0].message.content or "").strip()
            break
        except NotFoundError as exc:
            print(f"[query_rewrite] model {model!r} unavailable, skipping it: {exc}", flush=True)
            _bad_models.add(model)
        except (GroqError, IndexError, AttributeError) as exc:
            print(f"[query_rewrite] failed, using original: {exc}", flush=True)
            return message

    if OFF_TOPIC in out:
        if names_known_topic(text):
            # The visitor literally said "UPICON" (or named a known UPICON programme) - it is about UPICON, whatever
            # the small model thought. Keep the (spell-corrected) message and
            # let retrieval decide; don't cache the verdict.
            print(f"[query_rewrite] ignoring OFF_TOPIC for UPICON message: {text!r}", flush=True)
            return message
        out = OFF_TOPIC
    else:
        out = out.splitlines()[0].strip().strip('"').strip() if out else ""
    if not out or len(out) > 300:
        return message

    await asyncio.to_thread(cache.set, key, out, _REWRITE_TTL_SECONDS, "rewrite")
    print(f"[query_rewrite] {text!r} -> {out!r}", flush=True)
    return out