"""Rebook end to end: enrollment -> send -> reply -> booking, on shared rails.

The point of these tests is that Rebook adds NO new path. Every assertion below
is really asking "did this reuse the machinery Quote Chaser already proved?" —
the same tick(), the same reply handler, the same human-gated booking, the same
escalation. A second recovery system would pass none of them.
"""

import json
from datetime import datetime, timedelta

import recovery_service
from conftest import StubAgent
from db_models import (
    BOOKING_CANCELLED,
    BOOKING_CONFIRMED,
    BOOKING_PROPOSED,
    ORIGIN_REACTIVATION,
    Business,
    Job,
    OwnerNotification,
    RecoveryCampaign,
    RecoveryJob,
)
from deployment import deploy_role
from recovery_engine import REBOOK_DAYS, SEQUENCE_DAYS, TEMPLATES
from sqlmodel import Session, select

CUSTOMER = "+15551234567"


class StubCalendar:
    connected = True

    def get_available_slots(self, business_hours="", days_ahead=7, count=3):
        return ["Tuesday 09/02 morning (9am-12pm)", "Wednesday 09/03 afternoon (1pm-4pm)"][:count]


def _client(session: Session) -> Business:
    c = Business(
        business_name="Ridgeline Plumbing",
        trade="Plumbing",
        hours="Mon-Sat 7am-7pm",
        pricing_faq="n/a",
        escalation_phone="+15550000000",
        inbound_number="+15559990000",
    )
    session.add(c)
    session.commit()
    session.refresh(c)
    assert c.id is not None
    deploy_role(session, c.id, "retention_manager")
    session.refresh(c)
    return c


def _cancelled(session: Session, client: Business) -> Job:
    job = Job(
        business_id=client.id,
        customer_phone=CUSTOMER,
        customer_name="Mike",
        service_type="AC install",
        urgency="routine",
        callback_number=CUSTOMER,
        booking_status=BOOKING_CANCELLED,
        cancelled_at=datetime.utcnow() - timedelta(hours=48),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def _stub(monkeypatch, tool_name, tool_input, reply=""):
    monkeypatch.setattr(
        recovery_service,
        "agent",
        StubAgent(
            {
                "reply": reply,
                "jobs": [],
                "new_messages": [],
                "pending_tool_call": {"name": tool_name, "input": tool_input},
            }
        ),
    )


# --- shape of the sequence ----------------------------------------------------


def test_rebook_uses_a_shorter_sequence_than_the_quote_face():
    """A cancelled booking decays faster than a cold estimate, and the customer
    has already said "not this time" once."""
    assert REBOOK_DAYS == [1, 3, 7]
    assert len(REBOOK_DAYS) < len(SEQUENCE_DAYS)
    assert max(REBOOK_DAYS) < max(SEQUENCE_DAYS)


def test_rebook_copy_never_asserts_a_time():
    """Offering a slot is the reply path's job, and only from real availability.
    A template that names a day would be inventing one."""
    for day, text in TEMPLATES["rebook"].items():
        lowered = text.lower()
        for weekday in ("monday", "tuesday", "wednesday", "thursday", "friday"):
            assert weekday not in lowered, f"day {day} names a time: {text}"
        assert "am-" not in lowered and "pm-" not in lowered, f"day {day} names a window: {text}"


def test_the_first_rebook_message_actually_sends(session, monkeypatch):
    sent_messages = []

    class Recorder:
        def send(self, from_number, to_number, body):
            sent_messages.append({"to": to_number, "body": body})

    monkeypatch.setattr(recovery_service, "sms_channel", Recorder())
    client = _client(session)
    _cancelled(session, client)
    recovery_service.enroll_cancelled_jobs(session)

    campaign = session.exec(select(RecoveryCampaign)).first()
    assert campaign is not None
    campaign.started_at = datetime.utcnow() - timedelta(days=2)
    session.add(campaign)
    session.commit()

    sent = recovery_service.tick(session)

    assert len(sent) == 1
    assert sent_messages[0]["to"] == CUSTOMER
    assert "didn't work out" in sent_messages[0]["body"]


# --- reply handling reuses the existing rails ---------------------------------


def test_an_interested_reply_offers_real_slots_and_never_confirms(session, monkeypatch):
    """The human-gated booking path: a customer choosing a slot produces
    BOOKING_PROPOSED. Only a human can reach BOOKING_CONFIRMED."""
    monkeypatch.setattr(recovery_service, "get_calendar_provider", lambda c: StubCalendar())
    client = _client(session)
    _cancelled(session, client)
    recovery_service.enroll_cancelled_jobs(session)
    job = session.exec(select(RecoveryJob)).first()
    assert job is not None
    job.last_sent_day = 1
    session.add(job)
    session.commit()

    _stub(monkeypatch, "record_response", {"intent": "interested"})
    reply = recovery_service.handle_recovery_reply(session, client, job, "yes please")
    assert "Tuesday 09/02 morning (9am-12pm)" in reply
    session.refresh(job)
    assert job.current_status == "awaiting_slot"

    _stub(monkeypatch, "confirm_slot", {"slot_index": 0})
    recovery_service.handle_recovery_reply(session, client, job, "the first one")
    session.refresh(job)

    booked = session.exec(select(Job).where(Job.booking_status == BOOKING_PROPOSED)).all()
    assert len(booked) == 1, "a rebooked job should be PROPOSED"
    assert booked[0].booking_status != BOOKING_CONFIRMED
    assert booked[0].origin == ORIGIN_REACTIVATION, "credited to Retention Manager"
    assert job.current_status == "booked"


def test_the_cancelled_job_itself_is_never_resurrected(session, monkeypatch):
    """db_models: cancellation is terminal, "a customer who comes back gets a
    NEW Job rather than a resurrected one"."""
    monkeypatch.setattr(recovery_service, "get_calendar_provider", lambda c: StubCalendar())
    client = _client(session)
    original = _cancelled(session, client)
    recovery_service.enroll_cancelled_jobs(session)
    job = session.exec(select(RecoveryJob)).first()
    assert job is not None
    job.last_sent_day = 1
    job.current_status = "awaiting_slot"
    job.offered_slots_json = json.dumps(["Tuesday 09/02 morning (9am-12pm)"])
    session.add(job)
    session.commit()

    _stub(monkeypatch, "confirm_slot", {"slot_index": 0})
    recovery_service.handle_recovery_reply(session, client, job, "yes")

    session.refresh(original)
    assert original.booking_status == BOOKING_CANCELLED, "the cancelled row must not be mutated"
    assert original.cancelled_at is not None


def test_a_declining_reply_stops_the_sequence(session, monkeypatch):
    client = _client(session)
    _cancelled(session, client)
    recovery_service.enroll_cancelled_jobs(session)
    job = session.exec(select(RecoveryJob)).first()
    assert job is not None
    job.last_sent_day = 1
    session.add(job)
    session.commit()

    _stub(monkeypatch, "record_response", {"intent": "not_interested"})
    recovery_service.handle_recovery_reply(session, client, job, "no thanks")

    session.refresh(job)
    assert job.current_status == "declined"
    assert job.current_status not in recovery_service.ACTIVE_STATUSES


def test_a_high_risk_reply_escalates_to_a_human(session, monkeypatch):
    """Price, complaint and refund talk after a cancellation is exactly where an
    automated sequence should stop and fetch a person."""
    client = _client(session)
    _cancelled(session, client)
    recovery_service.enroll_cancelled_jobs(session)
    job = session.exec(select(RecoveryJob)).first()
    assert job is not None
    job.last_sent_day = 1
    session.add(job)
    session.commit()

    _stub(
        monkeypatch,
        "escalate_to_owner",
        {"reason": "wants a refund and is unhappy about the cancellation"},
    )
    recovery_service.handle_recovery_reply(session, client, job, "I want my money back")

    session.refresh(job)
    assert job.current_status == "escalated"
    assert job.current_status not in recovery_service.ACTIVE_STATUSES
    assert session.exec(select(OwnerNotification)).all(), "owner was never told"


def test_a_stop_reply_is_honoured(session, monkeypatch):
    """Compliance is channel-wide, not per face."""
    client = _client(session)
    _cancelled(session, client)
    recovery_service.enroll_cancelled_jobs(session)
    job = session.exec(select(RecoveryJob)).first()
    assert job is not None
    job.last_sent_day = 1
    session.add(job)
    session.commit()

    recovery_service.handle_recovery_reply(session, client, job, "STOP")

    session.refresh(job)
    # "declined" is the status the STOP path has always set — the guarantee
    # being tested is that the sequence stops, not the name of the state.
    assert job.current_status == "declined"
    assert job.current_status not in recovery_service.ACTIVE_STATUSES


# --- no regression to the faces that already worked ---------------------------


def test_quote_chaser_still_runs_its_own_full_sequence(session):
    """Rebook must not shorten anyone else's sequence."""
    client = _client(session)
    assert client.id is not None
    deploy_role(session, client.id, "quote_chaser")
    session.refresh(client)

    job = Job(
        business_id=client.id,
        customer_phone="+15559998888",
        customer_name="Dana",
        service_type="AC replacement",
        urgency="routine",
        callback_number="+15559998888",
        is_estimate=True,
        completed_at=datetime.utcnow() - timedelta(hours=2),
    )
    session.add(job)
    session.commit()

    enrolled = recovery_service.enroll_completed_estimates(session)
    assert len(enrolled) == 1
    campaign = session.get(RecoveryCampaign, enrolled[0].campaign_id)
    assert campaign is not None and campaign.face == "quote"


def test_one_job_can_never_be_in_two_faces_at_once(session):
    """The unique index makes "one job, one sequence" a property of the data
    rather than of the deployment topology."""
    client = _client(session)
    assert client.id is not None
    deploy_role(session, client.id, "quote_chaser")
    session.refresh(client)

    # A cancelled estimate is eligible for BOTH enrollment paths on paper.
    job = Job(
        business_id=client.id,
        customer_phone=CUSTOMER,
        customer_name="Mike",
        service_type="AC install",
        urgency="routine",
        callback_number=CUSTOMER,
        is_estimate=True,
        completed_at=datetime.utcnow() - timedelta(hours=48),
        booking_status=BOOKING_CANCELLED,
        cancelled_at=datetime.utcnow() - timedelta(hours=48),
    )
    session.add(job)
    session.commit()

    recovery_service.enroll_completed_estimates(session)
    recovery_service.enroll_cancelled_jobs(session)

    rows = session.exec(select(RecoveryJob).where(RecoveryJob.source_job_id == job.id)).all()
    assert len(rows) == 1, f"one job produced {len(rows)} sequences: {[r.id for r in rows]}"
