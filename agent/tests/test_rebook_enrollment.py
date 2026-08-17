"""Rebook: a cancelled job should get one honest attempt at recovery.

A cancelled booking is a warmer, faster-decaying signal than a cold estimate —
the customer wanted the work recently enough to book it. It is also the easiest
thing in the product to get wrong, because every failure mode texts a real
person about a job they already dealt with:

  - texting someone who already rebooked
  - texting twice for the same cancellation
  - texting during an active reschedule negotiation
  - texting the moment they cancel, before anyone could reschedule by hand

Each of those has a test below. `RecoveryCampaign.face == "rebook"` reuses the
entire existing RecoveryJob machinery — tick(), handle_recovery_reply, the
state machine, the human-gated booking path. No second recovery system.
"""

from datetime import datetime, timedelta

import recovery_service
from db_models import (
    BOOKING_CANCELLED,
    BOOKING_CONFIRMED,
    BOOKING_PROPOSED,
    BOOKING_REQUESTED,
    BOOKING_RESCHEDULE_REQUESTED,
    Business,
    Job,
    RecoveryCampaign,
    RecoveryJob,
)
from deployment import deploy_role
from sqlmodel import Session, select

CUSTOMER = "+15551234567"


def make_client(session: Session, hire: str = "retention_manager") -> Business:
    client = Business(
        business_name="Ridgeline Plumbing",
        trade="Plumbing",
        hours="Mon-Sat 7am-7pm",
        pricing_faq="n/a",
        escalation_phone="+15550000000",
        inbound_number="+15559990000",
    )
    session.add(client)
    session.commit()
    session.refresh(client)
    assert client.id is not None
    if hire:
        deploy_role(session, client.id, hire)
        session.refresh(client)
    return client


def cancelled_job(
    session: Session,
    client: Business,
    hours_ago: float = 48,
    phone: str = CUSTOMER,
    status: str = BOOKING_CANCELLED,
) -> Job:
    job = Job(
        business_id=client.id,
        customer_phone=phone,
        customer_name="Mike",
        service_type="AC install",
        urgency="routine",
        callback_number=phone,
        booking_status=status,
        cancelled_at=datetime.utcnow() - timedelta(hours=hours_ago),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


# --- the core behaviour -------------------------------------------------------


def test_a_cancelled_job_is_enrolled_for_rebooking(session):
    client = make_client(session)
    job = cancelled_job(session, client)

    enrolled = recovery_service.enroll_cancelled_jobs(session)

    assert len(enrolled) == 1, enrolled
    assert enrolled[0].source_job_id == job.id
    assert enrolled[0].customer_phone == CUSTOMER
    campaign = session.get(RecoveryCampaign, enrolled[0].campaign_id)
    assert campaign is not None
    assert campaign.face == "rebook", "must reuse the RecoveryJob machinery, not a new system"


def test_the_same_cancelled_job_is_never_enrolled_twice(session):
    """Idempotency. The tick runs hourly; a second sequence for one cancellation
    means a real person gets two parallel conversations about the same job."""
    client = make_client(session)
    cancelled_job(session, client)

    first = recovery_service.enroll_cancelled_jobs(session)
    second = recovery_service.enroll_cancelled_jobs(session)
    third = recovery_service.enroll_cancelled_jobs(session)

    assert len(first) == 1
    assert second == [] and third == []
    assert len(session.exec(select(RecoveryJob)).all()) == 1


def test_a_job_cancelled_moments_ago_is_left_alone(session):
    """A same-day reschedule handled by a human must not race an automated
    sequence. The window exists so the owner gets first refusal."""
    client = make_client(session)
    cancelled_job(session, client, hours_ago=1)

    assert recovery_service.enroll_cancelled_jobs(session) == []


def test_a_customer_who_already_rebooked_is_not_chased(session):
    """The single worst outcome: texting "want to rebook?" to someone holding a
    confirmed appointment."""
    client = make_client(session)
    cancelled = cancelled_job(session, client)

    session.add(
        Job(
            business_id=client.id,
            customer_phone=CUSTOMER,
            customer_name="Mike",
            service_type="AC install",
            urgency="routine",
            callback_number=CUSTOMER,
            booking_status=BOOKING_CONFIRMED,
            created_at=cancelled.cancelled_at + timedelta(hours=1),
        )
    )
    session.commit()

    assert recovery_service.enroll_cancelled_jobs(session) == []


def test_a_successor_job_that_is_merely_requested_still_counts(session):
    """Not yet confirmed is still "this customer is already in a conversation
    about new work" — chasing them would collide with it."""
    client = make_client(session)
    cancelled = cancelled_job(session, client)

    session.add(
        Job(
            business_id=client.id,
            customer_phone=CUSTOMER,
            service_type="AC install",
            urgency="routine",
            callback_number=CUSTOMER,
            booking_status=BOOKING_REQUESTED,
            created_at=cancelled.cancelled_at + timedelta(hours=2),
        )
    )
    session.commit()

    assert recovery_service.enroll_cancelled_jobs(session) == []


def test_an_older_unrelated_job_does_not_block_enrollment(session):
    """Only work that came AFTER the cancellation means they moved on. Their
    history from last year does not."""
    client = make_client(session)
    cancelled = cancelled_job(session, client)

    session.add(
        Job(
            business_id=client.id,
            customer_phone=CUSTOMER,
            service_type="drain clear",
            urgency="routine",
            callback_number=CUSTOMER,
            booking_status=BOOKING_CONFIRMED,
            created_at=cancelled.cancelled_at - timedelta(days=200),
        )
    )
    session.commit()

    assert len(recovery_service.enroll_cancelled_jobs(session)) == 1


# --- what must stay untouched -------------------------------------------------


def test_jobs_that_should_not_be_rebooked_are_left_alone(session):
    """Every non-cancelled state. A live request, a proposed time and a
    confirmed appointment are all still in play — texting any of them a
    "want to rebook?" sequence would be wrong."""
    client = make_client(session)
    for i, status in enumerate(
        (BOOKING_REQUESTED, BOOKING_PROPOSED, BOOKING_CONFIRMED, BOOKING_RESCHEDULE_REQUESTED)
    ):
        cancelled_job(session, client, phone=f"+1555000{i:04d}", status=status)

    assert recovery_service.enroll_cancelled_jobs(session) == []


def test_a_reschedule_in_progress_is_never_hijacked(session):
    """BOOKING_RESCHEDULE_REQUESTED means a time exists and someone wants a
    different one — an active negotiation, not an abandoned job."""
    client = make_client(session)
    cancelled_job(session, client, status=BOOKING_RESCHEDULE_REQUESTED)

    assert recovery_service.enroll_cancelled_jobs(session) == []


def test_a_business_without_retention_manager_never_enrolls(session):
    """Same deployment gate every other auto-enrollment uses: the Employee row
    is the consent record. A business that never hired this must not have its
    customers texted."""
    client = make_client(session, hire="")
    cancelled_job(session, client)

    assert recovery_service.enroll_cancelled_jobs(session) == []


def test_a_cancelled_job_with_no_number_is_skipped(session):
    client = make_client(session)
    job = cancelled_job(session, client)
    job.callback_number = None
    session.add(job)
    session.commit()

    assert recovery_service.enroll_cancelled_jobs(session) == []


def test_enrollment_never_crosses_businesses(session):
    """business_id is the security boundary everywhere in Roster."""
    a = make_client(session)
    b = make_client(session)
    cancelled_job(session, a)

    enrolled = recovery_service.enroll_cancelled_jobs(session)

    assert len(enrolled) == 1
    assert enrolled[0].business_id == a.id
    assert enrolled[0].business_id != b.id
