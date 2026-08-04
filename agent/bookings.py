"""The single write path for booking a job — idempotent by design.

Every retry vector in the system (Twilio redelivering an SMS, xAI/Svix
redelivering a call webhook, the model re-calling log_job with new details
mid-conversation — which its own description invites) eventually lands here,
so this is where duplication is killed: one OPEN job per (business, thread,
service_type) inside the dedup window. A re-book of the same service on the
same thread MERGES new details into the existing job instead of inserting a
second row; a different service, a completed job, or an old job books fresh.
"""
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from sqlmodel import Session, select

from db_models import Business, Job
from eventbus import bus
from events import JOB_BOOKED, DomainEvent

# A same-thread re-book of the same service within this window is treated as
# the same job (details merged), not a new booking. Long enough to cover any
# single conversation incl. voice retries; short enough that a genuine repeat
# customer next week books cleanly.
BOOKING_DEDUP_WINDOW_HOURS = 24


def _publish_job_booked(session: Session, job: Job, customer_id: Optional[int]) -> None:
    """Record a NEW booking on the event stream. Best-effort, and deliberately
    subordinate to the booking itself: the job has already committed by the time
    this runs, and nothing here may undo it or raise into the caller. A lost
    event is a gap in history; a lost booking is a lost customer.

    Only reached on the create path — a detail-merge is not a new booking and
    must not emit a second event (the whole point of book_job's upsert). The
    dedup_key makes that structural too, so even a mistaken second call is
    dropped rather than duplicating history.

    ponytail: eventbus dispatches subscribers synchronously, in-process, so a
    slow handler would run inside the booking path. Safe today — there are zero
    subscribers. Move dispatch off-thread if one ever appears.
    """
    try:
        bus.publish(session, DomainEvent(
            type=JOB_BOOKED,
            business_id=job.business_id,
            customer_id=customer_id,
            payload={
                "job_id": job.id,
                "service_type": job.service_type,
                "urgency": job.urgency,
            },
            dedup_key=f"job.booked:{job.id}",
        ))
    except Exception as e:
        # Loud, like notifications.record_owner_notification: a booking that
        # never reached the event stream is invisible everywhere else.
        print(f"[events] failed to publish job.booked for job {job.id}: {e}", file=sys.stderr)


def book_job(
    session: Session,
    business: Business,
    thread: str,
    caller_number: str,
    args: Dict[str, Any],
    customer_id: Optional[int] = None,
) -> Tuple[Job, bool]:
    """Upsert a booking. Returns (job, created) — callers must only fire
    owner notifications when created is True, so a detail-merge never
    re-texts the owner about a job they already know about."""
    service_type = (args.get("service_type") or "").strip()
    since = datetime.utcnow() - timedelta(hours=BOOKING_DEDUP_WINDOW_HOURS)

    open_jobs = session.exec(
        select(Job).where(
            Job.business_id == business.id,
            Job.customer_phone == thread,
            Job.completed_at.is_(None),
            Job.created_at >= since,
        )
    ).all()
    match = next(
        (j for j in open_jobs if j.service_type.strip().lower() == service_type.lower()),
        None,
    )

    if match is not None:
        for field in ("customer_name", "address", "notes", "callback_number", "preferred_window"):
            value = args.get(field)
            if value:
                setattr(match, field, value)
        if args.get("urgency"):
            match.urgency = args["urgency"]
        if args.get("is_estimate"):
            match.is_estimate = True
        if customer_id is not None and match.customer_id is None:
            match.customer_id = customer_id
        session.add(match)
        session.commit()
        session.refresh(match)
        return match, False

    job = Job(
        business_id=business.id,
        customer_id=customer_id,
        customer_phone=thread,
        customer_name=args.get("customer_name"),
        service_type=service_type,
        urgency=args.get("urgency") or "routine",
        address=args.get("address"),
        callback_number=args.get("callback_number") or caller_number,
        notes=args.get("notes"),
        preferred_window=args.get("preferred_window"),
        is_estimate=bool(args.get("is_estimate")),
    )
    session.add(job)
    session.commit()
    # Strictly after the commit above: the job is already durable, so a failed
    # or deduped publish can only roll back the event, never the booking.
    _publish_job_booked(session, job, customer_id)
    # Refresh AFTER publishing, not before. publish() commits, and a commit
    # expires every instance in the session — so refreshing first would leave
    # callers holding a Job whose fields raise DetachedInstanceError once the
    # session closes. This is the last statement that touches `job` for exactly
    # that reason; don't add anything that commits between here and the return.
    session.refresh(job)
    return job, True


ESCALATION_SERVICE_TYPE = "Escalated call"

# Why an escalation needs a time window at all, when a voice call never did:
# a voice thread is `xai-voice:{call_id}` — unique per call, so "one escalation
# per thread" is already scoped to one conversation and can never recur. The
# SMS path threads on the customer's PHONE NUMBER, which is forever. Without a
# window, the first emergency that number ever reported would permanently
# suppress the page for every later one — a silently-dropped gas leak months
# later. Same window as BOOKING_DEDUP_WINDOW_HOURS above, for the same reason:
# long enough to cover one conversation's retries, short enough that a genuine
# new emergency pages again.
ESCALATION_DEDUP_WINDOW_HOURS = 24


def record_escalation(
    session: Session,
    business: Business,
    thread: str,
    caller_number: str,
    reason: str,
    customer_id: Optional[int] = None,
) -> Tuple[Job, bool]:
    """Upsert an emergency-escalation Job — the same idempotency guarantee
    book_job gives log_job, for alert_owner. One thread gets at most one
    escalation Job inside the dedup window; a repeat alert_owner call in the
    same conversation reuses it instead of creating a second one, while a new
    emergency from the same number after the window pages the owner again.

    Returns (job, should_notify). should_notify is True until the owner has
    actually been successfully paged for THIS job (job.owner_alerted_at is
    set by the caller only once notify_owner_of_escalation succeeds) — so a
    retry after a failed page still fires, and only a repeat call after a
    SUCCESSFUL page is deduped.
    """
    since = datetime.utcnow() - timedelta(hours=ESCALATION_DEDUP_WINDOW_HOURS)
    existing = session.exec(
        select(Job)
        .where(
            Job.business_id == business.id,
            Job.customer_phone == thread,
            Job.service_type == ESCALATION_SERVICE_TYPE,
            Job.created_at >= since,
        )
        .order_by(Job.id.desc())  # newest in-window escalation, deterministically
    ).first()
    if existing is not None:
        if customer_id is not None and existing.customer_id is None:
            existing.customer_id = customer_id
            session.add(existing)
            session.commit()
            session.refresh(existing)
        return existing, existing.owner_alerted_at is None

    job = Job(
        business_id=business.id,
        customer_id=customer_id,
        customer_phone=thread,
        service_type=ESCALATION_SERVICE_TYPE,
        urgency="emergency",
        callback_number=caller_number,
        notes=reason,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job, True
