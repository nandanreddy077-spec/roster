"""Lead Qualifier: deterministic enrichment of every Job into structured,
reusable qualification data (job_type, financing_candidate,
membership_candidate, priority, possible_spam). Tick-based, not a hook off
bookings.book_job — with zero LLM cost/latency there's no need for
synchronous wiring, and this way the frozen booking path stays completely
untouched (2026-07-30 design review).
"""
from typing import List, Optional

from sqlmodel import Session, select

from db_models import Customer, Job, JobQualification
from lead_qualifier_engine import qualify


def _find_customer(session: Session, job: Job) -> Optional[Customer]:
    if job.customer_id is not None:
        return session.get(Customer, job.customer_id)
    phone = job.customer_phone or job.callback_number
    if not phone:
        return None
    return session.exec(
        select(Customer).where(Customer.business_id == job.business_id, Customer.phone == phone)
    ).first()


def qualify_new_jobs(session: Session) -> List[JobQualification]:
    """Qualify every Job with no JobQualification row yet. Idempotent — safe
    to call every tick, gated by source_job_id the same way
    enroll_completed_estimates gates its own enrollment. No time window: a
    job qualifies whenever it exists, including a historical backfill the
    first time this runs."""
    qualified: List[JobQualification] = []
    jobs = session.exec(select(Job)).all()

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
