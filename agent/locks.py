"""Per-conversation serialization.

Two simultaneous inbound messages from the same customer must not run their
agent turns concurrently: both would load the same history, interleave their
appends, and can leave the thread violating Anthropic's strict user/assistant
alternation — poisoning every future turn. This lock makes turns for one
(business, customer) pair run one-at-a-time while different conversations
proceed in parallel.

SQLite deployments run in one process (SQLite is dev/test-only — see db.py),
so a process-local threading.Lock suffices. Postgres deployments may run many
workers/hosts, so the lock is a pg_advisory_lock held on a dedicated
connection for the duration of the turn.
"""
import hashlib
import threading
from contextlib import contextmanager

from sqlalchemy import text

import db

_registry_lock = threading.Lock()
_local_locks: dict = {}


def _advisory_key(business_id: int, phone: str) -> int:
    digest = hashlib.sha256(f"{business_id}:{phone}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


@contextmanager
def conversation_lock(business_id: int, phone: str):
    if db.engine.dialect.name == "sqlite":
        with _registry_lock:
            lock = _local_locks.setdefault((business_id, phone), threading.Lock())
        with lock:
            yield
        return

    key = _advisory_key(business_id, phone)
    conn = db.engine.connect()
    try:
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": key})
        yield
    finally:
        try:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
        finally:
            conn.close()
