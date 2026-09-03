import logging
from datetime import datetime

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

    # Every phase ran and produced nothing (empty database). Each is logged as
    # "<name> tick" with a uniform `produced` count — see recovery_tick._phase.
    by_message = {r.message: r for r in caplog.records}
    for phase in (
        "Lead Qualifier",
        "Dispatcher",
        "Quote Chaser enrollment",
        "Recovery sends",
        "Referrals",
        "Reviews",
        "Review follow-ups",
        "Membership offers",
        "Membership follow-ups",
    ):
        assert by_message[f"{phase} tick"].produced == 0


def test_run_skips_sends_but_still_qualifies_and_dispatches_outside_send_hours(
    monkeypatch, test_engine, caplog
):
    monkeypatch.setattr(recovery_tick, "engine", test_engine)
    monkeypatch.setattr(recovery_tick, "send_hours_ok", lambda: False)

    with caplog.at_level(logging.INFO, logger="recovery_tick"):
        recovery_tick.run()

    messages = _messages(caplog)
    by_message = {r.message: r for r in caplog.records}
    assert by_message["Quote Chaser enrollment tick"].produced == 0
    assert by_message["Lead Qualifier tick"].produced == 0
    assert by_message["Dispatcher tick"].produced == 0
    assert any("outside send hours" in m for m in messages)
    assert "Recovery sends tick" not in messages
    assert "Referrals tick" not in messages
    assert "Reviews tick" not in messages


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


def test_a_failing_phase_is_recorded_and_the_rest_still_run(monkeypatch, test_engine, caplog):
    """P1-2: one phase raising must NOT abort the tick. Before this, a bug in
    Lead Qualifier silenced Reviews/Referral/Membership for an hour. Now the
    failure is named in the heartbeat, the crash does not propagate, and every
    later phase still runs."""
    monkeypatch.setattr(recovery_tick, "engine", test_engine)
    monkeypatch.setattr(recovery_tick, "send_hours_ok", lambda: True)

    def _boom(session):
        raise RuntimeError("qualifier exploded")

    monkeypatch.setattr(recovery_tick, "qualify_new_jobs", _boom)

    with caplog.at_level(logging.INFO, logger="recovery_tick"):
        recovery_tick.run()  # does not raise

    with Session(test_engine) as s:
        hb = s.get(SchedulerHeartbeat, 1)
    assert hb is not None
    assert hb.ok is False
    assert "Lead Qualifier" in hb.error and "qualifier exploded" in hb.error

    # A later phase still ran.
    assert "Membership follow-ups tick" in _messages(caplog)


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
