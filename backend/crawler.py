"""
Static-content crawler.

Reads every URL out of https://upicon.in/sitemap.xml, crawls each page with
crawl4ai (headless-browser based, JS-rendered, returns clean markdown), splits
the text into overlapping chunks, and saves them to data/static_pages.json.

Run standalone to (re)build the static corpus:
    python crawler.py

Then run `python build_index.py` (or call vector_store.build_index()) to embed
the chunks into the FAISS index used for semantic search.
"""

import asyncio
import json
import re
import xml.etree.ElementTree as ET

import httpx
from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler

import config
# NOTE: api_crawler is imported lazily inside run_full_crawl(), not here at
# module level - api_crawler.py itself imports `_chunk_text` from this file,
# so importing it up here would be a circular import.

# Some pages (e.g. the homepage's "100+ Partnerships" style stats) render
# their real numbers via a JS count-up animation that starts at 0 and ticks
# up over ~1-2s after load. A plain crawl4ai snapshot can grab the DOM mid
# (or pre-) animation and capture "0+" or an empty string instead of the
# real value. Scrolling the stats into view (in case the animation is
# scroll-triggered) and then giving it a moment to finish before we read the
# HTML fixes that.
COUNTER_ANIMATION_DELAY = 2.5  # seconds to wait after load/scroll before snapshotting
_COUNTER_SCROLL_JS = """
(() => {
    window.scrollTo(0, document.body.scrollHeight / 2);
    window.scrollTo(0, document.body.scrollHeight);
    window.scrollTo(0, 0);
})();
"""


def clean_html(text: str) -> str:
    """Strip HTML tags/entities from raw rich-text fields before they're
    chunked/embedded or shown to the LLM. Shared by api_crawler.py (baking a
    snapshot into the static index) and router.py (live dynamic fetches) so
    there's exactly one place this logic lives - it used to be duplicated
    in both, and only one copy ever got fixed when the issue was first
    found. Some UPICON API fields (e.g. initiatives 'description') return
    raw HTML - unescaped, this shows up as literal '<p>', '&nbsp;', etc."""
    if not text or ("<" not in text and "&" not in text):
        return text
    return BeautifulSoup(text, "html.parser").get_text(separator=" ").strip()


def _chunk_text(text: str, size: int = config.CHUNK_SIZE, overlap: int = config.CHUNK_OVERLAP):
    """Simple sliding-window character chunker with overlap, breaking on
    whitespace boundaries so we don't cut words in half."""
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []

    chunks = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + size, n)
        # try to end on a sentence/paragraph boundary
        if end < n:
            boundary = text.rfind("\n", start, end)
            if boundary == -1 or boundary <= start + int(size * 0.4):
                boundary = text.rfind(". ", start, end)
            if boundary != -1 and boundary > start + int(size * 0.4):
                end = boundary + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks


async def fetch_sitemap_urls(sitemap_url: str = config.SITEMAP_URL) -> list[str]:
    """Fetch and parse sitemap.xml (handles plain sitemaps and sitemap
    index files that point to child sitemaps)."""
    urls: list[str] = []
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(sitemap_url)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    # sitemap index -> recurse into each child sitemap
    sitemap_tags = root.findall("sm:sitemap/sm:loc", ns)
    if sitemap_tags:
        for tag in sitemap_tags:
            child_urls = await fetch_sitemap_urls(tag.text.strip())
            urls.extend(child_urls)
        return urls

    for loc in root.findall("sm:url/sm:loc", ns):
        if loc.text:
            urls.append(loc.text.strip())
    return urls


async def discover_links(page_url: str) -> list[str]:
    """Find links on a page using crawl4ai's JS-rendered crawl (not a plain
    HTTP fetch), so nav links that only appear after client-side JS runs
    (e.g. a React/Vue nav menu) are still found. Falls back to whatever raw
    <a href> crawl4ai's cleaned_html/links data gives us on failure."""
    found: set[str] = set()
    try:
        async with AsyncWebCrawler(verbose=False) as crawler:
            result = await crawler.arun(
                url=page_url,
                bypass_cache=True,
                js_code=_COUNTER_SCROLL_JS,
                delay_before_return_html=1.5,
            )
        if result and result.success:
            raw_links = []
            if getattr(result, "links", None):
                internal = result.links.get("internal", []) if isinstance(result.links, dict) else []
                raw_links = [l.get("href", "") if isinstance(l, dict) else str(l) for l in internal]
            if not raw_links:
                html = result.cleaned_html or result.html or ""
                raw_links = re.findall(r'href=["\']([^"\']+)["\']', html)

            for href in raw_links:
                href = href.split("#")[0].split("?")[0].strip()
                if not href or href.startswith(("javascript:", "mailto:", "tel:")):
                    continue
                if href.startswith("/"):
                    href = config.SITE_ROOT.rstrip("/") + href
                if href.startswith(config.SITE_ROOT) and not href.lower().endswith(
                    (".pdf", ".jpg", ".jpeg", ".png", ".zip", ".doc", ".docx")
                ):
                    found.add(href)
    except Exception as exc:  # noqa: BLE001 - degrade gracefully
        print(f"[crawler] link discovery failed for {page_url}: {exc}")
    return list(found)


async def crawl_all_pages(urls: list[str]) -> list[dict]:
    """Crawl every URL with crawl4ai and return [{url, title, text}, ...]."""
    pages = []
    async with AsyncWebCrawler(verbose=False) as crawler:
        for url in urls:
            try:
                result = await crawler.arun(
                    url=url,
                    bypass_cache=True,
                    # let scroll-triggered / on-load JS count-up animations
                    # (e.g. homepage "100+ Partnerships") finish before we
                    # capture the DOM - see COUNTER_ANIMATION_DELAY above.
                    js_code=_COUNTER_SCROLL_JS,
                    delay_before_return_html=COUNTER_ANIMATION_DELAY,
                )
            except Exception as exc:  # noqa: BLE001 - keep crawling on single-page failure
                print(f"[crawler] failed: {url} ({exc})")
                continue

            if not result or not result.success:
                print(f"[crawler] no content: {url}")
                continue

            text = (result.markdown or result.cleaned_html or "").strip()
            if not text:
                continue

            title = ""
            if result.metadata:
                title = result.metadata.get("title", "") or ""

            pages.append({"url": url, "title": title, "text": text})
            print(f"[crawler] ok: {url} ({len(text)} chars)")
    return pages


def build_chunk_records(pages: list[dict]) -> list[dict]:
    """Turn crawled pages into chunk-level records ready for embedding.
    Internal 'source' (url) is kept ONLY in this backend file — it must
    never be surfaced to the end user, see llm.py's system prompt."""
    records = []
    for page in pages:
        chunks = _chunk_text(page["text"])
        for i, chunk in enumerate(chunks):
            records.append(
                {
                    "id": f"{page['url']}#{i}",
                    "url": page["url"],
                    "title": page["title"],
                    "text": chunk,
                }
            )
    return records


async def run_full_crawl():
    print("[crawler] fetching sitemap...")
    urls = await fetch_sitemap_urls()
    print(f"[crawler] {len(urls)} urls found in sitemap")

    # Always include pages that matter but might not be in the sitemap.
    merged = list(dict.fromkeys(urls + config.EXTRA_STATIC_URLS))
    added = len(merged) - len(urls)
    if added:
        print(f"[crawler] +{added} url(s) from EXTRA_STATIC_URLS")
    urls = merged

    pages = await crawl_all_pages(urls)
    records = build_chunk_records(pages)

    # Also fold in a snapshot of every UPICON *listing* API action (jobs,
    # initiatives, schemes, news, contents/articles) so it's reachable via
    # ordinary semantic search too, not only when router.py's keyword
    # classifier fires a live dynamic fetch. See api_crawler.py.
    import api_crawler  # lazy import - see note near the top of this file

    print("[crawler] fetching API listing data...")
    try:
        api_records = await api_crawler.fetch_all_api_records()
    except Exception as exc:  # noqa: BLE001 - never let API hiccups kill a static crawl
        print(f"[crawler] API fetch failed, continuing with static content only: {exc}")
        api_records = []
    print(f"[crawler] {len(api_records)} chunk(s) from API listing data")
    records += api_records

    with open(config.STATIC_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    print(f"[crawler] saved {len(records)} chunks -> {config.STATIC_JSON_PATH}")
    return records


if __name__ == "__main__":
    asyncio.run(run_full_crawl())