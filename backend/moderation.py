"""
Abusive / offensive language detector.

Runs BEFORE retrieval and the LLM, so an abusive message costs nothing and
never reaches the model. Covers English, Hinglish (Hindi in English letters)
and Devanagari, and tolerates the usual tricks: repeated letters
("fuuuck"), symbol swaps ("f@ck", "sh!t"), spaced-out letters ("f u c k"),
and common misspellings (fuzzy match on longer words).

Matching is per WHOLE WORD on purpose, so place/people names that merely
contain a bad substring (Gandhi, Bastar, Mandi...) are never flagged.

Add words to _EN / _HINGLISH / _DEVANAGARI as you see them in your logs.
"""

import re
import unicodedata

from rapidfuzz import fuzz, process

import config

_EN = {
    "fuck", "fucker", "fucking", "fck", "fuk", "fack", "fuq", "phuck", "motherfucker", "shit", "bullshit",
    "bitch", "bastard", "asshole", "dick", "dickhead", "cunt", "slut", "whore",
    "stfu", "idiot", "stupid", "moron", "dumbass", "retard", "scum", "loser",
}
_HINGLISH = {
    "madarchod", "maderchod", "behenchod", "bhenchod", "bhosdike", "bhosdi",
    "chutiya", "chutiye", "chutia", "gandu", "gaandu", "gaand", "randi",
    "harami", "haramkhor", "kamina", "kameena", "kutta", "kutte", "kutti",
    "lund", "lauda", "lavda", "bsdk", "mkc", "mc", "bc", "bewakoof", "bewakuf",
}
_DEVANAGARI = {
    "मादरचोद", "बहनचोद", "भेनचोद", "चूतिया", "चुतिया", "चूतिये", "गांडू", "गान्डू",
    "रंडी", "हरामी", "हरामखोर", "कमीना", "कमीने", "कुत्ता", "कुत्ते", "भोसड़ी",
    "भोसड़ीके", "लौड़ा", "लंड", "बेवकूफ", "मूर्ख",
}
_PHRASES = ("shut up", "chup kar", "चुप कर", "चुप रह")

# real words that are close to a bad word - never flag these
_WHITELIST = {"bastar", "haram", "gandhi", "chandi", "mandi", "sandhya", "kamini", "kuttiyadi"}

_LEET = str.maketrans({"@": "a", "$": "s", "0": "o", "1": "i", "!": "i", "3": "e", "5": "s", "*": "u"})
_DEVA_DROP = dict.fromkeys(map(ord, "\u0901\u0902\u093c"))  # chandrabindu, anusvara, nukta


def _norm_deva(w: str) -> str:
    return unicodedata.normalize("NFC", w).translate(_DEVA_DROP)


_LATIN_BAD = {w for w in _EN | _HINGLISH}
_DEVA_BAD = {_norm_deva(w) for w in _DEVANAGARI}
_LATIN_LONG = [w for w in _LATIN_BAD if len(w) >= 5]
_DEVA_LONG = [w for w in _DEVA_BAD if len(w) >= 5]


def _latin_candidates(tok: str) -> set[str]:
    t = re.sub(r"[^a-z]", "", tok.lower().translate(_LEET))
    if not t:
        return set()
    return {re.sub(r"(.)\1{2,}", r"\1\1", t), re.sub(r"(.)\1+", r"\1", t), t}


def _is_bad_latin(cands: set[str]) -> bool:
    for c in cands:
        if c in _WHITELIST or c in getattr(config, "DOMAIN_TERMS", ()):
            return False
    for c in cands:
        if c in _LATIN_BAD:
            return True
        if len(c) >= 5:
            m = process.extractOne(c, _LATIN_LONG, scorer=fuzz.ratio)
            if m and m[1] >= 88:
                return True
    return False


def is_abusive(text: str) -> bool:
    if not text:
        return False
    low = unicodedata.normalize("NFC", text.lower())
    if any(p in low for p in _PHRASES):
        return True

    raw_tokens = low.split()

    # spaced-out letters: "f u c k" -> "fuck"
    joined, run = [], []
    for t in raw_tokens + [""]:
        if len(re.sub(r"[^\w]", "", t)) == 1:
            run.append(re.sub(r"[^\w]", "", t))
        else:
            if len(run) >= 3:
                joined.append("".join(run))
            run = []
    for j in joined:
        if _is_bad_latin(_latin_candidates(j)):
            return True

    for tok in raw_tokens:
        # Devanagari words
        for w in re.findall(r"[\u0900-\u097F]+", tok):
            w = _norm_deva(w)
            if w in _DEVA_BAD:
                return True
            if len(w) >= 5:
                m = process.extractOne(w, _DEVA_LONG, scorer=fuzz.ratio)
                if m and m[1] >= 88:
                    return True
        # Latin words (incl. symbol/leet tricks)
        if re.search(r"[a-z@$!*013]", tok) and _is_bad_latin(_latin_candidates(tok)):
            return True
    return False


_REPLY = {
    "en": "Let's keep our conversation respectful. Please use appropriate language, and I'll be glad to help you with anything about UPICON.",
    "hi": "कृपया सम्मानजनक भाषा का प्रयोग करें। UPICON से जुड़ी किसी भी जानकारी में मुझे आपकी मदद करके खुशी होगी।",
}


def reply(lang: str = "en") -> str:
    return _REPLY["hi" if str(lang).startswith("hi") else "en"]