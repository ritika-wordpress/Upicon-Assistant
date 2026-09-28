"""
Standalone diagnostic - NOT part of the app, just a one-off check.

Prints every URL in sitemap.xml, then crawls the homepage with JS rendering
to discover real nav links, and shows any link found on the live site that
ISN'T in the sitemap. Those are the pages worth adding to
config.EXTRA_STATIC_URLS.

Run from the backend/ directory (needs the same venv as the rest of the app,
since it reuses crawler.py):

    python check_sitemap_coverage.py
"""

import asyncio

import config
import crawler


async def main():
    print("Fetching sitemap.xml ...")
    sitemap_urls = set(await crawler.fetch_sitemap_urls())
    print(f"  {len(sitemap_urls)} URL(s) in sitemap.xml\n")
    for u in sorted(sitemap_urls):
        print(f"  [sitemap] {u}")

    print("\nDiscovering live nav links (JS-rendered) from the homepage ...")
    live_links = set(await crawler.discover_links(config.SITE_ROOT))
    print(f"  {len(live_links)} internal link(s) found on the live homepage\n")

    missing = sorted(live_links - sitemap_urls)
    if missing:
        print(f"\n{len(missing)} page(s) found on the live site but NOT in sitemap.xml:\n")
        for u in missing:
            print(f"  [missing] {u}")
        print("\nSuggested config.py addition:\n")
        print("EXTRA_STATIC_URLS = [")
        for u in missing:
            print(f'    "{u}",')
        for u in config.EXTRA_STATIC_URLS:
            if u not in missing:
                print(f'    "{u}",  # already added')
        print("]")
    else:
        print("\nNo gaps found - every live nav link is already in sitemap.xml.")


if __name__ == "__main__":
    asyncio.run(main())
