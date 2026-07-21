from db_models import Business, Job
from upsell_engine import build_upsell_message, send_upsell_message


def test_build_upsell_message_includes_business_and_service():
    body = build_upsell_message("Ridgeline Plumbing", "drain cleaning")
    assert "Ridgeline Plumbing" in body
    assert "drain cleaning" in body


def test_build_upsell_message_falls_back_when_service_type_missing():
    body = build_upsell_message("Ridgeline Plumbing", None)
    assert "recent job" in body


class _FakeChannel:
    def __init__(self):
        self.sent = []

    def send(self, from_number, to_number, body):
        self.sent.append((from_number, to_number, body))


def test_send_upsell_message_skipped_without_callback_number():
    business = Business(business_name="Ridgeline Plumbing", inbound_number="+1500")
    job = Job(business_id=1, service_type="drain cleaning", urgency="routine", callback_number=None)
    channel = _FakeChannel()
    result = send_upsell_message(business, job, channel)
    assert result is None
    assert channel.sent == []


def test_send_upsell_message_sends_and_returns_body():
    business = Business(business_name="Ridgeline Plumbing", inbound_number="+1500")
    job = Job(business_id=1, service_type="drain cleaning", urgency="routine", callback_number="+1999")
    channel = _FakeChannel()
    result = send_upsell_message(business, job, channel)
    assert result is not None
    assert channel.sent == [("+1500", "+1999", result)]
