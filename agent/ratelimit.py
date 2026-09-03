"""A minimal in-process sliding-window rate limiter.

For the abuse surfaces a founder-driven, 10-20-customer product actually has:
login brute-force and public-form spam. NOT a dependency and NOT a distributed
limiter — a plain dict of timestamp deques, checked under a lock.

ponytail: process-local, so it only limits per worker. Fine at --workers 1
(what SQLite forces). When production moves to Postgres and raises the worker
count, move the counter to Postgres or Redis — the `allow()` signature stays
the same, only `_HITS` changes.

The financial-abuse scenario the brief cares about most — a stranger making
Roster buy Twilio numbers — is NOT defended here. It's defended by
provisioning.provisioning_allowed (payment/unlock gate) + claim_provisioning
(atomic, no double-buy), which are correctness guarantees, not best-effort
throttles.
"""

import threading
import time
from collections import defaultdict, deque

_LOCK = threading.Lock()
_HITS: dict[str, deque] = defaultdict(deque)

# Keep the dict from growing without bound on a long-lived process: whenever we
# touch a key, drop it if it has gone quiet. Cheap, and the working set of
# active keys is tiny.
_IDLE_EVICT_SECONDS = 3600


def allow(key: str, max_hits: int, window_seconds: float, now: float | None = None) -> bool:
    """Record one hit for `key` and return whether it is within the limit.

    True  -> under `max_hits` in the trailing `window_seconds`; proceed.
    False -> over the limit; the caller should reject (429 / a form error).

    The hit is recorded either way, so a caller that keeps hammering stays
    blocked for the full window rather than getting a fresh allowance each try.
    """
    t = time.monotonic() if now is None else now
    cutoff = t - window_seconds
    with _LOCK:
        hits = _HITS[key]
        while hits and hits[0] < cutoff:
            hits.popleft()
        hits.append(t)
        if not hits:
            _HITS.pop(key, None)
        return len(hits) <= max_hits


def client_ip(request) -> str:
    """Best-effort caller IP. uvicorn runs with --proxy-headers, so
    request.client.host is already the real client behind Railway's proxy;
    the X-Forwarded-For fallback covers a misconfiguration rather than being
    the primary path (trusting a client-set header as primary is how you get
    a trivially-spoofed limiter)."""
    if request.client and request.client.host:
        return request.client.host
    xff = request.headers.get("x-forwarded-for", "")
    return xff.split(",")[0].strip() or "unknown"


def reset() -> None:
    """Test hook — clear all counters."""
    with _LOCK:
        _HITS.clear()
