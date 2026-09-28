"""
Standalone diagnostic - NOT part of the app.

Shows exactly what the bot has for one page, so you can tell WHY it can't
answer from it:
  1. what a fresh JS-rendered crawl of the page returns (markdown length + preview)
  2. which chunks of that URL are actually in the saved index

    python check_page.py https://upicon.in/cmyuva
"""

import asyncio
import json
import sys

import config
import crawler


async def main(url: str):
    print(f"=== 1) Live crawl of {url} ===")
    pages = await crawler.crawl_all_pages([url])
    if not pages:
        print("  !! crawl returned NO content (blocked, timed out, or empty page)")
    else:
        text = pages[0]["text"]
        print(f"  {len(text)} characters of markdown. First 1500 chars:\n")
        print(text[:1500])
        print("\n  -> If this is only menu/footer text, the real content loads late or via API/tabs.")

    print(f"\n=== 2) Chunks saved in {config.STATIC_JSON_PATH} for this URL ===")
    try:
        with open(config.STATIC_JSON_PATH, "r", encoding="utf-8") as f:
            records = json.load(f)
    except FileNotFoundError:
        print("  !! static_pages.json not found - run crawler.py first")
        return
    mine = [r for r in records if r.get("url") == url]
    print(f"  {len(mine)} chunk(s)")
    for r in mine[:8]:
        print(f"   - {r['text'][:110]!r}")
    if not mine:
        print("  !! none - run: python crawler.py && python build_index.py")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "https://upicon.in/cmyuva"))