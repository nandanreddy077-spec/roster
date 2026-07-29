import recovery_tick


def test_run_executes_without_error(monkeypatch, test_engine, capsys):
    monkeypatch.setattr(recovery_tick, "engine", test_engine)
    recovery_tick.run()
    captured = capsys.readouterr()
    assert "Recovery tick: sent 0 message(s)." in captured.out
    assert "Referrals: sent 0 message(s)." in captured.out
    assert "Reviews: sent 0 message(s)." in captured.out
    assert "Review follow-ups: sent 0 message(s)." in captured.out
