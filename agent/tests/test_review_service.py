"""Reviews: automatic review-request texts triggered off Job.completed_at,
after waiting REVIEW_DELAY_DAYS. Moved off app.py's synchronous "Mark done"
path (2026-07-29/30 plan) onto the same tick-based, delayed mechanism
referral_service.send_due_referral_asks already uses — safe to call more
than once a day since review_requested_at gates re-sending.
"""
from datetime import datetime, timedelta

from sqlmodel import Session

from db_models import Business, Job
import review_service


def make_client(session: Session, review_link=None) -> Business:
    client = Business(
        business_name="Ridgeline Plumbing", trade="Plumbing", hours="9-5",
        pricing_faq="n/a", escalation_phone="+15550000000",
        inbound_number="+15559990000", review_link=review_link,
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


class ExplodingSMSChannel:
    def send(self, from_number, to_number, body):
        raise RuntimeError("simulated Twilio failure")


def test_send_due_review_requests_sends_to_eligible_job(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(review_service, "sms_channel", fake_channel)

    client = make_client(session, review_link="https://g.page/r/test")
    job = Job(
        business_id=client.id, service_type="AC repair", urgency="routine",
        callback_number="+15551234567",
        completed_at=datetime.utcnow() - timedelta(days=2),
    )
    session.add(job)
    session.commit()

    sent = review_service.send_due_review_requests(session)

    assert len(sent) == 1
    assert fake_channel.sent[0]["to"] == "+15551234567"
    assert "https://g.page/r/test" in fake_channel.sent[0]["body"]
    assert "Ridgeline Plumbing" in fake_channel.sent[0]["body"]
    session.refresh(job)
    assert job.review_requested_at is not None


def test_send_due_review_requests_skips_job_completed_too_recently(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(review_service, "sms_channel", fake_channel)

    client = make_client(session, review_link="https://g.page/r/test")
    job = Job(
        business_id=client.id, service_type="AC repair", urgency="routine",
        callback_number="+15551234567", completed_at=datetime.utcnow(),
    )
    session.add(job)
    session.commit()

    sent = review_service.send_due_review_requests(session)

    assert sent == []
    assert fake_channel.sent == []
    session.refresh(job)
    assert job.review_requested_at is None


def test_send_due_review_requests_skips_business_without_review_link(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(review_service, "sms_channel", fake_channel)

    client = make_client(session, review_link=None)
    job = Job(
        business_id=client.id, service_type="AC repair", urgency="routine",
        callback_number="+15551234567",
        completed_at=datetime.utcnow() - timedelta(days=2),
    )
    session.add(job)
    session.commit()

    sent = review_service.send_due_review_requests(session)

    assert sent == []
    assert fake_channel.sent == []


def test_send_due_review_requests_skips_job_without_callback_number(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(review_service, "sms_channel", fake_channel)

    client = make_client(session, review_link="https://g.page/r/test")
    job = Job(
        business_id=client.id, service_type="AC repair", urgency="routine",
        callback_number=None, completed_at=datetime.utcnow() - timedelta(days=2),
    )
    session.add(job)
    session.commit()

    sent = review_service.send_due_review_requests(session)

    assert sent == []


def test_send_due_review_requests_is_idempotent_across_two_calls(session, monkeypatch):
    """Safe to call more than once a day — review_requested_at gates it, the
    same idempotency guarantee referral_service's own tick function has."""
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(review_service, "sms_channel", fake_channel)

    client = make_client(session, review_link="https://g.page/r/test")
    job = Job(
        business_id=client.id, service_type="AC repair", urgency="routine",
        callback_number="+15551234567",
        completed_at=datetime.utcnow() - timedelta(days=2),
    )
    session.add(job)
    session.commit()

    review_service.send_due_review_requests(session)
    second = review_service.send_due_review_requests(session)

    assert second == []
    assert len(fake_channel.sent) == 1


def test_send_due_review_requests_survives_a_send_failure(session, monkeypatch):
    """A failed send must not crash the tick or leave review_requested_at
    set for a message that never actually went out — mirrors
    app.py's old complete_job guarantee ("the review text is a bonus"),
    now upheld by the tick instead."""
    monkeypatch.setattr(review_service, "sms_channel", ExplodingSMSChannel())

    client = make_client(session, review_link="https://g.page/r/test")
    job = Job(
        business_id=client.id, service_type="AC repair", urgency="routine",
        callback_number="+15551234567",
        completed_at=datetime.utcnow() - timedelta(days=2),
    )
    session.add(job)
    session.commit()

    sent = review_service.send_due_review_requests(session)  # must not raise

    assert sent == []
    session.refresh(job)
    assert job.review_requested_at is None
