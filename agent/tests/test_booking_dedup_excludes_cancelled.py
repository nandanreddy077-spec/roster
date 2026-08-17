"""A cancelled job must never absorb a new booking.

db_models.BOOKING_CANCELLED is documented as terminal: "The appointment is not
happening... A customer who comes back gets a NEW Job rather than a resurrected
one, so `completed_at` and every employee that reads it keep their existing
meaning untouched."

book_job's dedup window matched on completed_at IS NULL, which a cancelled job
also satisfies — so a customer who cancelled and rebooked the same service
within BOOKING_DEDUP_WINDOW_HOURS had their new booking merged into the dead
row. Because booking_status is deliberately never revised on a merge, the
result was a customer told "you're booked" with no bookable job anywhere.

Found while building Rebook, whose entire purpose is booking after a
cancellation — but the bug predates it and hits Frontdesk identically.
"""

from datetime import datetime, timedelta

from bookings import book_job
from db_models import BOOKING_CANCELLED, BOOKING_PROPOSED, BOOKING_REQUESTED, Business, Job
from sqlmodel import Session, select


def _client(session: Session) -> Business:
    c = Business(
        business_name="Ridgeline Plumbing",
        trade="Plumbing",
        hours="Mon-Sat 7am-7pm",
        escalation_phone="+15550000000",
        inbound_number="+15559990000",
    )
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


def test_a_cancelled_job_does_not_absorb_a_new_booking(session):
    client = _client(session)
    cancelled = Job(
        business_id=client.id,
        customer_phone="+15551234567",
        service_type="AC install",
        urgency="routine",
        callback_number="+15551234567",
        booking_status=BOOKING_CANCELLED,
        cancelled_at=datetime.utcnow() - timedelta(hours=2),
    )
    session.add(cancelled)
    session.commit()
    session.refresh(cancelled)

    new_job, created = book_job(
        session,
        client,
        "+15551234567",
        "+15551234567",
        {"service_type": "AC install", "urgency": "routine"},
        booking_status=BOOKING_PROPOSED,
    )

    assert created is True, "a cancelled job absorbed the new booking"
    assert new_job.id != cancelled.id
    assert new_job.booking_status == BOOKING_PROPOSED
    session.refresh(cancelled)
    assert cancelled.booking_status == BOOKING_CANCELLED, "the dead row was resurrected"


def test_a_genuinely_open_job_still_dedups(session):
    """The dedup window must keep working — this fix narrows it, not removes
    it. Two log_job calls for one live request stay one job."""
    client = _client(session)
    first, created_first = book_job(
        session,
        client,
        "+15551234567",
        "+15551234567",
        {"service_type": "AC install", "urgency": "routine"},
    )
    second, created_second = book_job(
        session,
        client,
        "+15551234567",
        "+15551234567",
        {"service_type": "AC install", "urgency": "routine", "customer_name": "Mike"},
    )

    assert created_first is True
    assert created_second is False, "a live duplicate request created a second job"
    assert first.id == second.id
    assert second.customer_name == "Mike", "the merge should still enrich the open job"
    assert len(session.exec(select(Job)).all()) == 1


def test_the_merge_still_never_upgrades_booking_status(session):
    """Unchanged guarantee: a detail-merge must not quietly promote REQUESTED
    to PROPOSED."""
    client = _client(session)
    first, _ = book_job(
        session,
        client,
        "+15551234567",
        "+15551234567",
        {"service_type": "AC install", "urgency": "routine"},
    )
    assert first.booking_status == BOOKING_REQUESTED

    merged, created = book_job(
        session,
        client,
        "+15551234567",
        "+15551234567",
        {"service_type": "AC install", "urgency": "routine"},
        booking_status=BOOKING_PROPOSED,
    )

    assert created is False
    assert merged.booking_status == BOOKING_REQUESTED, "a merge upgraded the status"
