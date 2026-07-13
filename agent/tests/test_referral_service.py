import json
from datetime import datetime, timedelta

from sqlmodel import Session, select

from db_models import Business, Job, ReferralLead
from conftest import StubAgent
import referral_service


def make_client(session: Session, referral_incentive=None) -> Business:
    client = Business(
        business_name="Test Co", trade="HVAC", services_json=json.dumps(["AC repair"]),
        hours="9-5", pricing_faq="n/a", escalation_phone="+15550000000",
        inbound_number="+15559990000", referral_incentive=referral_incentive,
    )
    session.add(client)
    session.commit()
    session.refresh(client)
    return client


class FakeSMSChannel:
    def __init__(self):
        self.sent = []

    def send(self, from_number, to_number, body):
        self.sent.append({"from": from_number, "to": to_number, "body": body})


def test_send_due_referral_asks_sends_to_eligible_job(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(referral_service, "sms_channel", fake_channel)

    client = make_client(session, referral_incentive="$25 off your next service")
    job = Job(
        business_id=client.id, service_type="AC repair", urgency="routine",
        customer_name="Mike", callback_number="+15551234567",
        completed_at=datetime.utcnow() - timedelta(days=5),
    )
    session.add(job)
    session.commit()

    sent = referral_service.send_due_referral_asks(session)

    assert len(sent) == 1
    assert fake_channel.sent[0]["to"] == "+15551234567"
    assert "Mike" in fake_channel.sent[0]["body"]
    assert "$25 off your next service" in fake_channel.sent[0]["body"]
    session.refresh(job)
    assert job.referral_sent_at is not None


def test_send_due_referral_asks_skips_job_completed_too_recently(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(referral_service, "sms_channel", fake_channel)

    client = make_client(session, referral_incentive="$25 off")
    job = Job(
        business_id=client.id, service_type="AC repair", urgency="routine",
        callback_number="+1", completed_at=datetime.utcnow() - timedelta(days=1),
    )
    session.add(job)
    session.commit()

    sent = referral_service.send_due_referral_asks(session)

    assert sent == []
    assert fake_channel.sent == []


def test_send_due_referral_asks_skips_client_without_incentive(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(referral_service, "sms_channel", fake_channel)

    client = make_client(session, referral_incentive=None)
    job = Job(
        business_id=client.id, service_type="AC repair", urgency="routine",
        callback_number="+1", completed_at=datetime.utcnow() - timedelta(days=5),
    )
    session.add(job)
    session.commit()

    sent = referral_service.send_due_referral_asks(session)

    assert sent == []


def test_send_due_referral_asks_does_not_resend(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(referral_service, "sms_channel", fake_channel)

    client = make_client(session, referral_incentive="$25 off")
    job = Job(
        business_id=client.id, service_type="AC repair", urgency="routine",
        callback_number="+1", completed_at=datetime.utcnow() - timedelta(days=5),
    )
    session.add(job)
    session.commit()

    referral_service.send_due_referral_asks(session)
    second = referral_service.send_due_referral_asks(session)

    assert second == []
    assert len(fake_channel.sent) == 1


class RaisingSMSChannel:
    """Raises for one phone number, sends normally for everyone else — simulates
    a single bad number failing mid-batch."""

    def __init__(self, bad_phone):
        self.bad_phone = bad_phone
        self.sent = []

    def send(self, from_number, to_number, body):
        if to_number == self.bad_phone:
            raise RuntimeError("simulated Twilio failure")
        self.sent.append({"from": from_number, "to": to_number, "body": body})


def test_send_due_referral_asks_isolates_per_job_failure(session, monkeypatch):
    fake_channel = RaisingSMSChannel(bad_phone="+1")
    monkeypatch.setattr(referral_service, "sms_channel", fake_channel)

    client = make_client(session, referral_incentive="$25 off")
    bad_job = Job(
        business_id=client.id, service_type="AC repair", urgency="routine",
        callback_number="+1", completed_at=datetime.utcnow() - timedelta(days=5),
    )
    good_job = Job(
        business_id=client.id, service_type="Furnace repair", urgency="routine",
        callback_number="+2", completed_at=datetime.utcnow() - timedelta(days=5),
    )
    session.add(bad_job)
    session.add(good_job)
    session.commit()

    sent = referral_service.send_due_referral_asks(session)

    assert len(sent) == 1
    assert sent[0].callback_number == "+2"
    session.refresh(bad_job)
    assert bad_job.referral_sent_at is None
    session.refresh(good_job)
    assert good_job.referral_sent_at is not None


def test_find_active_referral_ask_matches_within_window(session):
    client = make_client(session, referral_incentive="$25 off")
    job = Job(
        business_id=client.id, service_type="AC repair", urgency="routine",
        callback_number="+1", completed_at=datetime.utcnow() - timedelta(days=5),
        referral_sent_at=datetime.utcnow() - timedelta(days=1),
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    found = referral_service.find_active_referral_ask(session, client.id, "+1")
    assert found is not None
    assert found.id == job.id


def test_find_active_referral_ask_ignores_expired_window(session):
    client = make_client(session, referral_incentive="$25 off")
    job = Job(
        business_id=client.id, service_type="AC repair", urgency="routine",
        callback_number="+1", completed_at=datetime.utcnow() - timedelta(days=10),
        referral_sent_at=datetime.utcnow() - timedelta(days=5),  # outside the 3-day window
    )
    session.add(job)
    session.commit()

    assert referral_service.find_active_referral_ask(session, client.id, "+1") is None


def test_find_active_referral_ask_ignores_already_captured(session):
    client = make_client(session, referral_incentive="$25 off")
    job = Job(
        business_id=client.id, service_type="AC repair", urgency="routine",
        callback_number="+1", completed_at=datetime.utcnow() - timedelta(days=5),
        referral_sent_at=datetime.utcnow(),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    session.add(ReferralLead(
        business_id=client.id, source_job_id=job.id, asker_phone="+1", raw_reply_text="already replied",
    ))
    session.commit()

    assert referral_service.find_active_referral_ask(session, client.id, "+1") is None


def test_handle_referral_reply_extracts_structured_info(session, monkeypatch):
    client = make_client(session, referral_incentive="$25 off")
    job = Job(
        business_id=client.id, service_type="AC repair", urgency="routine",
        customer_name="Mike", callback_number="+1",
        completed_at=datetime.utcnow() - timedelta(days=5),
        referral_sent_at=datetime.utcnow(),
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    monkeypatch.setattr(
        referral_service,
        "agent",
        StubAgent({
            "reply": "",
            "jobs": [],
            "new_messages": [],
            "pending_tool_call": {
                "name": "record_referral",
                "input": {"referred_name": "Sarah", "referred_phone": "+15559998888"},
            },
        }),
    )

    reply = referral_service.handle_referral_reply(
        session, client, job, "yeah, my neighbor Sarah needs this, her number is 555-998-8888"
    )

    lead = session.exec(select(ReferralLead).where(ReferralLead.source_job_id == job.id)).first()
    assert lead is not None
    assert lead.referred_name == "Sarah"
    assert lead.referred_phone == "+15559998888"
    assert lead.raw_reply_text == "yeah, my neighbor Sarah needs this, her number is 555-998-8888"
    assert reply != ""


def test_handle_referral_reply_falls_back_to_raw_text_when_extraction_fails(session, monkeypatch):
    client = make_client(session, referral_incentive="$25 off")
    job = Job(
        business_id=client.id, service_type="AC repair", urgency="routine",
        customer_name="Mike", callback_number="+1",
        completed_at=datetime.utcnow() - timedelta(days=5),
        referral_sent_at=datetime.utcnow(),
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    monkeypatch.setattr(
        referral_service,
        "agent",
        StubAgent({"reply": "No worries!", "jobs": [], "new_messages": [], "pending_tool_call": None}),
    )

    reply = referral_service.handle_referral_reply(session, client, job, "no thanks")

    lead = session.exec(select(ReferralLead).where(ReferralLead.source_job_id == job.id)).first()
    assert lead is not None
    assert lead.referred_name is None
    assert lead.referred_phone is None
    assert lead.raw_reply_text == "no thanks"
    assert reply == "No worries!"
