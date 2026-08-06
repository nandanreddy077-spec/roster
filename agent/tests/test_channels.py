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
    monkeypatch.setenv("TWILIO_MESSAGING_SERVICE_SID", "MG_test")

    result = channels.get_channel()

    assert isinstance(result, channels.TwilioChannel)
    captured = capsys.readouterr()
    assert "WARNING" not in captured.err


def test_missing_messaging_service_warns_about_carrier_filtering(monkeypatch, capsys):
    """Unregistered A2P traffic still sends, so nothing else would ever
    surface that US carriers are quietly dropping it."""
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC_test")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "token_test")
    monkeypatch.delenv("TWILIO_MESSAGING_SERVICE_SID", raising=False)

    channels.get_channel()

    captured = capsys.readouterr()
    assert "10DLC" in captured.err


# ---- normalize_phone ---------------------------------------------------------
# The bug these pin: an owner typed `770-288-1238`, Twilio resolved it against
# the US sender, and every escalation alert went to a stranger in Georgia while
# reporting success.

def test_a_number_a_human_typed_becomes_e164():
    for typed in ("770-288-1238", "(770) 288-1238", "770.288.1238", " 7702881238 "):
        assert channels.normalize_phone(typed) == "+17702881238", typed


def test_a_us_number_with_a_leading_one_is_not_double_prefixed():
    assert channels.normalize_phone("17702881238") == "+17702881238"
    assert channels.normalize_phone("+1 (770) 288-1238") == "+17702881238"


def test_an_explicit_country_code_is_never_rewritten_to_us():
    """The founder tests from India. Rewriting +91 to +1 would send the alert
    to a US number that happens to share the digits — the original bug."""
    assert channels.normalize_phone("+91 98765 43210") == "+919876543210"


def test_an_unrecognisable_number_is_left_alone_to_fail_loudly():
    """Better a Twilio rejection the founder can see than a silent delivery
    to a number we invented."""
    assert channels.normalize_phone("12345") == "12345"
    assert channels.normalize_phone("") == ""


class _Recorder:
    def __init__(self):
        self.sent = {}

    @property
    def messages(self):
        return self

    def create(self, **kwargs):
        self.sent = kwargs


def _channel(monkeypatch, messaging_service_sid=""):
    ch = channels.TwilioChannel.__new__(channels.TwilioChannel)
    ch._client = _Recorder()
    ch._messaging_service_sid = messaging_service_sid
    return ch


def test_send_normalizes_a_recipient_stored_before_this_fix(monkeypatch):
    """Rows written by the old code still hold bare digits; the send path is
    the last gate before a real handset."""
    ch = _channel(monkeypatch)
    ch.send("+16187473488", "7702881238", "hi")
    assert ch._client.sent["to"] == "+17702881238"
    assert ch._client.sent["from_"] == "+16187473488"


def test_send_uses_the_messaging_service_when_one_is_registered(monkeypatch):
    ch = _channel(monkeypatch, messaging_service_sid="MG_test")
    ch.send("+16187473488", "7702881238", "hi")
    assert ch._client.sent["messaging_service_sid"] == "MG_test"
    assert "from_" not in ch._client.sent, "from_ overrides the registered campaign sender"
