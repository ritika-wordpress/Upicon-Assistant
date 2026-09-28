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

GREETINGS = [
    "hi", "hii", "hiii", "hello", "hey", "yo", "hola",
    "namaste", "namaskar", "namaskaar", "pranam",
    "good morning", "good afternoon", "good evening", "good night",
    "suprabhat",
]

THANKS = [
    "thanks", "thank you", "thankyou", "thanx", "thnx", "ty",
    "dhanyawad", "dhanyavad", "shukriya", "bahut dhanyawad", "thanks a lot",
    "great thanks", "appreciate it",
]

GOODBYE_STOP = [
    "bye", "goodbye", "bye bye", "see you", "stop", "exit", "quit",
    "band karo", "alvida", "phir milenge", "chalta hoon", "chalti hoon",
    "ruko", "bas", "cancel",
]

NOTHING_NEGATIVE = [
    "no", "nope", "nah", "no thanks", "nothing", "not now", "never mind",
    "nahi", "nhi", "kuch nahi", "koi nahi", "bas kuch nahi",
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
    text = re.sub(r"[^\w\s]", "", text)
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

    for label, phrases in _ALL_GROUPS.items():
        # exact / substring match first (cheap, precise)
        if norm in phrases:
            return label
        for phrase in phrases:
            if phrase in norm and word_count <= 5:
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
