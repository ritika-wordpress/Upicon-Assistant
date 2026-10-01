"""
Decides, for every real content question, whether to pull:
  - dynamic content from the UPICON API (jobs, schemes, news, initiatives...)
  - static content from the crawled+embedded site pages
  - or both

and returns a single list of plain-text context snippets for the LLM.
Nothing here ever labels a snippet as "static" or "dynamic" or attaches a
URL/source to it — that's what keeps the final answer source-free, per the
requirement that the bot never reveals where its data came from.
"""

import re

import api_client
import config
import live_fetch
import vector_store
from crawler import clean_html

# Keyword -> which dynamic fetchers are relevant. Checked against the
# lower-cased user message. English + Hindi (Latin-script + Devanagari)
# keywords included so both languages route correctly.
#
# NOTE: "project" was previously in the "initiatives" keyword list, but it's
# far too generic - it hijacked completely unrelated questions like "MSME
# project report" into fetching the live initiatives API instead of (or
# in a way that masked) the actual relevant static content. Keep dynamic
# route keywords specific to UPICON's own initiatives/programs, not any
# question that happens to contain the word "project".
DYNAMIC_ROUTES = [
    (
        ["job", "career", "vacancy", "opening", "hiring", "recruit", "apply",
         "naukri", "nokri", "bharti", "नौकरी", "भर्ती", "करियर"],
        "openings",
    ),
    (
        ["scheme", "yojana", "yojna", "योजना", "subsidy", "सब्सिडी",
         "cm yuva", "cmyuva", "yuva udyami", "yuva abhiyan",
         "मुख्यमंत्री युवा", "युवा उद्यमी", "युवा अभियान",
         "msme", "एमएसएमई"],
        "schemes",
    ),
    (
        ["news", "article", "update", "press", "समाचार", "खबर", "लेख"],
        "news_contents",
    ),
    (
        ["initiative", "flagship program", "flagship programme",
         "upicon's initiatives", "upicon programs", "पहल",
         "msme", "एमएसएमई"],
        "initiatives",
    ),
    (
        ["odop", "one district one product", "product of the month",
         "district special product", "ओडीओपी", "जिला उत्पाद",
         "spotlight", "स्पॉटलाइट"],
        "odop",
    ),
]


def _classify(message: str) -> set[str]:
    text = message.lower()
    matched = set()
    for keywords, route in DYNAMIC_ROUTES:
        if any(kw in text for kw in keywords):
            matched.add(route)
    return matched


def _flatten_items(items, fields: list[str]) -> list[str]:
    """Turn a list of API dicts into short plain-text blurbs."""
    blurbs = []
    if not isinstance(items, list):
        return blurbs
    for item in items:
        if not isinstance(item, dict):
            continue
        parts = [
            clean_html(str(item.get(f, "")).strip())
            for f in fields
            if item.get(f)
        ]
        parts = [p for p in parts if p]
        blurb = " — ".join(p for p in parts if p)
        if blurb:
            blurbs.append(blurb)
    return blurbs


async def _fetch_dynamic(route: str) -> list[str]:
    try:
        if route == "openings":
            data = await api_client.get_openings()
            items = data.get("data", data) if isinstance(data, dict) else data
            return _flatten_items(
                items, ["title", "location", "experience", "positions", "description"]
            )

        if route == "schemes":
            data = await api_client.get_schemes()
            items = data.get("data", data) if isinstance(data, dict) else data
            return _flatten_items(items, ["title", "name", "description", "details"])

        if route == "news_contents":
            home = await api_client.get_home_contents()
            home_items = home.get("data", home) if isinstance(home, dict) else home
            blurbs = _flatten_items(
                home_items, ["title", "category_name", "description"]
            )
            contents = await api_client.get_contents(category="All", offset=0)
            c_items = contents.get("data", contents) if isinstance(contents, dict) else contents
            blurbs += _flatten_items(
                c_items, ["title", "category_name", "description"]
            )
            return blurbs

        if route == "initiatives":
            data = await api_client.get_initiatives()
            items = data.get("data", data) if isinstance(data, dict) else data
            return _flatten_items(items, ["title", "description", "details"])

        if route == "odop":
            # Don't guess the category name — look it up via contentCategory
            # first (real UPICON API action), match anything ODOP-related,
            # then fetch that category's contents. Falls back to filtering
            # the general "All" contents feed if no matching category exists
            # or the category list itself is empty/unreachable.
            blurbs: list[str] = []
            real_category = None
            try:
                cats = await api_client.get_content_categories()
                cat_items = cats.get("data", cats) if isinstance(cats, dict) else cats
                if isinstance(cat_items, list):
                    for c in cat_items:
                        name = ""
                        if isinstance(c, dict):
                            name = str(
                                c.get("name") or c.get("category_name") or c.get("category") or ""
                            )
                        elif isinstance(c, str):
                            name = c
                        if "odop" in name.lower() or "one district one product" in name.lower():
                            real_category = name
                            break
            except Exception as exc:  # noqa: BLE001
                print(f"[router] contentCategory fetch failed: {exc}")

            if real_category:
                try:
                    data = await api_client.get_contents(category=real_category, offset=0)
                    items = data.get("data", data) if isinstance(data, dict) else data
                    blurbs += _flatten_items(
                        items, ["title", "category_name", "description", "details"]
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"[router] odop category fetch failed: {exc}")

            if not blurbs:
                # Page size is 12 (per API reference) - walk a few pages
                # since ODOP entries may not be in the first page of "All".
                for offset in (0, 12, 24, 36, 48):
                    data = await api_client.get_contents(category="All", offset=offset)
                    items = data.get("data", data) if isinstance(data, dict) else data
                    if not isinstance(items, list) or not items:
                        break
                    blurbs += [
                        b for b in _flatten_items(
                            items, ["title", "category_name", "description", "details"]
                        )
                        if "odop" in b.lower()
                    ]
                    if len(items) < 12:
                        break
            return blurbs
    except Exception as exc:  # noqa: BLE001 - degrade gracefully, don't crash chat
        print(f"[router] dynamic fetch failed for {route}: {exc}")
        return []
    return []


async def gather_context(message: str) -> list[str]:
    """Return a flat list of plain-text context snippets relevant to the
    user's message, pulled from whichever source(s) are relevant.

    Ordering matters: _apply_context_budget() below caps the total number
    of snippets, and a dynamic route (e.g. "schemes") can return dozens of
    generic items. Static/keyword hits are the ones that directly matched
    this specific question, so they go FIRST - otherwise a big dynamic
    dump could fill the whole budget and crowd out the actual answer
    before the LLM ever sees it.
    """
    static_context: list[str] = []
    dynamic_context: list[str] = []

    # Always try semantic search over the static crawl first — a query can
    # be about a specific page's details even when the keyword classifier
    # also fires a dynamic route, or it can be a purely static-content
    # question (About Us, contact info, general pages) with no dynamic
    # route at all.
    if vector_store.is_index_ready():
        # If the question names a page (e.g. "business units"), lead with
        # that page's own content so the answer has the real details.
        static_context.extend(h["text"] for h in vector_store.pages_matching_query(message))

        static_hits = vector_store.search(message)
        static_context.extend(t for t in (h["text"] for h in static_hits) if t not in static_context)

        # Hybrid retrieval: semantic search alone misses exact terms / rare
        # wording ("Business Unit"), and a query in different casing or with
        # a small typo can score just under MIN_SIMILARITY. So the
        # case-insensitive fuzzy full-text pass ALWAYS runs too; its hits
        # are added after the semantic ones (deduped). If semantic search
        # found nothing, keyword hits are the main context.
        seen = set(static_context)
        kw_limit = config.TOP_K if not static_hits else 3
        for hit in vector_store.keyword_search(message, top_k=kw_limit):
            if hit["text"] not in seen:
                static_context.append(hit["text"])
                seen.add(hit["text"])

    routes = _classify(message)
    for route in routes:
        dynamic_context.extend(await _fetch_dynamic(route))

    context = static_context + dynamic_context

    # Last resort: nothing crawled and nothing dynamic matched. Rather than
    # let the LLM answer with "I don't have that information," go fetch the
    # live site directly for a couple of likely-relevant pages.
    if not context:
        context.extend(await live_fetch.fetch_live_context(message))

    return _apply_context_budget(context)


def _apply_context_budget(context: list[str]) -> list[str]:
    """Dynamic fetches (news/schemes/openings/...) have no page-size limit,
    so a broad question can pull in dozens of items and blow past the LLM
    provider's per-request token limit (Groq's free tier is only 8,000 TPM).
    Trim any single long snippet, then keep snippets - most relevant first,
    since dynamic/static results are already appended in relevance order -
    until either the count cap or the total character budget is hit."""
    trimmed: list[str] = []
    total_chars = 0
    for snippet in context[: config.MAX_CONTEXT_SNIPPETS]:
        snippet = snippet.strip()
        if not snippet:
            continue
        if len(snippet) > config.MAX_SNIPPET_CHARS:
            snippet = snippet[: config.MAX_SNIPPET_CHARS].rstrip() + "…"
        if total_chars + len(snippet) > config.MAX_CONTEXT_CHARS:
            break
        trimmed.append(snippet)
        total_chars += len(snippet)
    return trimmed