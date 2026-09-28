"""
Live fallback fetch.

router.gather_context() tries dynamic API routes, then the crawled+embedded
static index. If a real question still comes back with *no* context at all
(something new on the site that hasn't been crawled yet, a page outside the
sitemap's usual sections, etc.), this module is the last resort: it fetches
upicon.in directly — a couple of likely-relevant pages, not a full re-crawl —
so the visitor gets a real, grounded answer instead of the LLM's canned "I
don't have that information on the website".

Kept cheap and safe:
  - the sitemap URL list is cached for a while (LIVE_SITEMAP_CACHE_SECONDS)
    so a burst of misses doesn't refetch it every time
  - each crawled page's text is cached too (LIVE_PAGE_CACHE_SECONDS), so
    repeated/similar questions in the same window don't hammer the live site
  - candidate pages are picked by keyword overlap between the question and
    each URL's slug, so we crawl a small handful of relevant pages instead
    of the whole site
  - anything that scores well is folded into the in-memory FAISS index (see
    vector_store.add_live_records), so a follow-up on the same topic is
    served instantly next time without hitting the site again
  - never raises — any failure here just means an empty context list, same
    as "nothing matched", not a broken chat turn
"""

"""
Live fallback fetch.

router.gather_context() tries dynamic API routes, then the crawled+embedded
static index. If a real question still comes back with *no* context at all
(something new on the site that hasn't been crawled yet, a page outside the
sitemap's usual sections, etc.), this module is the last resort: it scrapes
upicon.in directly — a couple of likely-relevant pages, not a full re-crawl —
so the visitor gets a real, grounded answer instead of the LLM's canned "I
don't have that information on the website".

Two ways candidate pages are found, combined:
  1. sitemap.xml — the usual, fast path
  2. actual web scraping — hrefs pulled straight off the live homepage (and,
     if the first pass finds nothing, off whatever page it did manage to
     fetch), so pages that are new, unlisted, or missing from the sitemap
     are still reachable

Kept cheap and safe:
  - the sitemap URL list and the scraped link list are both cached for a
    while (LIVE_SITEMAP_CACHE_SECONDS) so a burst of misses doesn't refetch
    them every time
  - each crawled page's text is cached too (LIVE_PAGE_CACHE_SECONDS), so
    repeated/similar questions in the same window don't hammer the live site
  - candidate pages are picked by keyword overlap between the question and
    each URL's slug, so we crawl a small handful of relevant pages instead
    of the whole site
  - anything that scores well is folded into the in-memory FAISS index (see
    vector_store.add_live_records), so a follow-up on the same topic is
    served instantly next time without hitting the site again
  - never raises — any failure here just means an empty context list, same
    as "nothing matched", not a broken chat turn
"""

import re
import time

import httpx

import config
import crawler
import vector_store

_sitemap_cache: dict = {"urls": [], "ts": 0.0}
_link_cache: dict[str, dict] = {}  # page url -> {"urls": [...], "ts": float}
_page_cache: dict[str, dict] = {}  # url -> {"text": str, "ts": float}

_STOPWORDS = {
    "the", "a", "an", "is", "are", "what", "how", "who", "when", "where",
    "why", "does", "do", "of", "for", "on", "in", "to", "about", "tell",
    "me", "please", "and", "or", "any", "there",
    # common Hindi function words (Latin transliteration)
    "kya", "hai", "ka", "ki", "ke", "kaun", "kaise", "kab", "kahan",
    "baare", "bare", "mein", "batao", "bataiye", "koi",
}


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z\u0900-\u097F]+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


async def _get_sitemap_urls() -> list[str]:
    now = time.time()
    if _sitemap_cache["urls"] and (now - _sitemap_cache["ts"]) < config.LIVE_SITEMAP_CACHE_SECONDS:
        return _sitemap_cache["urls"]

    try:
        urls = await crawler.fetch_sitemap_urls()
    except Exception as exc:  # noqa: BLE001 - degrade gracefully
        print(f"[live_fetch] sitemap fetch failed: {exc}")
        urls = _sitemap_cache["urls"] or []

    _sitemap_cache["urls"] = urls
    _sitemap_cache["ts"] = now
    return urls


async def _scrape_links(page_url: str) -> list[str]:
    """Find internal links on a page. Tries crawl4ai's JS-rendered crawl
    first (catches nav links that only exist after client-side JS runs),
    and falls back to a plain HTTP fetch + regex if that fails - this is
    what finds pages the sitemap doesn't list."""
    now = time.time()
    cached = _link_cache.get(page_url)
    if cached and (now - cached["ts"]) < config.LIVE_SITEMAP_CACHE_SECONDS:
        return cached["urls"]

    found: set[str] = set()

    try:
        found.update(await crawler.discover_links(page_url))
    except Exception as exc:  # noqa: BLE001 - degrade gracefully
        print(f"[live_fetch] JS link discovery failed for {page_url}: {exc}")

    if not found:
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                resp = await client.get(page_url)
                resp.raise_for_status()
                html = resp.text

            for href in re.findall(r'href=["\']([^"\']+)["\']', html):
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
            print(f"[live_fetch] raw HTML link scrape failed for {page_url}: {exc}")

    urls = list(found)
    _link_cache[page_url] = {"urls": urls, "ts": now}
    return urls


def _rank_candidate_urls(query: str, urls: list[str], max_pages: int) -> list[str]:
    q_tokens = _tokenize(query)
    if not q_tokens:
        return []

    scored = []
    for url in urls:
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        slug_tokens = _tokenize(slug.replace("-", " ").replace("_", " "))
        overlap = len(q_tokens & slug_tokens)
        if overlap:
            scored.append((overlap, url))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [url for _, url in scored[:max_pages]]


async def _get_page_text(url: str) -> str:
    now = time.time()
    cached = _page_cache.get(url)
    if cached and (now - cached["ts"]) < config.LIVE_PAGE_CACHE_SECONDS:
        return cached["text"]

    try:
        pages = await crawler.crawl_all_pages([url])
    except Exception as exc:  # noqa: BLE001 - degrade gracefully
        print(f"[live_fetch] page fetch failed for {url}: {exc}")
        pages = []

    text = pages[0]["text"] if pages else ""
    _page_cache[url] = {"text": text, "ts": now}
    return text


def _score_hits(query: str, records: list[dict]) -> list[dict]:
    if not records:
        return []
    texts = [r["text"] for r in records]
    chunk_vecs = vector_store._embed(texts)
    query_vec = vector_store._embed([vector_store.expand_query_for_embedding(query)])[0]
    sims = chunk_vecs @ query_vec
    ranked = sorted(zip(sims.tolist(), records), key=lambda x: x[0], reverse=True)
    return [rec for score, rec in ranked if score >= config.LIVE_MIN_SIMILARITY][: config.TOP_K]


async def _fetch_and_chunk(urls: list[str]) -> list[dict]:
    records = []
    for url in urls:
        text = await _get_page_text(url)
        if not text:
            continue
        for i, chunk in enumerate(crawler._chunk_text(text)):
            records.append({"id": f"live:{url}#{i}", "url": url, "title": "", "text": chunk})
    return records


async def fetch_live_context(query: str) -> list[str]:
    """Best-effort: scrape a couple of likely-relevant live pages and return
    only the passages that actually match the query well enough to use."""
    if not config.LIVE_FETCH_ENABLED:
        return []

    try:
        # Pool candidate URLs from both sources: the sitemap, and links
        # scraped straight off the live homepage (catches pages the sitemap
        # hasn't picked up yet).
        sitemap_urls = await _get_sitemap_urls()
        scraped_urls = await _scrape_links(config.SITE_ROOT)
        # EXTRA_STATIC_URLS are always in the pool - independent of keyword
        # ranking below - so a page like /odop is reachable even if the
        # question's wording doesn't overlap with its URL slug.
        pool = list(dict.fromkeys(sitemap_urls + scraped_urls + config.EXTRA_STATIC_URLS))

        candidates = _rank_candidate_urls(query, pool, config.LIVE_MAX_CANDIDATE_PAGES)
        # Always keep any EXTRA_STATIC_URLS pages in the running too, even if
        # they didn't win the keyword ranking above.
        for extra in config.EXTRA_STATIC_URLS:
            if extra not in candidates and len(candidates) < config.LIVE_MAX_CANDIDATE_PAGES + len(config.EXTRA_STATIC_URLS):
                candidates.append(extra)
        if not candidates:
            candidates = [config.SITE_ROOT]

        records = await _fetch_and_chunk(candidates)
        hits = _score_hits(query, records)

        if not hits:
            # Second pass: scrape links *off* whatever pages we just fetched
            # (one hop deeper — e.g. homepage -> "Schemes" listing ->
            # individual scheme pages) and try again with that wider pool.
            deeper_urls: set[str] = set()
            for url in candidates:
                for link in await _scrape_links(url):
                    if link not in pool:
                        deeper_urls.add(link)

            if deeper_urls:
                deeper_candidates = _rank_candidate_urls(
                    query, list(deeper_urls), config.LIVE_MAX_CANDIDATE_PAGES
                )
                deeper_records = await _fetch_and_chunk(deeper_candidates)
                hits = _score_hits(query, deeper_records)

        if not hits:
            return []

        # remember it for the rest of this process's lifetime
        vector_store.add_live_records(hits)

        return [h["text"] for h in hits]
    except Exception as exc:  # noqa: BLE001 - never break a chat turn
        print(f"[live_fetch] unexpected failure: {exc}")
        return []