"""
Canned answers for a handful of very common, essentially-static questions
(e.g. "what is UPICON", "how do I contact you"). These skip retrieval AND
the Groq call entirely — same idea as intent.py's chit-chat short-circuit,
but for real questions with variable phrasing rather than exact greetings.

Keep this list SHORT and only for facts that basically never change. Job
openings, schemes, news, ODOP, etc. must keep going through the normal
router + LLM path — hardcoding those here would go stale silently.

Matching: fuzzy (rapidfuzz), same tolerance style as intent.py, but against
a list of representative keyword phrases per FAQ rather than whole-message
equality, since real questions vary a lot in wording.
"""

import re

from rapidfuzz import fuzz

# Fill in real content/keywords as you notice repeat questions in your logs.
# Each entry: list of trigger phrases -> bilingual canned answer.
FAQS = [
    {
        "keywords": [
            "what is upicon", "about upicon", "what does upicon do",
            "upicon kya hai", "upicon kya karti hai",
        ],
        "answer": {
            "en": "UPICON is Uttar Pradesh's industry/MSME-support body — "
                  "ask me about specific initiatives, schemes, careers or "
                  "news and I can go into detail.",
            "hi": "UPICON उत्तर प्रदेश की उद्योग/MSME सहायता संस्था है — "
                  "किसी खास पहल, योजना, करियर या समाचार के बारे में पूछें, "
                  "मैं विस्तार से बताऊँगा।",
        },
    },
    # NOTE: deliberately no "contact"/"address" entry here. A canned FAQ
    # answer short-circuits BEFORE retrieval runs, so it can never be
    # overridden by the real address/phone/email actually crawled from the
    # site - it would keep returning this placeholder text forever, even
    # after the real content is indexed. Contact-type questions should stay
    # on the normal retrieval path below until you've confirmed exactly
    # what real, verified text you want hardcoded here (if ever).
]

_FUZZY_THRESHOLD = 85  # 0-100, rapidfuzz partial-ratio score


def _normalize(text: str) -> str:
    text = text.lower().strip()
    return re.sub(r"[^\w\s]", "", text)


def match(message: str) -> dict | None:
    """Return the bilingual answer dict for the first FAQ this message
    matches closely enough, or None if nothing matches (falls through to
    the normal retrieval + LLM pipeline)."""
    norm = _normalize(message)
    if not norm:
        return None
    for entry in FAQS:
        for kw in entry["keywords"]:
            kw_norm = _normalize(kw)
            if not kw_norm:
                continue
            if kw_norm in norm or fuzz.partial_ratio(norm, kw_norm) >= _FUZZY_THRESHOLD:
                return entry["answer"]
    return None


def canned_reply(answer: dict, lang: str = "en") -> str:
    lang = "hi" if lang.startswith("hi") else "en"
    return answer.get(lang, answer.get("en", ""))