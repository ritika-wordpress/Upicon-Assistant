"""
Standalone diagnostic - NOT part of the app, just a one-off check.

check_sitemap_coverage.py only discovers live nav links off the HOMEPAGE,
which misses archive/gallery links that only live on inner pages (e.g. a
"past ODOP spotlights" list on /odop itself, which is how odop_jhansi and
odop_lalitpur exist as separate pages in the first place).

This script does the same discover_links() pass, but targeted specifically
at /odop, and reports any odop_* (or otherwise ODOP-looking) URLs that
aren't already in sitemap.xml or config.EXTRA_STATIC_URLS.

Run from the backend/ directory (same venv as the rest of the app):

    python check_odop_archive.py
"""

import asyncio

import config
import crawler

ODOP_PAGE = "https://upicon.in/odop"


async def main():
    print("Fetching sitemap.xml ...")
    sitemap_urls = set(await crawler.fetch_sitemap_urls())
    print(f"  {len(sitemap_urls)} URL(s) in sitemap.xml\n")

    print(f"Discovering live links (JS-rendered) from {ODOP_PAGE} ...")
    live_links = set(await crawler.discover_links(ODOP_PAGE))
    print(f"  {len(live_links)} internal link(s) found on the page\n")

    known = sitemap_urls | set(config.EXTRA_STATIC_URLS)
    missing = sorted(
        u for u in live_links
        if u not in known and ("odop" in u.lower())
    )

    if missing:
        print(f"{len(missing)} ODOP-related page(s) found on /odop but not yet crawled:\n")
        for u in missing:
            print(f"  [missing] {u}")
        print("\nAdd these to config.EXTRA_STATIC_URLS, e.g.:\n")
        print("EXTRA_STATIC_URLS = [")
        for u in config.EXTRA_STATIC_URLS:
            print(f'    "{u}",')
        for u in missing:
            print(f'    "{u}",  # discovered via check_odop_archive.py')
        print("]")
    else:
        print("No extra ODOP archive links found beyond what's already crawled.")
        print("(Note: if the archive is a JS dropdown/paginated list rather than")
        print(" plain <a href> links, discover_links() won't see it — you'd need")
        print(" to inspect the page's DOM/network requests in a browser instead.)")


if __name__ == "__main__":
    asyncio.run(main())