"""
Standalone diagnostic - NOT part of the app.

Isolates exactly where a failing question breaks down:
  1. Raw semantic search over the static index (vector_store.search)
  2. Full context assembly, dynamic + static + live-fetch (router.gather_context)
  3. The final LLM answer given that context (llm.generate_answer)

Run from the backend/ directory (same venv as the rest of the app):

    python debug_query.py "what is odop product of aug"
"""

import asyncio
import sys

import config
import llm
import router
import vector_store


async def main(query: str):
    print(f"QUERY: {query!r}\n")

    print("=" * 70)
    print("STEP 1: raw vector_store.search() (static index only)")
    print("=" * 70)
    if not vector_store.is_index_ready():
        print("  !! FAISS index not found on disk - run build_index.py !!")
    else:
        hits = vector_store.search(query, top_k=5)
        if not hits:
            print(f"  0 hits above MIN_SIMILARITY={config.MIN_SIMILARITY}")
            # show what the raw top-5 scores actually are, ignoring the
            # threshold, so we can tell "no match at all" from "matched but
            # score fell just under the cutoff"
            vector_store._load_index()
            q_vec = vector_store._embed([query])
            scores, idxs = vector_store._index.search(q_vec, 5)
            print("  Raw top-5 scores (threshold ignored):")
            for score, idx in zip(scores[0], idxs[0]):
                if idx == -1:
                    continue
                rec = vector_store._meta[idx]
                print(f"    score={score:.3f}  url={rec.get('url')}  text={rec['text'][:80]!r}")
        else:
            for h in hits:
                print(f"  score={h['score']:.3f}  text={h['text'][:100]!r}")

    print("\n" + "=" * 70)
    print("STEP 2: router.gather_context() (dynamic + static + live-fetch)")
    print("=" * 70)
    context = await router.gather_context(query)
    print(f"  {len(context)} context snippet(s) assembled:")
    for i, c in enumerate(context):
        print(f"  [{i}] {c[:120]!r}")

    print("\n" + "=" * 70)
    print("STEP 3: llm.generate_answer() with that context")
    print("=" * 70)
    if not context:
        print("  (skipped - no context to give the LLM, this alone explains a sorry reply)")
    else:
        answer = llm.generate_answer(query, context, history=[], lang="en")
        print(f"  ANSWER: {answer}")


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "what is odop product of aug"
    asyncio.run(main(q))