"""Thread-safe response caching with stale-while-refresh semantics.

The dashboard serves the last successful aggregate response immediately after
an ingest invalidates it, while a bounded worker pool recomputes the value in
the background. A hard TTL still prevents indefinitely old data from being
served when the refresh source remains unavailable.
"""
from __future__ import annotations

import functools
import inspect
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

log = logging.getLogger("ghpulse.cache")


class _TTLCache:
    """Process-local cache with a flat TTL and generation-based staleness.

    Values are whatever the decorated endpoint returns (dicts). Not
    size-bounded — the keyspace is the small set of endpoint, range, and
    repository-filter combinations.

    Ingest used to ``clear()`` this, which dropped every user onto the
    cold path once an hour — and cold means 8s+ for the dashboard. It now
    calls ``invalidate()``, which bumps a generation counter so existing
    entries read as STALE but stay servable. A stale hit is returned
    immediately and refreshed in the background; the TTL remains the hard
    limit past which an entry is dropped and must be recomputed inline.
    """

    def __init__(self, ttl_seconds: int):
        self.ttl_seconds = ttl_seconds
        self._items: dict[str, tuple[Any, float, int]] = {}
        self._generation = 0
        self._guard = threading.Lock()

    def get_entry(self, key: str) -> tuple[Any, bool] | None:
        """Return ``(value, is_stale)``, or None on miss/expiry."""
        with self._guard:
            item = self._items.get(key)
            if item is None:
                return None
            value, ts, generation = item
            if time.time() - ts > self.ttl_seconds:
                self._items.pop(key, None)
                return None
            return value, generation != self._generation

    def get(self, key: str) -> Any | None:
        entry = self.get_entry(key)
        return None if entry is None else entry[0]

    def put(self, key: str, value: Any) -> None:
        with self._guard:
            self._items[key] = (value, time.time(), self._generation)

    def invalidate(self) -> None:
        """Mark every entry stale WITHOUT dropping it."""
        with self._guard:
            self._generation += 1

    def clear(self) -> None:
        with self._guard:
            self._items.clear()
            self._generation += 1


response_cache = _TTLCache(ttl_seconds=3600)


# Background refreshes for stale entries. Small pool on purpose: a refresh
# is a full uncached query, and running many at once would starve the
# connection pool that live requests need.
_refresh_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cache-refresh")
_refreshing: set[str] = set()
_key_locks: dict[str, threading.Lock] = {}
_registry_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _registry_guard:
        return _key_locks.setdefault(key, threading.Lock())


def _schedule_refresh(key: str, fn: Callable[..., dict], kwargs: dict[str, Any]) -> None:
    """Recompute `key` off the request path, at most once concurrently."""
    with _registry_guard:
        if key in _refreshing:
            return
        _refreshing.add(key)

    def _run() -> None:
        try:
            response_cache.put(key, fn(**kwargs))
        except Exception:
            # A failed refresh leaves the stale entry in place, which is
            # the whole point — better stale than a 500 or an 8s wait.
            log.exception("background refresh failed for %s", key)
        finally:
            with _registry_guard:
                _refreshing.discard(key)

    _refresh_pool.submit(_run)


def warm(fn: Callable[..., dict], **overrides: Any) -> None:
    """Populate the cache entry a request with `overrides` would produce.

    A fresh process has no response entries, so a caller may proactively warm
    the expensive dashboard query after ingest. Stale-while-refresh cannot
    help on an empty cache because there is nothing stale to serve yet.

    The key MUST match what the decorated wrapper computes for a real
    request, and FastAPI passes every declared query param as a keyword.
    So start from the endpoint's own defaults and apply the overrides on
    top; guessing the kwargs would warm a key nobody ever reads.
    """
    target = getattr(fn, "__wrapped__", fn)
    kwargs: dict[str, Any] = {}
    for name, param in inspect.signature(target).parameters.items():
        default = param.default
        # Query(...) defaults carry the real value on `.default`.
        kwargs[name] = getattr(default, "default", default)
    kwargs.update(overrides)
    if "fresh" in kwargs:
        # A truthy `fresh` bypasses the cache on both read and write, so
        # warming with it would compute the response and store nothing.
        kwargs["fresh"] = 0

    def _run() -> None:
        try:
            fn(**kwargs)
        except Exception:
            log.exception("cache warm failed for %s %r", fn.__qualname__, overrides)

    _refresh_pool.submit(_run)


def cache_response(fn: Callable[..., dict]) -> Callable[..., dict]:
    """Cache an endpoint's dict result keyed by its keyword args.

    FastAPI calls endpoints with all params as keywords and resolves the
    signature through ``functools.wraps``' ``__wrapped__`` link, so the
    wrapper can keep a ``**kwargs`` signature while FastAPI still parses
    the original query params. A truthy ``fresh`` kwarg bypasses the
    cache (read+write) — only ``/api/dashboard`` declares ``fresh``.

    The decorated endpoints are SYNC (blocking psycopg), so this wrapper
    is sync too and FastAPI runs it in its threadpool. Three behaviours
    matter here:

    - fresh hit  → return it.
    - stale hit  → return it IMMEDIATELY and refresh in the background.
      Serving slightly-old numbers beats blocking a page load for 8s+.
    - miss       → compute inline, single-flighted per key so a burst of
      concurrent cold requests (a dashboard fires ~7 at once) runs the
      query once instead of N times.
    """

    @functools.wraps(fn)
    def wrapper(**kwargs: Any) -> dict:
        if kwargs.get("fresh"):
            return fn(**kwargs)
        key = fn.__qualname__ + ":" + repr(sorted(kwargs.items()))

        entry = response_cache.get_entry(key)
        if entry is not None:
            value, is_stale = entry
            if is_stale:
                _schedule_refresh(key, fn, kwargs)
            return value

        with _lock_for(key):
            # Another thread may have populated it while we queued.
            entry = response_cache.get_entry(key)
            if entry is not None:
                return entry[0]
            result = fn(**kwargs)
            response_cache.put(key, result)
            return result

    return wrapper
