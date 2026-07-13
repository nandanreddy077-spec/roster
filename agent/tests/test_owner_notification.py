from db_models import Business, Job
from service import handle_customer_message
from notifications import notify_owner_of_booking


class SpyChannel:
    def __init__(self):
        self.sent = []

    def send(self, from_number, to_number, body):
        self.sent.append((from_number, to_number, body))


def _job(**kw):
    defaults = dict(
        business_id=1, customer_phone="+15559998888", customer_name="Sarah",
        service_type="AC Repair", urgency="same_day", callback_number="+15559998888",
    )
    defaults.update(kw)
    return Job(**defaults)


# ---- unit: the notification function (no DB) --------------------------------

def test_notifies_owner_with_all_job_details():
    biz = Business(business_name="B", trade="hvac",
                   escalation_phone="+15550001111", inbound_number="+15550002222")
    spy = SpyChannel()
    assert notify_owner_of_booking(biz, _job(), employee_name="Frontdesk", channel=spy) is True
    assert len(spy.sent) == 1
    from_n, to_n, body = spy.sent[0]
    assert from_n == "+15550002222"      # sent FROM the business's own number
    assert to_n == "+15550001111"        # TO the owner's escalation number
    for token in ("Frontdesk", "AC Repair", "same_day", "Sarah", "+15559998888"):
        assert token in body


def test_skips_when_no_escalation_phone():
    biz = Business(business_name="B", trade="hvac", escalation_phone="", inbound_number="+1")
    spy = SpyChannel()
    assert notify_owner_of_booking(biz, _job(), channel=spy) is False
    assert spy.sent == []


def test_skips_dashboard_test_bookings():
    biz = Business(business_name="B", trade="hvac",
                   escalation_phone="+15550001111", inbound_number="+1")
    spy = SpyChannel()
    assert notify_owner_of_booking(biz, _job(customer_phone="portal-test"), channel=spy) is False
    assert notify_owner_of_booking(biz, _job(customer_phone="dashboard"), channel=spy) is False
    assert spy.sent == []


def test_missing_name_and_callback_degrade_gracefully():
    biz = Business(business_name="B", trade="hvac",
                   escalation_phone="+15550001111", inbound_number="+15550002222")
    spy = SpyChannel()
    notify_owner_of_booking(biz, _job(customer_name=None, callback_number=None), channel=spy)
    body = spy.sent[0][2]
    assert "a customer" in body and "no number given" in body


def test_send_failure_never_raises_into_booking():
    class BoomChannel:
        def send(self, *a):
            raise RuntimeError("twilio down")
    biz = Business(business_name="B", trade="hvac",
                   escalation_phone="+15550001111", inbound_number="+1")
    assert notify_owner_of_booking(biz, _job(), channel=BoomChannel()) is False


# ---- integration: a real booking through the service layer notifies the owner

class _FakeAgent:
    def respond(self, client_config, history, tools=None, system_prompt=None, max_iters=None):
        return {
            "reply": "You're booked!",
            "jobs": [{"id": "t1", "input": {
                "service_type": "AC Repair", "urgency": "same_day", "customer_name": "Sarah",
            }}],
            "new_messages": [], "pending_tool_call": None,
        }


def test_handle_customer_message_texts_owner_on_booking(session, monkeypatch):
    import service
    import notifications
    monkeypatch.setattr(service, "agent", _FakeAgent())
    spy = SpyChannel()
    monkeypatch.setattr(notifications, "_owner_channel", spy)
    biz = Business(business_name="B", trade="hvac", email="own@test.io", frontdesk_live=True,
                   trial_cap_cents=10000, escalation_phone="+15550001111", inbound_number="+15550002222")
    session.add(biz)
    session.commit()
    session.refresh(biz)

    handle_customer_message(session, biz, "+15557778888", "my AC is dead")

    assert len(spy.sent) == 1
    _, to_n, body = spy.sent[0]
    assert to_n == "+15550001111"
    assert "AC Repair" in body and "Frontdesk" in body


def test_dashboard_test_chat_does_not_text_owner(session, monkeypatch):
    import service
    import notifications
    monkeypatch.setattr(service, "agent", _FakeAgent())
    spy = SpyChannel()
    monkeypatch.setattr(notifications, "_owner_channel", spy)
    biz = Business(business_name="B", trade="hvac", email="own2@test.io", frontdesk_live=True,
                   trial_cap_cents=10000, escalation_phone="+15550001111", inbound_number="+15550002222")
    session.add(biz)
    session.commit()
    session.refresh(biz)

    handle_customer_message(session, biz, "portal-test", "testing my receptionist")

    assert spy.sent == []   # owner isn't spammed while testing their own AI
