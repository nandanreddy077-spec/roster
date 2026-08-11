import logging
from datetime import datetime

import pytest
import recovery_tick
from db_models import SchedulerHeartbeat
from sqlmodel import Session, select


def _messages(caplog) -> list[str]:
    return [r.message for r in caplog.records]


def test_run_executes_without_error(monkeypatch, test_engine, caplog):
    monkeypatch.setattr(recovery_tick, "engine", test_engine)
    # Deterministic regardless of wall-clock time: this test is about run()
    # wiring every step together, not about send_hours_ok's own boundary
    # (that's covered by test_channels.py).
    monkeypatch.setattr(recovery_tick, "send_hours_ok", lambda: True)

    with caplog.at_level(logging.INFO, logger="recovery_tick"):
        recovery_tick.run()

    by_message = {r.message: r for r in caplog.records}
    assert by_message["Recovery tick sent"].sent == 0
    assert by_message["Referrals tick sent"].sent == 0
    assert by_message["Reviews tick sent"].sent == 0
    assert by_message["Review follow-ups tick sent"].sent == 0
    assert by_message["Quote Chaser tick"].enrolled == 0
    assert by_message["Lead Qualifier tick"].qualified == 0
    assert by_message["Dispatcher tick"].planned == 0


def test_run_skips_sends_but_still_qualifies_and_dispatches_outside_send_hours(
    monkeypatch, test_engine, caplog
):
    monkeypatch.setattr(recovery_tick, "engine", test_engine)
    monkeypatch.setattr(recovery_tick, "send_hours_ok", lambda: False)

    with caplog.at_level(logging.INFO, logger="recovery_tick"):
        recovery_tick.run()

    messages = _messages(caplog)
    by_message = {r.message: r for r in caplog.records}
    assert by_message["Quote Chaser tick"].enrolled == 0
    assert by_message["Lead Qualifier tick"].qualified == 0
    assert by_message["Dispatcher tick"].planned == 0
    assert any("outside send hours" in m for m in messages)
    assert "Recovery tick sent" not in messages
    assert "Referrals tick sent" not in messages
    assert "Reviews tick sent" not in messages


# ---- the heartbeat /health reads -------------------------------------------
# Before this, nothing anywhere recorded whether recovery_tick.run() had ever
# completed -- only what it printed to stdout, which nobody was watching.


def test_a_successful_run_records_an_ok_heartbeat(monkeypatch, test_engine):
    monkeypatch.setattr(recovery_tick, "engine", test_engine)
    monkeypatch.setattr(recovery_tick, "send_hours_ok", lambda: True)
    before = datetime.utcnow()

    recovery_tick.run()

    with Session(test_engine) as s:
        hb = s.get(SchedulerHeartbeat, 1)
    assert hb is not None
    assert hb.ok is True
    assert hb.error is None
    assert hb.last_tick_at >= before


def test_a_failing_run_records_the_failure_and_still_raises(monkeypatch, test_engine):
    """The heartbeat must reflect a crashed tick, not just a missing one --
    and the crash must still propagate, since scheduler.py's own try/except
    is what keeps the loop alive and logs it."""
    monkeypatch.setattr(recovery_tick, "engine", test_engine)

    def _boom():
        raise RuntimeError("qualifier exploded")

    monkeypatch.setattr(recovery_tick, "qualify_new_jobs", lambda session: _boom())

    with pytest.raises(RuntimeError, match="qualifier exploded"):
        recovery_tick.run()

    with Session(test_engine) as s:
        hb = s.get(SchedulerHeartbeat, 1)
    assert hb is not None
    assert hb.ok is False
    assert "qualifier exploded" in hb.error


def test_a_later_run_overwrites_the_same_row_not_a_new_one(monkeypatch, test_engine):
    monkeypatch.setattr(recovery_tick, "engine", test_engine)
    monkeypatch.setattr(recovery_tick, "send_hours_ok", lambda: True)

    recovery_tick.run()
    with Session(test_engine) as s:
        first_id = s.get(SchedulerHeartbeat, 1).id

    recovery_tick.run()
    with Session(test_engine) as s:
        rows = s.exec(select(SchedulerHeartbeat)).all()

    assert len(rows) == 1
    assert rows[0].id == first_id
