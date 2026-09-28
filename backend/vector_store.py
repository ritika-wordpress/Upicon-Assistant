"""
Semantic search over the crawled static content.

Uses a multilingual sentence-transformer to embed every chunk plus the
incoming user query into the same vector space, then FAISS for fast
cosine-similarity search. Because matching happens on *meaning* rather than
exact keywords, this is what gives the bot typo/spelling-mistake tolerance
and cross-language matching (Hindi query -> English page content, etc.).
"""

import json
import os
import pickle
import re
import threading

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

import config

_model = None
_model_lock = threading.Lock()

_index = None
_meta: list[dict] = []
_index_lock = threading.Lock()


def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                _model = SentenceTransformer(config.EMBEDDING_MODEL)
    return _model


def expand_query_for_embedding(query: str) -> str:
    """A bare acronym/keyword query ("msme", "odop") embeds too weakly
    against full descriptive page chunks to clear MIN_SIMILARITY, even
    when the site clearly covers the topic - a fuller sentence ("what is
    msme") embeds fine. Pad short queries with generic framing so the
    query vector lands nearer the same semantic neighborhood as an actual
    descriptive sentence, without changing what's being asked. Only for
    retrieval/scoring - never shown to the user or sent to the LLM."""
    words = query.strip().split()
    if len(words) <= 2:
        return f"What is {query} on the UPICON website? Information and details."
    return query


def _embed(texts: list[str]) -> np.ndarray:
    model = get_model()
    vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return np.asarray(vecs, dtype="float32")


def build_index():
    """Read data/static_pages.json (produced by crawler.py) and build a
    fresh FAISS index. Call this once after every crawl."""
    if not os.path.exists(config.STATIC_JSON_PATH):
        raise FileNotFoundError(
            f"{config.STATIC_JSON_PATH} not found — run crawler.py first."
        )

    with open(config.STATIC_JSON_PATH, "r", encoding="utf-8") as f:
        records = json.load(f)

    if not records:
        raise ValueError("static_pages.json is empty — nothing to index.")

    texts = [r["text"] for r in records]
    vectors = _embed(texts)

    dim = vectors.shape[1]
    index = faiss.IndexFlatIP(dim)  # cosine similarity, since vectors are normalized
    index.add(vectors)

    faiss.write_index(index, config.FAISS_INDEX_PATH)
    with open(config.FAISS_META_PATH, "wb") as f:
        pickle.dump(records, f)

    print(f"[vector_store] indexed {len(records)} chunks (dim={dim})")
    return index, records


def _load_index():
    global _index, _meta
    with _index_lock:
        if _index is not None:
            return
        if not (os.path.exists(config.FAISS_INDEX_PATH) and os.path.exists(config.FAISS_META_PATH)):
            raise FileNotFoundError(
                "FAISS index not found — run `python build_index.py` after crawling."
            )
        _index = faiss.read_index(config.FAISS_INDEX_PATH)
        with open(config.FAISS_META_PATH, "rb") as f:
            _meta = pickle.load(f)


def search(query: str, top_k: int = config.TOP_K) -> list[dict]:
    """Return the top_k most semantically similar chunks to `query`, each as
    {text, title, score}. Filters out weak matches below MIN_SIMILARITY so
    off-topic questions come back empty instead of forcing a bad answer."""
    _load_index()
    if _index is None or _index.ntotal == 0:
        return []

    q_vec = _embed([expand_query_for_embedding(query)])
    scores, idxs = _index.search(q_vec, top_k)

    results = []
    for score, idx in zip(scores[0], idxs[0]):
        if idx == -1:
            continue
        if float(score) < config.MIN_SIMILARITY:
            continue
        record = _meta[idx]
        results.append(
            {
                "text": record["text"],
                "title": record.get("title", ""),
                "score": float(score),
            }
        )
    return results


_KW_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "what", "whats", "how", "who",
    "when", "where", "why", "which", "does", "do", "did", "of", "for", "on", "in",
    "to", "about", "tell", "me", "please", "and", "or", "any", "there", "give",
    "explain", "details", "detail", "info", "information", "define", "meaning",
    "can", "you", "i", "want", "know", "my", "your", "our", "us",
    # Hindi function words (Latin transliteration)
    "kya", "hai", "hain", "ka", "ki", "ke", "ko", "kaun", "kaise", "kab", "kahan",
    "baare", "bare", "baarein", "mein", "me", "batao", "bataiye", "bataye", "koi",
    "aur", "se", "yeh", "ye", "wo", "vo",
    # Hindi function words (Devanagari)
    "क्या", "है", "हैं", "का", "की", "के", "को", "कौन", "कैसे", "कब", "कहाँ",
    "बारे", "में", "बताओ", "बताइए", "बताएं", "कोई", "और", "से", "यह", "वह",
}
_choices_cache: dict = {"key": None, "vals": []}


def _lowered_choices() -> list[str]:
    """Lower-cased copy of every chunk, cached so we don't re-lowercase the
    whole corpus on every query (rebuilt when the index/meta changes)."""
    key = (id(_meta), len(_meta))
    if _choices_cache["key"] != key:
        _choices_cache["vals"] = [r["text"].lower() for r in _meta]
        _choices_cache["key"] = key
    return _choices_cache["vals"]


def _core_query_tokens(query: str) -> list[str]:
    """Strip question filler ("what is", "tell me about", "kya hai") so
    'what is business unit' is matched as the phrase 'business unit'.
    Falls back to all words if nothing is left."""
    words = re.findall(r"[a-zA-Z0-9\u0900-\u097F]+", query.lower())
    core = [w for w in words if w not in _KW_STOPWORDS]
    return core or words


def keyword_search(query: str, top_k: int = config.TOP_K, min_score: int = 75) -> list[dict]:
    """Fuzzy, case-insensitive full-text scan across every crawled chunk on
    the whole site - a lexical net for wording the embedding model doesn't
    line up well with (exact scheme names, rare terms like "Business Unit",
    small typos). Both the query and the chunks are lower-cased, question
    filler words are dropped, and chunks that contain more of the query's
    words rank first. min_score is a rapidfuzz partial_ratio (0-100)."""
    _load_index()
    if not _meta:
        return []

    from rapidfuzz import fuzz, process

    core = _core_query_tokens(query)
    if not core:
        return []
    phrase = " ".join(core)

    choices = _lowered_choices()
    matches = process.extract(phrase, choices, scorer=fuzz.partial_ratio, limit=max(top_k * 10, 50))

    scored = []
    for _text, score, idx in matches:
        if score < min_score:
            continue
        text = choices[idx]
        coverage = sum(1 for w in core if w in text) / len(core)
        scored.append((coverage, score, idx))
    scored.sort(reverse=True)

    results = []
    seen_ids = set()
    for coverage, score, idx in scored:
        rec = _meta[idx]
        rid = rec.get("id", idx)
        if rid in seen_ids:
            continue
        seen_ids.add(rid)
        results.append({"text": rec["text"], "title": rec.get("title", ""), "score": score / 100})
        if len(results) >= top_k:
            break
    return results


_MD_LINK_RE = re.compile(r"\[[^\]]*\]\([^)]*\)")


def _is_nav_chunk(text: str) -> bool:
    """Crawled pages start with the site menu (lots of [Home](url) links).
    Those chunks carry no real content and would eat the context budget."""
    links = _MD_LINK_RE.findall(text)
    if len(links) < 4:
        return False
    link_chars = sum(len(l) for l in links)
    return link_chars / max(len(text), 1) >= 0.45


def pages_matching_query(query: str, max_chunks: int = 8) -> list[dict]:
    """If the question names a page of the site (e.g. "business units" ->
    /business-units, "odop" -> /odop), return that page's own content
    chunks in reading order. This is what lets the bot answer with the
    page's real details instead of only a navigation-menu mention of it,
    which is what semantic/keyword search tend to surface first."""
    try:
        _load_index()
    except FileNotFoundError:
        return []
    if not _meta:
        return []

    def norm(w: str) -> str:
        return w[:-1] if len(w) > 3 and w.endswith("s") else w

    q = {norm(w) for w in _core_query_tokens(query)}
    if not q:
        return []
    compact_q = re.sub(r"[^a-z0-9]", "", query.lower())

    best_url, best_len = None, 0
    seen_urls = set()
    for rec in _meta:
        url = rec.get("url", "")
        rid = str(rec.get("id", ""))
        if not url or rid.startswith("api:") or url in seen_urls:
            continue
        seen_urls.add(url)
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        if "." in slug or url.rstrip("/") == config.SITE_ROOT.rstrip("/"):
            continue  # domain root / non-page
        slug_tokens = {norm(t) for t in re.split(r"[-_]+", slug.lower()) if t}
        slug_compact = re.sub(r"[^a-z0-9]", "", slug.lower())
        matched = slug_tokens <= q or (len(slug_compact) >= 6 and slug_compact in compact_q)
        if slug_tokens and matched and len(slug_tokens) > best_len:
            best_url, best_len = url, len(slug_tokens)

    # Explicit aliases win over slug matching (e.g. "yuva udyami" -> /cmyuva).
    q_lower = query.lower()
    for slug_key, aliases in getattr(config, "PAGE_ALIASES", {}).items():
        if any(a.lower() in q_lower or re.sub(r"[^a-z0-9]", "", a.lower()) in compact_q and len(re.sub(r"[^a-z0-9]", "", a.lower())) >= 5 for a in aliases):
            for url in seen_urls:
                if url.rstrip("/").rsplit("/", 1)[-1].lower() == slug_key:
                    best_url = url
                    break
            if best_url and best_url.rstrip("/").rsplit("/", 1)[-1].lower() == slug_key:
                break

    if not best_url:
        return []

    page = [
        rec for rec in _meta
        if rec.get("url") == best_url and not str(rec.get("id", "")).startswith("api:")
    ]
    # Drop menu/footer link-lists so real page content gets the budget
    # (falls back to everything if that would leave nothing).
    content_only = [r for r in page if not _is_nav_chunk(r["text"])]
    page = content_only or page

    if len(page) <= max_chunks:
        chosen = page
    else:
        # Page is longer than the budget: keep the intro (first 3 chunks) plus
        # the remaining chunks that share the most words with the question,
        # so a specific question ("eligibility", "how to apply") still finds
        # its answer deeper in the page.
        slug = best_url.rstrip("/").rsplit("/", 1)[-1].lower()
        words = {w for w in _core_query_tokens(query) if w not in slug and len(w) > 2}
        head = page[:3]
        rest = page[3:]
        rest_scored = sorted(
            range(len(rest)),
            key=lambda i: (-sum(1 for w in words if w in rest[i]["text"].lower()), i),
        )
        keep = sorted(rest_scored[: max_chunks - 3])
        chosen = head + [rest[i] for i in keep]

    return [{"text": r["text"], "title": r.get("title", ""), "score": 1.0} for r in chosen]


def is_index_ready() -> bool:
    return os.path.exists(config.FAISS_INDEX_PATH) and os.path.exists(config.FAISS_META_PATH)


def add_live_records(records: list[dict]):
    """Fold freshly live-fetched site chunks (see live_fetch.py) into the
    in-memory index so a later, similar question in this same process hits
    the fast static-search path instead of re-fetching the site.

    In-memory only — this does NOT touch the FAISS files on disk. The
    on-disk index stays whatever `python crawler.py && python
    build_index.py` (or `/admin/reindex`) last produced; that remains the
    source of truth and survives a restart. This just avoids repeat live
    fetches within one running process.
    """
    global _index, _meta
    if not records:
        return

    texts = [r["text"] for r in records]
    vectors = _embed(texts)

    try:
        _load_index()  # populate _index/_meta from disk first, if present
    except FileNotFoundError:
        pass

    with _index_lock:
        if _index is None:
            dim = vectors.shape[1]
            _index = faiss.IndexFlatIP(dim)
            _meta = []
        _index.add(vectors)
        _meta.extend(records)