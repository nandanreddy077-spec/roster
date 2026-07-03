import json
from datetime import datetime, timedelta

from sqlmodel import Session, select

from db_models import Client, Job
import referral_service


def make_client(session: Session, referral_incentive=None) -> Client:
    client = Client(
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
        client_id=client.id, service_type="AC repair", urgency="routine",
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
        client_id=client.id, service_type="AC repair", urgency="routine",
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
        client_id=client.id, service_type="AC repair", urgency="routine",
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
        client_id=client.id, service_type="AC repair", urgency="routine",
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
        client_id=client.id, service_type="AC repair", urgency="routine",
        callback_number="+1", completed_at=datetime.utcnow() - timedelta(days=5),
    )
    good_job = Job(
        client_id=client.id, service_type="Furnace repair", urgency="routine",
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
