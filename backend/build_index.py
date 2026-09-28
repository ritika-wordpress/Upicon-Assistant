"""Run after crawler.py to embed the crawled chunks into a FAISS index.

    python crawler.py       # step 1: crawl sitemap -> data/static_pages.json
    python build_index.py   # step 2: embed chunks -> data/faiss.index
"""

from vector_store import build_index

if __name__ == "__main__":
    build_index()
