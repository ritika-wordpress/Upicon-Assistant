"""
API crawler — pulls every UPICON *listing* API action (per the API
reference doc: openings, initiatives, homeInitiatives, homeContents,
contents [paginated, per real category], contentCategory, schemes, news)
and turns each item into a chunk record with the exact same shape crawler.py
produces from static pages: {id, url, title, text}.

Why this exists in addition to router.py's live dynamic fetch:
  - router.py fetches these same actions live, per-question, only when the
    question's keywords match a DYNAMIC_ROUTES entry. That's great for
    freshness but means a phrasing that doesn't hit a keyword (or a
    follow-up question in a different language/spelling) can miss dynamic
    content entirely.
  - Folding a snapshot of the same data into the static FAISS index means
    it's *also* reachable through ordinary semantic search, same as any
    crawled page — typo-tolerant, cross-language, no keyword list to keep
    in sync.
  - The live dynamic fetch in router.py stays as-is and still wins for
    freshness (it hits the API on every matching question); this snapshot
    is just a safety net refreshed on every re-crawl / /admin/reindex.

Run standalone to see counts without touching static_pages.json:
    python api_crawler.py

Normally you don't call this directly — crawler.run_full_crawl() calls
fetch_all_api_records() and merges the result in automatically.
"""

import asyncio

import api_client
import config
from crawler import _chunk_text, clean_html

# Fake "urls" so each API-derived chunk still carries a `url` field (used
# only internally for dedup/debugging — never shown to the end user, same
# rule as crawler.py's real page URLs).
_URL_OPENINGS = config.SITE_ROOT.rstrip("/") + "/careers"
_URL_INITIATIVES = config.SITE_ROOT.rstrip("/") + "/initiatives"
_URL_SCHEMES = config.SITE_ROOT.rstrip("/") + "/schemes"
_URL_NEWS = config.SITE_ROOT.rstrip("/") + "/industry-news"
_URL_CONTENTS = config.SITE_ROOT.rstrip("/") + "/articles"

# contents is paginated 12-at-a-time per the API reference; cap how many
# pages we walk per category so a runaway category can't hang a crawl.
_MAX_CONTENT_PAGES_PER_CATEGORY = 25


def _unwrap(data):
    """Every listing action can come back either as a bare list or as
    {"data": [...]} depending on the endpoint - normalize to a list."""
    if isinstance(data, dict):
        data = data.get("data", [])
    return data if isinstance(data, list) else []


def _item_to_text(item: dict, fields: list[str]) -> str:
    parts = [clean_html(str(item.get(f, "")).strip()) for f in fields if item.get(f)]
    return " — ".join(p for p in parts if p)


def _records_from_items(items: list[dict], fields: list[str], url: str, prefix: str) -> list[dict]:
    records = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        text = _item_to_text(item, fields)
        if not text:
            continue
        title = str(item.get("title") or item.get("name") or "")
        for j, chunk in enumerate(_chunk_text(text)):
            records.append(
                {"id": f"api:{prefix}:{item.get('id', i)}#{j}", "url": url, "title": title, "text": chunk}
            )
    return records


async def fetch_openings_records() -> list[dict]:
    try:
        data = await api_client.get_openings()
    except Exception as exc:  # noqa: BLE001 - degrade gracefully
        print(f"[api_crawler] openings fetch failed: {exc}")
        return []
    items = _unwrap(data)
    return _records_from_items(
        items, ["title", "location", "experience", "positions", "description"], _URL_OPENINGS, "openings"
    )


async def fetch_initiatives_records() -> list[dict]:
    records = []
    for action, fetcher in (("initiatives", api_client.get_initiatives), ("homeInitiatives", api_client.get_home_initiatives)):
        try:
            data = await fetcher()
        except Exception as exc:  # noqa: BLE001
            print(f"[api_crawler] {action} fetch failed: {exc}")
            continue
        items = _unwrap(data)
        records.extend(
            _records_from_items(items, ["title", "description", "details"], _URL_INITIATIVES, "initiatives")
        )
    # de-dupe: homeInitiatives is usually a featured subset of initiatives
    seen = set()
    deduped = []
    for r in records:
        key = r["text"]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    return deduped


async def fetch_schemes_records() -> list[dict]:
    try:
        data = await api_client.get_schemes()
    except Exception as exc:  # noqa: BLE001
        print(f"[api_crawler] schemes fetch failed: {exc}")
        return []
    items = _unwrap(data)
    return _records_from_items(items, ["title", "name", "description", "details"], _URL_SCHEMES, "schemes")


async def fetch_news_records() -> list[dict]:
    try:
        data = await api_client.get_news()
    except Exception as exc:  # noqa: BLE001
        print(f"[api_crawler] news fetch failed: {exc}")
        return []
    items = _unwrap(data)
    return _records_from_items(
        items, ["title", "category_name", "description", "details"], _URL_NEWS, "news"
    )


async def _category_names() -> list[str]:
    """Real category names via contentCategory, plus 'All' as a catch-all
    (some content may not be tagged to any of the listed categories)."""
    names = ["All"]
    try:
        data = await api_client.get_content_categories()
    except Exception as exc:  # noqa: BLE001
        print(f"[api_crawler] contentCategory fetch failed: {exc}")
        return names

    for c in _unwrap(data):
        name = None
        if isinstance(c, dict):
            name = c.get("name") or c.get("category_name") or c.get("category")
        elif isinstance(c, str):
            name = c
        if name and name not in names:
            names.append(name)
    return names


async def fetch_contents_records() -> list[dict]:
    """Walk every real category (per contentCategory) through pagination
    (page size 12, per the API reference) until a short page ends it."""
    records = []
    seen_ids: set = set()
    categories = await _category_names()

    for category in categories:
        offset = 0
        for _ in range(_MAX_CONTENT_PAGES_PER_CATEGORY):
            try:
                data = await api_client.get_contents(category=category, offset=offset)
            except Exception as exc:  # noqa: BLE001
                print(f"[api_crawler] contents fetch failed (category={category}, offset={offset}): {exc}")
                break
            items = _unwrap(data)
            if not items:
                break

            new_items = [it for it in items if isinstance(it, dict) and it.get("id") not in seen_ids]
            for it in new_items:
                seen_ids.add(it.get("id"))
            records.extend(
                _records_from_items(
                    new_items,
                    ["title", "category_name", "description", "details"],
                    _URL_CONTENTS,
                    "contents",
                )
            )

            if len(items) < 12:
                break
            offset += 12

    return records


async def fetch_all_api_records() -> list[dict]:
    """Fetch + flatten every listing API action into chunk records ready
    to merge alongside crawler.py's static-page records."""
    groups = await asyncio.gather(
        fetch_openings_records(),
        fetch_initiatives_records(),
        fetch_schemes_records(),
        fetch_news_records(),
        fetch_contents_records(),
        return_exceptions=True,
    )

    records = []
    for group in groups:
        if isinstance(group, Exception):
            print(f"[api_crawler] a fetch group failed: {group}")
            continue
        records.extend(group)
    return records


async def _main():
    records = await fetch_all_api_records()
    by_prefix: dict[str, int] = {}
    for r in records:
        prefix = r["id"].split(":")[1]
        by_prefix[prefix] = by_prefix.get(prefix, 0) + 1
    print(f"[api_crawler] {len(records)} total chunk(s):")
    for prefix, count in sorted(by_prefix.items()):
        print(f"  {prefix}: {count}")


if __name__ == "__main__":
    asyncio.run(_main())