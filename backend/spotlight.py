"""
Spotlight chip: one top headline each from
  - ODOP Product of the Month   (https://upicon.in/odop)
  - Success Stories             (https://upicon.in/success-stories)
  - UP Ke Karigar               (https://upicon.in/karigar)

These are standalone pages (not API categories). Each page's current feature
looks like this once rendered:

    # One District One Product
    SEP | 2026 | Barabanki
    ## Barabanki Handloom Textile

so the headline is the first "## ..." line right after the "MON | YYYY | place"
line. Pages are read live (JS-rendered, cached for LIVE_PAGE_CACHE_SECONDS) and,
if the live fetch fails, from the crawled copy in data/static_pages.json.

No LLM call -> instant, and headlines can't be reworded or invented. If none of
the three can be read, build_reply() returns None and the caller falls back to
the normal retrieval + LLM pipeline.
"""

import json
import re
import time

import config
import crawler

_TRIGGERS = ("spotlight", "स्पॉटलाइट", "स्पोटलाइट")

# key -> page url. Dict order is the order shown in the reply.
_PAGES = {
    "odop": "https://upicon.in/odop",
    "success": "https://upicon.in/success-stories",
    "karigar": "https://upicon.in/karigar",
}

_LABELS = {
    "odop": {"en": "🏭 ODOP Product of the Month", "hi": "🏭 ओडीओपी – माह का उत्पाद"},
    "success": {"en": "🌟 Success Story", "hi": "🌟 सफलता की कहानी"},
    "karigar": {"en": "🧵 UP Ke Karigar", "hi": "🧵 यूपी के कारीगर"},
}

_HEADING = {
    "en": "Here's what's in the spotlight right now:",
    "hi": "अभी स्पॉटलाइट में यह है:",
}

# "SEP | 2026 | Barabanki" followed by "## Headline"
_ENTRY_RE = re.compile(
    r"^[ \t]*([A-Za-z]{3,9})[ \t]*\|[ \t]*(\d{4})[ \t]*\|[^\n]*\n+[ \t]*##[ \t]+([^\n]+)",
    re.MULTILINE,
)

_cache: dict[str, dict] = {}  # key -> {"value": (headline, "Sep 2026"), "ts": float}
_static_texts: dict[str, str] | None = None


def is_spotlight_query(message: str) -> bool:
    text = (message or "").lower()
    return any(t in text for t in _TRIGGERS)


def _parse_top(text: str) -> tuple[str, str] | None:
    """Return (headline, 'Mon YYYY') for the first entry on the page."""
    m = _ENTRY_RE.search(text or "")
    if not m:
        return None
    month, year, headline = m.group(1), m.group(2), m.group(3)
    headline = re.sub(r"\s+", " ", headline.replace("*", "")).strip()
    if not headline:
        return None
    return headline, f"{month[:3].title()} {year}"


def _load_static_texts() -> dict[str, str]:
    """url -> text of its first few crawled chunks (header + top entry)."""
    global _static_texts
    if _static_texts is not None:
        return _static_texts
    out: dict[str, list[str]] = {}
    try:
        with open(config.STATIC_JSON_PATH, encoding="utf-8") as f:
            for rec in json.load(f):
                url = rec.get("url")
                if url in _PAGES.values() and len(out.setdefault(url, [])) < 3:
                    out[url].append(rec.get("text", ""))
    except Exception as exc:  # noqa: BLE001
        print(f"[spotlight] could not read static pages: {exc}", flush=True)
    _static_texts = {u: "\n".join(parts) for u, parts in out.items()}
    return _static_texts


async def _live_texts(urls: list[str]) -> dict[str, str]:
    try:
        pages = await crawler.crawl_all_pages(urls)  # one browser, all pages
    except Exception as exc:  # noqa: BLE001
        print(f"[spotlight] live fetch failed: {exc}", flush=True)
        return {}
    return {p["url"]: p.get("text", "") for p in pages}


async def _headlines() -> dict[str, tuple[str, str]]:
    now = time.time()
    found: dict[str, tuple[str, str]] = {}
    stale = []
    for key, url in _PAGES.items():
        c = _cache.get(key)
        if c and (now - c["ts"]) < config.LIVE_PAGE_CACHE_SECONDS:
            found[key] = c["value"]
        else:
            stale.append(key)

    if stale:
        live = await _live_texts([_PAGES[k] for k in stale])
        static = _load_static_texts()
        for key in stale:
            url = _PAGES[key]
            parsed = _parse_top(live.get(url, "")) or _parse_top(static.get(url, ""))
            if parsed:
                found[key] = parsed
                _cache[key] = {"value": parsed, "ts": now}
    return found


async def build_reply(lang: str = "en") -> str | None:
    lang = "hi" if (lang or "").startswith("hi") else "en"
    found = await _headlines()
    if not found:
        return None
    lines = [_HEADING[lang], ""]
    for key in _PAGES:
        if key in found:
            headline, when = found[key]
            lines.append(f"{_LABELS[key][lang]}: {headline} ({when})")
    return "\n".join(lines)


_MORE = {"en": "🔗 See more:", "hi": "🔗 और देखें:"}


async def build_messages(lang: str = "en") -> list[str] | None:
    """Same content as build_reply(), but split into one chat message per
    spotlight item (ODOP / Success Story / UP Ke Karigar), each ending with
    the link to its own page. The first message also carries the heading."""
    lang = "hi" if (lang or "").startswith("hi") else "en"
    found = await _headlines()
    if not found:
        return None
    messages = []
    for key, url in _PAGES.items():
        if key not in found:
            continue
        headline, when = found[key]
        messages.append(
            f"{_LABELS[key][lang]}\n\n**{headline}** ({when})\n\n{_MORE[lang]} {url}"
        )
    if not messages:
        return None
    messages[0] = f"{_HEADING[lang]}\n\n{messages[0]}"
    return messages