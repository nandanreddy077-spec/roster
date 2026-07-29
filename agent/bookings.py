"""The single write path for booking a job — idempotent by design.

Every retry vector in the system (Twilio redelivering an SMS, xAI/Svix
redelivering a call webhook, the model re-calling log_job with new details
mid-conversation — which its own description invites) eventually lands here,
so this is where duplication is killed: one OPEN job per (business, thread,
service_type) inside the dedup window. A re-book of the same service on the
same thread MERGES new details into the existing job instead of inserting a
second row; a different service, a completed job, or an old job books fresh.
"""
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from sqlmodel import Session, select

from db_models import Business, Job

# A same-thread re-book of the same service within this window is treated as
# the same job (details merged), not a new booking. Long enough to cover any
# single conversation incl. voice retries; short enough that a genuine repeat
# customer next week books cleanly.
BOOKING_DEDUP_WINDOW_HOURS = 24


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
        for field in ("customer_name", "address", "notes", "callback_number"):
            value = args.get(field)
            if value:
                setattr(match, field, value)
        if args.get("urgency"):
            match.urgency = args["urgency"]
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
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job, True


ESCALATION_SERVICE_TYPE = "Escalated call"


def record_escalation(
    session: Session,
    business: Business,
    thread: str,
    caller_number: str,
    reason: str,
) -> Tuple[Job, bool]:
    """Upsert an emergency-escalation Job — the same idempotency guarantee
    book_job gives log_job, for alert_owner. One call thread gets at most one
    escalation Job; a repeat alert_owner call in the same conversation reuses
    it instead of creating a second one.

    Returns (job, should_notify). should_notify is True until the owner has
    actually been successfully paged for THIS job (job.owner_alerted_at is
    set by the caller only once notify_owner_of_escalation succeeds) — so a
    retry after a failed page still fires, and only a repeat call after a
    SUCCESSFUL page is deduped.
    """
    existing = session.exec(
        select(Job).where(
            Job.business_id == business.id,
            Job.customer_phone == thread,
            Job.service_type == ESCALATION_SERVICE_TYPE,
        )
    ).first()
    if existing is not None:
        return existing, existing.owner_alerted_at is None

    job = Job(
        business_id=business.id,
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
