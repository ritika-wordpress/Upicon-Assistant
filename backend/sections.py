"""
Section walkthrough.

When a visitor asks about a SECTION of the site by name - "articles and
research papers", "odop", "success stories", "karigar" - the bot answers in
three separate chat messages instead of one:

  1. what the section is        -> the normal grounded LLM answer (unchanged)
  2. its top / first item       -> built here, straight from the API / page
  3. the link to the section    -> built here (the LLM never prints URLs)

Messages 2 and 3 involve no LLM, so the headline and the link can't be
reworded or invented. Only a message that is *just* a section name (plus
filler like "tell me about", "section", "kya hai") triggers this - questions
with extra detail ("how do I apply...", "what initiatives does UPICON run?")
and the suggestion chips keep their normal single-message answer.

To add a section: add an entry to SECTIONS. `top` is one of
  "contents"          first item of the contents/articles API feed
  "spotlight:<key>"   first headline of the ODOP / success / karigar page
"""

import re

from rapidfuzz import fuzz

import api_client
import config
import spotlight
from crawler import clean_html

SECTIONS = {
    "articles": {
        "url": "https://upicon.in/articles",
        "top": "contents",
        "label": {"en": "Articles & Research Papers", "hi": "लेख और रिसर्च पेपर"},
        "aliases": [
            "articles", "article", "articles and research papers", "articles research papers",
            "research papers", "research paper", "research", "lekh",
            "लेख", "लेख और रिसर्च पेपर", "रिसर्च पेपर", "आर्टिकल", "आर्टिकल्स",
        ],
    },
    "odop": {
        "url": "https://upicon.in/odop",
        "top": "spotlight:odop",
        "label": {"en": "ODOP - Product of the Month", "hi": "ओडीओपी - माह का उत्पाद"},
        "aliases": ["odop", "one district one product", "product of the month",
                    "ओडीओपी", "एक जनपद एक उत्पाद", "माह का उत्पाद"],
    },
    "success-stories": {
        "url": "https://upicon.in/success-stories",
        "top": "spotlight:success",
        "label": {"en": "Success Stories", "hi": "सफलता की कहानियाँ"},
        "aliases": ["success stories", "success story", "सफलता की कहानी", "सफलता की कहानियाँ",
                    "सफलता की कहानियां"],
    },
    "karigar": {
        "url": "https://upicon.in/karigar",
        "top": "spotlight:karigar",
        "label": {"en": "UP Ke Karigar", "hi": "यूपी के कारीगर"},
        "aliases": ["karigar", "karigars", "up ke karigar", "कारीगर", "यूपी के कारीगर"],
    },
}

_FILLER = {
    "what", "whats", "is", "are", "the", "a", "an", "about", "tell", "me", "please", "show",
    "give", "of", "in", "on", "upicon", "section", "sections", "page", "pages", "link", "links",
    "website", "site", "and", "details", "detail", "information", "info", "read", "see", "open",
    "kya", "hai", "ka", "ki", "ke", "batao", "bataiye", "bare", "baare", "mein", "me",
    "क्या", "है", "के", "बारे", "में", "बताओ", "बताइए", "बताएं", "जानकारी", "सेक्शन", "पेज",
    "लिंक", "का", "की", "और", "यूपीकॉन",
}


def _core(text: str) -> str:
    t = (text or "").lower().replace("&", " and ")
    t = re.sub(r"[^\w\s\u0900-\u097F]", " ", t)
    return " ".join(w for w in t.split() if w not in _FILLER)


def match(message: str) -> str | None:
    """Section key if the message is just a section name, else None."""
    core = _core(message)
    if not core:
        return None
    for key, sec in SECTIONS.items():
        for alias in sec["aliases"]:
            a = _core(alias)
            if a and (core == a or fuzz.ratio(core, a) >= 88):
                return key
    return None


def _snippet(text: str, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:- ") + "…"


async def _top_from_contents(label: str, lang: str) -> str | None:
    data = await api_client.get_contents(category="All", offset=0)
    items = data.get("data", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        return None
    first = next((i for i in items if isinstance(i, dict) and i.get("title")), None)
    if not first:
        return None
    title = clean_html(str(first["title"])).strip()
    category = clean_html(str(first.get("category_name") or "")).strip()
    desc = _snippet(clean_html(str(first.get("description") or first.get("details") or "")))
    head = f"**{title}**" + (f" ({category})" if category else "")
    intro = f"📰 Top item in {label}:" if lang == "en" else f"📰 {label} में सबसे ऊपर:"
    return "\n".join(p for p in (intro, head, desc) if p)


async def _top_from_spotlight(spot_key: str, label: str, lang: str) -> str | None:
    found = await spotlight._headlines()
    if spot_key not in found:
        return None
    headline, when = found[spot_key]
    intro = f"🌟 Top in {label}:" if lang == "en" else f"🌟 {label} में सबसे ऊपर:"
    return f"{intro}\n**{headline}** ({when})"


async def follow_up_messages(key: str, lang: str = "en") -> list[str]:
    """Message 2 (top item) and message 3 (link) for a matched section.
    Message 2 is skipped if the top item can't be read; the link is always
    sent. Never raises."""
    lang = "hi" if (lang or "").startswith("hi") else "en"
    sec = SECTIONS[key]
    label = sec["label"][lang]
    out: list[str] = []

    try:
        if sec["top"] == "contents":
            top = await _top_from_contents(label, lang)
        elif sec["top"].startswith("spotlight:"):
            top = await _top_from_spotlight(sec["top"].split(":", 1)[1], label, lang)
        else:
            top = None
        if top:
            out.append(top)
    except Exception as exc:  # noqa: BLE001 - never break a chat turn
        print(f"[sections] top item failed for {key}: {exc}", flush=True)

    link = (
        f"🔗 See the full {label} section here: {sec['url']}"
        if lang == "en"
        else f"🔗 पूरा {label} सेक्शन यहाँ देखें: {sec['url']}"
    )
    out.append(link)
    return out