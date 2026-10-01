"""
Central configuration for the UPICON chatbot backend.
All secrets are read from environment variables (.env) - never hardcode
real keys in source. A .env.example is provided as a template.
"""

import os
from dotenv import load_dotenv

load_dotenv(override=True)

# ---------------------------------------------------------------------------
# Groq (LLM + Whisper speech-to-text)
# ---------------------------------------------------------------------------
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY is not set.")
GROQ_CHAT_MODEL = os.getenv("GROQ_CHAT_MODEL")
GROQ_WHISPER_MODEL = os.getenv("GROQ_WHISPER_MODEL")
# Optional smaller/faster model used only to clean up messy user messages
# (see query_rewrite.py). Leave unset to reuse GROQ_CHAT_MODEL. Must be a
# model your Groq account can access.
GROQ_QUERY_MODEL = os.getenv("GROQ_QUERY_MODEL") or None

# ---------------------------------------------------------------------------
# UPICON site — static crawl target + dynamic API
# ---------------------------------------------------------------------------
SITE_ROOT = "https://upicon.in/"
SITEMAP_URL = "https://upicon.in/sitemap.xml"

# Pages that matter but aren't reliably in sitemap.xml (new sections, pages
# added after the sitemap was last generated, client-side-only nav links,
# etc.). Always folded into the crawl and into the live-fetch candidate pool
# regardless of sitemap coverage or keyword matching.
EXTRA_STATIC_URLS = [
    "https://upicon.in/odop",
    "https://upicon.in/youth-adda",
    "https://upicon.in/cmyuva",
    "https://upicon.in/karigar",
    "https://upicon.in/business-units",
    "https://upicon.in/bihar-elderine",
    # Youth Adda's actual content lives on its own domain, not upicon.in -
    # discover_links()/_scrape_links() only follow links within SITE_ROOT,
    # so this would never get picked up automatically. Listed explicitly
    # here instead: crawl_all_pages() has no domain restriction, it just
    # crawls whatever URLs it's given.
    "https://cmyouthadda.in/",
]

UPICON_LISTING_URL = "https://upicon.in/apis/root/listing.php"
UPICON_OPERATION_URL = "https://upicon.in/apis/root/operation.php"
UPICON_TOKEN = os.getenv("UPICON_TOKEN", "")
if not UPICON_TOKEN:
    raise RuntimeError("UPICON_TOKEN is not set. Add it to your .env file.")

# ---------------------------------------------------------------------------
# Embeddings / vector store (semantic search over crawled static content)
# multilingual model -> lets the same index serve Hindi + English queries,
# and gives us tolerance to typos/misspellings because it matches on meaning
# rather than exact tokens.
# ---------------------------------------------------------------------------
EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2"
)
CHUNK_SIZE = 700          # characters per chunk
CHUNK_OVERLAP = 120
TOP_K = 5                 # chunks returned per static-content search
MIN_SIMILARITY = 0.30     # below this, we treat the query as "not on site"

# ---------------------------------------------------------------------------
# Context budget — dynamic API fetches (news/schemes/openings/...) have no
# built-in page size limit, so a broad question can otherwise pull in dozens
# of items and blow well past the LLM provider's per-request token limit
# (Groq's free tier is especially tight - 8,000 TPM). These caps are applied
# to the final merged context list right before it's handed to the LLM.
MAX_CONTEXT_SNIPPETS = 12     # keep at most this many snippets, most-relevant first
MAX_SNIPPET_CHARS = 800       # trim any single overly-long snippet
MAX_CONTEXT_CHARS = 6000      # hard cap on the total context block size

# ---------------------------------------------------------------------------
# Storage paths
# ---------------------------------------------------------------------------
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
STATIC_JSON_PATH = os.path.join(DATA_DIR, "static_pages.json")
FAISS_INDEX_PATH = os.path.join(DATA_DIR, "faiss.index")
FAISS_META_PATH = os.path.join(DATA_DIR, "faiss_meta.pkl")

# ---------------------------------------------------------------------------
# Live-site fallback fetch
# Last resort when a question matches nothing in the crawled static index
# and nothing in the dynamic API: fetch upicon.in directly (a couple of
# likely-relevant pages, not a full re-crawl) so the visitor still gets a
# real answer instead of "I don't have that information."
# ---------------------------------------------------------------------------
LIVE_FETCH_ENABLED = os.getenv("LIVE_FETCH_ENABLED", "true").lower() != "false"
LIVE_SITEMAP_CACHE_SECONDS = 6 * 3600   # how long the url list itself is cached
LIVE_PAGE_CACHE_SECONDS = 3600          # how long one crawled page is cached
LIVE_MAX_CANDIDATE_PAGES = 3            # pages fetched live per unmatched question
LIVE_MIN_SIMILARITY = 0.22              # looser than MIN_SIMILARITY — a live
                                         # fetch only covers 1-3 pages, not the
                                         # whole site, so scores run lower

# ---------------------------------------------------------------------------
# Shared cache (SQLite) — one on-disk file used by every worker/process, so
# the cache is genuinely shared without running (or paying for) a separate
# cache service. Lives next to the other generated data files.
# ---------------------------------------------------------------------------
CACHE_DB_PATH = os.getenv("CACHE_DB_PATH", os.path.join(DATA_DIR, "cache.db"))

# How long dynamic UPICON API results (jobs/schemes/news/initiatives) are
# reused before we hit the live API again for the same query.
CACHE_TTL_DYNAMIC_SECONDS = int(os.getenv("CACHE_TTL_DYNAMIC_SECONDS", 3600))
# How long an LLM answer is reused when the exact same question comes in
# again with the exact same underlying context (nothing changed). Bumped up
# since the site content is mostly static - the cache key already changes
# automatically whenever the underlying context text changes, so a long TTL
# here just means fewer repeat Groq calls for genuinely unchanged content.
CACHE_TTL_CHAT_SECONDS = int(os.getenv("CACHE_TTL_CHAT_SECONDS", 86400))
# Hard cap on reply length, enforced both in the prompt and as a fallback trim.
MAX_REPLY_WORDS = int(os.getenv("MAX_REPLY_WORDS", 150))

# ---------------------------------------------------------------------------
# CORS - restrict in production to your real frontend origin(s)
# ---------------------------------------------------------------------------
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")

# ---------------------------------------------------------------------------
# Page aliases - alternate names a visitor might use for a page, keyed by the
# last part of its URL (e.g. /cmyuva -> "cmyuva"). If the question contains
# any alias, that page's own content is put first in the context. Add more
# as you notice pages the bot fails to find.
# ---------------------------------------------------------------------------
PAGE_ALIASES = {
    "cmyuva": ["cm yuva", "cmyuva", "yuva udyami", "yuva abhiyan", "cm yuva abhiyan",
               "सीएम युवा", "मुख्यमंत्री युवा", "युवा उद्यमी", "युवा अभियान"],
    "youth-adda": ["youth adda", "youthadda", "yuva adda", "युवा अड्डा", "यूथ अड्डा"],
    "odop": ["odop", "one district one product", "ओडीओपी", "एक जनपद एक उत्पाद"],
    "business-units": ["business unit", "business units"],
    "karigar": ["karigar", "कारीगर"],
    # slug on the site is "bihar-elderine"; visitors spell it many ways
    "bihar-elderine": ["bihar elderline", "bihar elderine", "bihar elder line", "bihar elderly line",
                       "elderline", "elderine", "elder line", "elderly line", "14567",
                       "बिहार एल्डरलाइन", "बिहार एल्डरिन", "बिहार एल्डर लाइन", "एल्डरलाइन", "एल्डरिन"],
}

# ---------------------------------------------------------------------------
# Domain terms - UPICON-specific words the spellchecker must never "correct"
# into an unrelated word, and that mistyped versions get pulled TOWARD
# (e.g. "odo"/"odp" -> "odop", not "odor"). Add new scheme/program names here.
# ---------------------------------------------------------------------------
DOMAIN_TERMS = [
    "upicon", "odop", "cmyuva", "yuva", "udyami", "abhiyan", "msme", "karigar",
    "adda", "youth", "scheme", "yojana", "initiative", "initiatives", "career",
    "careers", "subsidy", "district", "product", "business", "units",
    "elderline", "elderine",
]

# Common Hinglish words the spellchecker must leave alone (it only knows the
# site's English vocabulary and would otherwise bend these into English words).
PROTECTED_WORDS = {
    "kya", "hai", "hain", "kaise", "kese", "kaisa", "kab", "kb", "kahan", "kaha", "kha",
    "kaun", "kon", "kitna", "kitne", "kitni", "batao", "btao", "bataiye", "bataye",
    "samjhao", "milega", "milegi", "chahiye", "chahie", "mujhe", "mera", "meri",
    "hamare", "apna", "uska", "iska", "unka", "nokri", "naukri", "bharti", "yojna",
    "sarkar", "sarkari", "dukan", "kaam", "karna", "karni", "karo", "kare", "lena",
    "dena", "wala", "wali", "nahi", "nhi", "aur", "ya", "par", "pta", "pata",
}