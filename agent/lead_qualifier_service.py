"""Lead Qualifier: deterministic enrichment of every Job into structured,
reusable qualification data (job_type, financing_candidate,
membership_candidate, priority, possible_spam). Tick-based, not a hook off
bookings.book_job — with zero LLM cost/latency there's no need for
synchronous wiring, and this way the frozen booking path stays completely
untouched (2026-07-30 design review).

Dispatched through runner.dispatch_tick (2026-07-30, Critical Finding #2
fix): qualify_jobs_for_business never queries across businesses itself —
it's only ever called with a Business the dispatcher has already confirmed
has lead_qualifier deployed. There is no reachable path to an undeployed
business's jobs to forget to guard.
"""

from typing import List, Optional

from db_models import ORIGIN_ESCALATION, Business, Customer, Job, JobQualification
from lead_qualifier_engine import qualify
from sqlmodel import Session, select


def _find_customer(session: Session, job: Job) -> Optional[Customer]:
    if job.customer_id is not None:
        return session.get(Customer, job.customer_id)
    phone = job.customer_phone or job.callback_number
    if not phone:
        return None
    return session.exec(
        select(Customer).where(Customer.business_id == job.business_id, Customer.phone == phone)
    ).first()


def qualify_jobs_for_business(session: Session, business: Business) -> List[JobQualification]:
    """Qualify every Job belonging to ONE business with no JobQualification
    row yet. Idempotent — safe to call every tick, gated by source_job_id
    the same way enroll_completed_estimates gates its own enrollment. No
    time window: a job qualifies whenever it exists, including a historical
    backfill the first time this runs for a newly-deployed business.

    Called only via qualify_new_jobs below (runner.dispatch_tick) — never
    call this directly for a business you haven't already confirmed has
    lead_qualifier deployed."""
    qualified: List[JobQualification] = []
    # Escalation rows are alert bookkeeping, not leads. Qualifying them
    # produced a "high priority" classification for a job that doesn't exist,
    # which Dispatcher then planned and flagged for missing an address.
    # Filtering here is enough to stop both: dispatcher_service only plans
    # jobs that already have a qualification.
    jobs = session.exec(
        select(Job).where(Job.business_id == business.id, Job.origin != ORIGIN_ESCALATION)
    ).all()

    for job in jobs:
        already = session.exec(
            select(JobQualification).where(JobQualification.source_job_id == job.id)
        ).first()
        if already is not None:
            continue

        customer = _find_customer(session, job)
        result = qualify(job, customer)
        row = JobQualification(
            business_id=job.business_id,
            source_job_id=job.id,
            **result,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        qualified.append(row)

    return qualified


def qualify_new_jobs(session: Session) -> List[JobQualification]:
    """Entry point called by recovery_tick.py. Dispatches per-business via
    runner.dispatch_tick, so only businesses with lead_qualifier deployed
    are ever processed — see runner.py for why this is the single path,
    not a convention each employee remembers on its own."""
    from runner import dispatch_tick

    return dispatch_tick(session, "lead_qualifier", qualify_jobs_for_business)
