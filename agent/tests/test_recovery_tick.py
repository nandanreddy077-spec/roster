import recovery_tick


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
