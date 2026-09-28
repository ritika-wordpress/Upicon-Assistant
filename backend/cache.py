"""
Shared server-side cache, backed by a SQLite file on disk.

Why SQLite instead of Redis: this needs to be a genuinely *shared* cache -
every uvicorn worker/process should see the same cached data - but without
running (or paying for) a separate cache service. SQLite ships with Python
(no extra service, no extra cost) and the database is just a file: every
worker on the same machine reads and writes the same file, so a value
cached by one worker is immediately visible to all the others. WAL mode
lets multiple processes read/write it concurrently without locking each
other out.

  - api_client.py's dynamic UPICON listings (jobs/schemes/news/...) are
    fetched from the live API once, then reused by *every* worker handling
    *every* user, until the TTL expires.
  - llm.py's chat-answer cache is likewise shared: the first worker to
    answer a given question caches it, and every other worker (and every
    other user asking the same thing) gets the cached reply.

Scope note: this shares the cache across every process on one machine/one
disk. If you ever deploy across multiple *separate* machines that don't
share a filesystem, point CACHE_DB_PATH at a shared/mounted volume, or
swap this module for a networked cache service - but for a normal
single-server (even multi-worker) deployment, this file *is* the shared
cache, for free.
"""

import functools
import hashlib
import json
import sqlite3
import threading
import time

import config

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn
    with _lock:
        if _conn is not None:
            return _conn
        conn = sqlite3.connect(config.CACHE_DB_PATH, check_same_thread=False, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY,
                namespace TEXT NOT NULL DEFAULT '',
                value TEXT NOT NULL,
                expires_at REAL NOT NULL
            )"""
        )
        # Migrate a cache.db created by the older schema (no namespace
        # column): CREATE TABLE IF NOT EXISTS above leaves it untouched.
        cols = [row[1] for row in conn.execute("PRAGMA table_info(cache)")]
        if "namespace" not in cols:
            conn.execute("ALTER TABLE cache ADD COLUMN namespace TEXT NOT NULL DEFAULT ''")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_namespace ON cache(namespace)")
        conn.commit()
        _conn = conn
        return _conn


def build_key(*parts: str) -> str:
    raw = "\x1f".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()


def get(key: str):
    conn = _get_conn()
    with _lock:
        try:
            row = conn.execute(
                "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
            ).fetchone()
        except sqlite3.Error as exc:  # noqa: BLE001 - degrade, don't break chat
            print(f"[cache] SQLite read failed ({exc}).", flush=True)
            return None

    if row is None:
        return None

    value, expires_at = row
    if time.time() > expires_at:
        _delete(key)
        return None
    return json.loads(value)


def set(key: str, value, ttl_seconds: int, namespace: str = "") -> None:
    conn = _get_conn()
    expires_at = time.time() + ttl_seconds
    payload = json.dumps(value)
    with _lock:
        try:
            conn.execute(
                "INSERT INTO cache (key, namespace, value, expires_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "expires_at = excluded.expires_at, namespace = excluded.namespace",
                (key, namespace, payload, expires_at),
            )
            conn.commit()
        except sqlite3.Error as exc:  # noqa: BLE001
            print(f"[cache] SQLite write failed ({exc}).", flush=True)


def _delete(key: str) -> None:
    conn = _get_conn()
    with _lock:
        try:
            conn.execute("DELETE FROM cache WHERE key = ?", (key,))
            conn.commit()
        except sqlite3.Error:
            pass


def clear(namespace: str | None = None) -> None:
    """Wipe cached entries. With no namespace, wipes everything (rarely what
    you want). Pass a namespace (e.g. "dynamic") to only drop that slice -
    chat-answer entries are keyed off the actual context text, so unchanged
    pages keep serving cached answers after a reindex; only entries whose
    underlying content changed become naturally unreachable (and expire via
    TTL on their own). No need to nuke them and re-pay Groq for questions
    nothing changed on."""
    conn = _get_conn()
    with _lock:
        try:
            if namespace is None:
                conn.execute("DELETE FROM cache")
            else:
                conn.execute("DELETE FROM cache WHERE namespace = ?", (namespace,))
            conn.commit()
        except sqlite3.Error as exc:  # noqa: BLE001
            print(f"[cache] SQLite clear failed ({exc}).", flush=True)


def cached_async(ttl_seconds: int, prefix: str | None = None, namespace: str = "dynamic"):
    """Decorator for async functions whose return value is JSON-serializable
    and safe to share across every worker/user for a while (e.g. the UPICON
    listing endpoints). SQLite I/O is offloaded to a thread so it never
    blocks the event loop. Tagged under `namespace` ("dynamic" by default)
    so /admin/reindex can clear just this slice instead of the whole cache."""
    import asyncio

    def decorator(fn):
        name = prefix or fn.__name__

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            key = build_key(name, args, tuple(sorted(kwargs.items())))
            cached = await asyncio.to_thread(get, key)
            if cached is not None:
                return cached
            result = await fn(*args, **kwargs)
            await asyncio.to_thread(set, key, result, ttl_seconds, namespace)
            return result

        return wrapper

    return decorator