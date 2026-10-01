"""
Fast, cheap intent detection for chit-chat turns (greetings, thanks,
goodbye/stop, "no"/"nothing") so we don't burn an LLM call — and don't run
semantic/API search — on messages that aren't actually questions about the
website.

Uses rapidfuzz for fuzzy matching so "helo", "thnx", "namste" etc. still
match, in the same spirit as the semantic search's typo tolerance.
"""

import re

from rapidfuzz import fuzz

import config

GREETINGS = [
    "hi", "hii", "hiii", "hello", "hey", "yo", "hola",
    "namaste", "namaskar", "namaskaar", "pranam",
    "good morning", "good afternoon", "good evening", "good night",
    "suprabhat",
    # Devanagari
    "नमस्ते", "नमस्कार", "प्रणाम", "हैलो", "हेलो", "हाय", "हलो", "सुप्रभात",
]

THANKS = [
    "thanks", "thank you", "thankyou", "thanx", "thnx", "ty",
    "dhanyawad", "dhanyavad", "shukriya", "bahut dhanyawad", "thanks a lot",
    "great thanks", "appreciate it",
    # Devanagari
    "धन्यवाद", "बहुत धन्यवाद", "शुक्रिया", "थैंक्स", "थैंक यू", "थैंक्यू",
]

GOODBYE_STOP = [
    "bye", "goodbye", "bye bye", "see you", "stop", "exit", "quit",
    "band karo", "alvida", "phir milenge", "chalta hoon", "chalti hoon",
    "ruko", "bas", "cancel",
    # Devanagari
    "अलविदा", "बाय", "फिर मिलेंगे", "रुकिए", "बस",
]

NOTHING_NEGATIVE = [
    "no", "nope", "nah", "no thanks", "nothing", "not now", "never mind",
    "nahi", "nhi", "kuch nahi", "koi nahi", "bas kuch nahi",
    # Devanagari
    "नहीं", "नही", "कुछ नहीं", "कुछ नही", "कोई नहीं",
]

_ALL_GROUPS = {
    "greeting": GREETINGS,
    "thanks": THANKS,
    "goodbye": GOODBYE_STOP,
    "nothing": NOTHING_NEGATIVE,
}

_FUZZY_THRESHOLD = 82  # 0-100, rapidfuzz partial-ratio score


def _normalize(text: str) -> str:
    text = text.lower().strip()
    # keep Devanagari (incl. vowel signs/virama, which are not \w) intact
    text = re.sub(r"[^\w\s\u0900-\u097F]", "", text)
    return text


def detect(text: str) -> str | None:
    """Return one of 'greeting' | 'thanks' | 'goodbye' | 'nothing' | None.

    None means: this looks like a real content question, run it through
    retrieval + the LLM as normal.
    """
    norm = _normalize(text)
    if not norm:
        return "nothing"

    word_count = len(norm.split())

    # A message that names a UPICON topic (odop, yojana, cmyuva...) is a real
    # question, never chit-chat - even if it is short.
    tokens = set(norm.split())
    if tokens & set(getattr(config, "DOMAIN_TERMS", [])):
        return None

    for label, phrases in _ALL_GROUPS.items():
        # exact / whole-word match first (cheap, precise). Whole-word matters:
        # plain substring matching made "yojana" match "yo" (greeting) and
        # "this"/"which" match "hi".
        if norm in phrases:
            return label
        for phrase in phrases:
            if word_count <= 5 and re.search(rf"(?<![\w\u0900-\u097F]){re.escape(phrase)}(?![\w\u0900-\u097F])", norm):
                return label

    # only fuzzy-match very short utterances — a long sentence that merely
    # *contains* fuzzy-similar words to "hi" should NOT be treated as chit-chat
    if word_count <= 3:
        for label, phrases in _ALL_GROUPS.items():
            for phrase in phrases:
                if fuzz.ratio(norm, phrase) >= _FUZZY_THRESHOLD:
                    return label

    return None


CANNED_REPLIES = {
    "greeting": {
        "en": "Hi there! 👋 I'm the UPICON assistant. Ask me about our initiatives, schemes, news, careers, or anything else on the site.",
        "hi": "नमस्ते! 👋 मैं UPICON असिस्टेंट हूँ। आप हमारी योजनाओं, पहलों, समाचार, करियर या वेबसाइट से जुड़ी किसी भी जानकारी के बारे में पूछ सकते हैं।",
    },
    "thanks": {
        "en": "You're welcome! Let me know if there's anything else you'd like to know about UPICON.",
        "hi": "आपका स्वागत है! अगर UPICON के बारे में कुछ और जानना हो तो बेझिझक पूछें।",
    },
    "goodbye": {
        "en": "Alright, take care! Come back anytime you have questions about UPICON.",
        "hi": "ठीक है, ध्यान रखिए! जब भी UPICON से जुड़ा कोई सवाल हो, वापस आइए।",
    },
    "nothing": {
        "en": "No problem — I'm here whenever you'd like to ask something about UPICON.",
        "hi": "कोई बात नहीं — जब भी UPICON के बारे में कुछ पूछना हो, मैं यहाँ हूँ।",
    },
}


def canned_reply(intent_label: str, lang: str = "en") -> str:
    lang = "hi" if lang.startswith("hi") else "en"
    return CANNED_REPLIES.get(intent_label, CANNED_REPLIES["nothing"])[lang]