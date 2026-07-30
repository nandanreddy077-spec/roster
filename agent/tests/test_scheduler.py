"""The in-process background scheduler (2026-07-30): runs recovery_tick.run()
on an interval inside the same running app process, so it shares the same
SQLite file on Railway's single-service volume instead of needing a second
service with no access to the database. Pure loop logic — DB/Twilio/
Anthropic calls happen inside the injected `tick` callable, not here.
"""
import asyncio

import pytest

from scheduler import run_scheduler


class _StopLoop(Exception):
    """Raised by a fake sleep to end an otherwise-infinite loop in a test."""


def test_run_scheduler_ticks_immediately_then_sleeps_the_given_interval():
    calls = []
    sleeps = []

    def tick():
        calls.append("tick")

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            raise _StopLoop()

    with pytest.raises(_StopLoop):
        asyncio.run(run_scheduler(tick, interval_seconds=42, sleep=fake_sleep))

    assert calls == ["tick", "tick"]
    assert sleeps == [42, 42]


def test_run_scheduler_survives_a_crashing_tick(capsys):
    calls = []

    def tick():
        calls.append(1)
        raise RuntimeError("boom")

    async def fake_sleep(seconds):
        if len(calls) >= 2:
            raise _StopLoop()

    with pytest.raises(_StopLoop):
        asyncio.run(run_scheduler(tick, interval_seconds=1, sleep=fake_sleep))

    assert len(calls) == 2, "one crashed tick must not stop the next interval from firing"
    assert "tick failed" in capsys.readouterr().err


def test_run_scheduler_runs_a_blocking_tick_without_a_custom_sleep():
    """Regression: the default sleep is real asyncio.sleep, and tick runs in
    a thread (run_in_threadpool) rather than blocking the event loop."""
    calls = []

    def tick():
        calls.append(1)

    async def _drive():
        task = asyncio.create_task(run_scheduler(tick, interval_seconds=0.01))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_drive())

    assert len(calls) >= 1
