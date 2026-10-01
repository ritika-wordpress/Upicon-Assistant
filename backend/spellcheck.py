"""
Lightweight spelling correction for visitor queries - tuned for people
typing quickly/casually on a phone (typos, phonetic Hindi-English spellings,
dropped letters, etc.).

The vocabulary is built from the site's own crawled content
(data/static_pages.json), so a "corrected" word is always a real term that
actually appears somewhere on upicon.in - this can never invent or
substitute an unrelated word.

Deliberately conservative: a word already in the vocabulary is left alone;
an unknown word is only replaced if a very close match exists (ratio >=
_MATCH_THRESHOLD). Ordinary words that simply aren't on the site (numbers,
common verbs, "hi", "please"...) are left as-is rather than getting
mangled into whatever happens to be the nearest site term.
"""

import json
import re
import threading

from rapidfuzz import fuzz, process

import config

_vocab: list[str] | None = None
_vocab_set: set[str] = set()
_lock = threading.Lock()

_MATCH_THRESHOLD = 84  # 0-100, rapidfuzz ratio - conservative on purpose
# Short words are easy to 'correct' into the wrong word (odo -> odor), so they
# need a near-exact match. Domain terms (config.DOMAIN_TERMS) get first pick.
_SHORT_WORD_MAX_LEN = 4
_SHORT_WORD_THRESHOLD = 90
_DOMAIN_THRESHOLD = 80
_MIN_WORD_LEN = 3  # don't try to "correct" very short tokens (of, ka, ke, hi, ...)


def _build_vocab() -> list[str]:
    words: set[str] = set()
    try:
        with open(config.STATIC_JSON_PATH, "r", encoding="utf-8") as f:
            records = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

    for r in records:
        text = f"{r.get('title', '')} {r.get('text', '')}"
        for w in re.findall(r"[a-zA-Z\u0900-\u097F]+", text.lower()):
            if len(w) >= _MIN_WORD_LEN:
                words.add(w)
    return list(words)


def _get_vocab() -> list[str]:
    global _vocab, _vocab_set
    if _vocab is None:
        with _lock:
            if _vocab is None:
                _vocab = _build_vocab()
                _vocab_set = set(_vocab)
    return _vocab


def refresh_vocab() -> None:
    """Call after a reindex (new crawl) so newly-added site terms become
    correctable immediately instead of waiting for the next process
    restart. Cheap to call - just rebuilds an in-memory word set."""
    global _vocab, _vocab_set
    with _lock:
        _vocab = _build_vocab()
        _vocab_set = set(_vocab)


def correct_query(text: str) -> str:
    """Best-effort spelling correction against real site vocabulary. Safe
    to call on every query - clean or already-correct words pass through
    unchanged, and a word with no close site-vocabulary match is left as
    typed rather than forced into something wrong."""
    vocab = _get_vocab()
    if not vocab:
        return text

    def _fix(match: re.Match) -> str:
        word = match.group(0)
        lower = word.lower()
        if len(lower) < _MIN_WORD_LEN:
            return word
        domain = getattr(config, "DOMAIN_TERMS", [])
        if lower in domain or lower in getattr(config, "PROTECTED_WORDS", ()):
            return word
        # 1) mistyped UPICON term? pull it toward the real term first
        if lower not in _vocab_set and domain:
            d = process.extractOne(lower, domain, scorer=fuzz.ratio)
            if d and d[1] >= _DOMAIN_THRESHOLD:
                return d[0]
        if lower in _vocab_set:
            return word
        # 2) general site vocabulary, stricter for very short words
        threshold = _SHORT_WORD_THRESHOLD if len(lower) <= _SHORT_WORD_MAX_LEN else _MATCH_THRESHOLD
        result = process.extractOne(lower, vocab, scorer=fuzz.ratio)
        if result and result[1] >= threshold:
            return result[0]
        return word

    # Latin words only: correcting Devanagari words against the vocabulary
    # tends to swap a correct Hindi word for a different, similar one.
    return re.sub(r"[a-zA-Z]+", _fix, text)