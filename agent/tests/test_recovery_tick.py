from datetime import datetime

import pytest
import recovery_tick
from db_models import SchedulerHeartbeat
from sqlmodel import Session, select


def test_run_executes_without_error(monkeypatch, test_engine, capsys):
    monkeypatch.setattr(recovery_tick, "engine", test_engine)
    # Deterministic regardless of wall-clock time: this test is about run()
    # wiring every step together, not about send_hours_ok's own boundary
    # (that's covered by test_channels.py).
    monkeypatch.setattr(recovery_tick, "send_hours_ok", lambda: True)
    recovery_tick.run()
    captured = capsys.readouterr()
    assert "Recovery tick: sent 0 message(s)." in captured.out
    assert "Referrals: sent 0 message(s)." in captured.out
    assert "Reviews: sent 0 message(s)." in captured.out
    assert "Review follow-ups: sent 0 message(s)." in captured.out
    assert "Quote Chaser: enrolled 0 estimate(s)." in captured.out
    assert "Lead Qualifier: qualified 0 job(s)." in captured.out
    assert "Dispatcher: planned 0 job(s)." in captured.out


def test_run_skips_sends_but_still_qualifies_and_dispatches_outside_send_hours(
    monkeypatch, test_engine, capsys
):
    monkeypatch.setattr(recovery_tick, "engine", test_engine)
    monkeypatch.setattr(recovery_tick, "send_hours_ok", lambda: False)
    recovery_tick.run()
    captured = capsys.readouterr()
    assert "Quote Chaser: enrolled 0 estimate(s)." in captured.out
    assert "Lead Qualifier: qualified 0 job(s)." in captured.out
    assert "Dispatcher: planned 0 job(s)." in captured.out
    assert "Outside send hours" in captured.out
    assert "Recovery tick:" not in captured.out
    assert "Referrals:" not in captured.out
    assert "Reviews:" not in captured.out


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
