"""In-process background scheduler (2026-07-30). Runs a blocking callable
(recovery_tick.run, in production) on an interval inside the same running
app process — not a separate Railway service — so it shares the same
SQLite file on the app's own volume rather than needing a second service
with no access to the database.

Pure loop logic only: no DB/Twilio/Anthropic imports here. Those happen
inside whatever `tick` callable the caller passes in.
"""

import asyncio
import sys
import traceback
from typing import Callable

from starlette.concurrency import run_in_threadpool

DEFAULT_INTERVAL_SECONDS = 3600  # hourly — nothing scheduled here needs to wait longer


async def run_scheduler(
    tick: Callable[[], None],
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    sleep=asyncio.sleep,
) -> None:
    """Runs `tick()` in a thread (so a blocking DB/HTTP call never freezes
    the event loop) every `interval_seconds`, forever. Fires immediately on
    startup rather than waiting a full interval first — a just-deployed or
    just-restarted app may already have work queued up. A crashing tick is
    logged and never kills the loop; the next interval still fires."""
    while True:
        try:
            await run_in_threadpool(tick)
        except Exception:
            print("[scheduler] tick failed:", file=sys.stderr)
            traceback.print_exc()
        await sleep(interval_seconds)
