import channels


def test_get_channel_returns_console_channel_and_warns_when_credentials_missing(monkeypatch, capsys):
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)

    result = channels.get_channel()

    assert isinstance(result, channels.ConsoleChannel)
    captured = capsys.readouterr()
    assert "TWILIO_ACCOUNT_SID" in captured.err
    assert "WARNING" in captured.err


def test_get_channel_returns_twilio_channel_without_warning_when_credentials_set(monkeypatch, capsys):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC_test")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "token_test")

    result = channels.get_channel()

    assert isinstance(result, channels.TwilioChannel)
    captured = capsys.readouterr()
    assert "WARNING" not in captured.err
