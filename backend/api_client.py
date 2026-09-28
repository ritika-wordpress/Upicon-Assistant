"""
Thin async wrapper around the UPICON dynamic content API
(apis/root/listing.php and apis/root/operation.php).

Every "listing" call needs {"action": ..., "token": ...}; this module
centralizes that so callers just do e.g. `await get_openings()`.
"""

import httpx

import cache
import config

_TIMEOUT = 20


async def _post_listing(action: str, extra: dict | None = None) -> dict | list:
    payload = {"action": action, "token": config.UPICON_TOKEN}
    if extra:
        payload.update(extra)
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(config.UPICON_LISTING_URL, json=payload)
        resp.raise_for_status()
        return resp.json()


async def _post_operation(payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(config.UPICON_OPERATION_URL, json=payload)
        resp.raise_for_status()
        return resp.json()


# --- Listing endpoints ---------------------------------------------------
# Read-only and slow-changing (jobs/schemes/news don't change minute to
# minute), so these are cached for CACHE_TTL_DYNAMIC_SECONDS. This avoids
# hitting the live UPICON API again every time a chat message mentions the
# same topic. Cache is cleared on /admin/reindex.

@cache.cached_async(config.CACHE_TTL_DYNAMIC_SECONDS)
async def get_openings():
    return await _post_listing("openings")


@cache.cached_async(config.CACHE_TTL_DYNAMIC_SECONDS)
async def get_initiatives():
    return await _post_listing("initiatives")


@cache.cached_async(config.CACHE_TTL_DYNAMIC_SECONDS)
async def get_home_initiatives():
    return await _post_listing("homeInitiatives")


@cache.cached_async(config.CACHE_TTL_DYNAMIC_SECONDS)
async def get_home_contents():
    return await _post_listing("homeContents")


@cache.cached_async(config.CACHE_TTL_DYNAMIC_SECONDS)
async def get_contents(category: str = "All", offset: int = 0):
    return await _post_listing("contents", {"category": category, "offset": offset})


@cache.cached_async(config.CACHE_TTL_DYNAMIC_SECONDS)
async def get_content_categories():
    return await _post_listing("contentCategory")


@cache.cached_async(config.CACHE_TTL_DYNAMIC_SECONDS)
async def get_schemes():
    return await _post_listing("schemes")


@cache.cached_async(config.CACHE_TTL_DYNAMIC_SECONDS)
async def get_news():
    return await _post_listing("news")


# --- Operation endpoints -------------------------------------------------

async def apply_job(
    opening_id: str,
    name: str,
    mobile: str,
    email: str,
    experience: str,
    about: str,
    resume_base64: str,
    resume_name: str,
):
    payload = {
        "action": "applyJob",
        "opening_id": opening_id,
        "name": name,
        "mobile": mobile,
        "email": email,
        "experience": experience,
        "about": about,
        "resume": resume_base64,
        "resume_name": resume_name,
    }
    return await _post_operation(payload)


async def add_contact(name: str, email: str, mobile: str, message: str):
    payload = {
        "action": "addContact",
        "name": name,
        "email": email,
        "mobile": mobile,
        "message": message,
        "token": config.UPICON_TOKEN,
    }
    return await _post_operation(payload)


def image_url(kind: str, filename: str) -> str:
    """kind: 'contents' | 'initiatives' | 'openings'"""
    return f"https://upicon.in/manage/app-assets/images/{kind}/{filename}"
