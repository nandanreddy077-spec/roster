"""Dispatcher: deterministic enrichment of every qualified Job into a
scheduling recommendation (dispatch_priority, scheduling_window,
requires_dispatch_review). Tick-based, positioned after Lead Qualifier's own
qualify_new_jobs — Dispatcher depends on JobQualification existing and skips
anything not yet qualified rather than guessing, picking it up on a later
tick instead.
"""
from typing import List

from sqlmodel import Session, select

from db_models import DispatchPlan, Job, JobQualification
from dispatcher_engine import plan


def recommend_dispatch(session: Session) -> List[DispatchPlan]:
    """Plan every Job that has a JobQualification but no DispatchPlan yet.
    Idempotent — safe to call every tick, gated by source_job_id the same
    way qualify_new_jobs/enroll_completed_estimates gate their own work."""
    planned: List[DispatchPlan] = []
    jobs = session.exec(select(Job)).all()

    for job in jobs:
        already = session.exec(
            select(DispatchPlan).where(DispatchPlan.source_job_id == job.id)
        ).first()
        if already is not None:
            continue

        qualification = session.exec(
            select(JobQualification).where(JobQualification.source_job_id == job.id)
        ).first()
        if qualification is None:
            continue  # not yet qualified — pick up on a later tick

        result = plan(job, qualification)
        row = DispatchPlan(
            business_id=job.business_id,
            source_job_id=job.id,
            **result,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        planned.append(row)

    return planned
