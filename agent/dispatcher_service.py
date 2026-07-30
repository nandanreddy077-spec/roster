"""Dispatcher: deterministic enrichment of every qualified Job into a
scheduling recommendation (dispatch_priority, scheduling_window,
requires_dispatch_review). Tick-based, positioned after Lead Qualifier's own
qualify_new_jobs — Dispatcher depends on JobQualification existing and skips
anything not yet qualified rather than guessing, picking it up on a later
tick instead.

Dispatched through runner.dispatch_tick (2026-07-30, Critical Finding #2
fix): recommend_dispatch_for_business never queries across businesses
itself — it's only ever called with a Business the dispatcher has already
confirmed has dispatcher deployed. There is no reachable path to an
undeployed business's jobs to forget to guard.
"""
from typing import List

from sqlmodel import Session, select

from db_models import Business, DispatchPlan, Job, JobQualification
from dispatcher_engine import plan


def recommend_dispatch_for_business(session: Session, business: Business) -> List[DispatchPlan]:
    """Plan every Job belonging to ONE business that has a JobQualification
    but no DispatchPlan yet. Idempotent — safe to call every tick, gated by
    source_job_id the same way qualify_new_jobs/enroll_completed_estimates
    gate their own work.

    Called only via recommend_dispatch below (runner.dispatch_tick) — never
    call this directly for a business you haven't already confirmed has
    dispatcher deployed."""
    planned: List[DispatchPlan] = []
    jobs = session.exec(select(Job).where(Job.business_id == business.id)).all()

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


def recommend_dispatch(session: Session) -> List[DispatchPlan]:
    """Entry point called by recovery_tick.py. Dispatches per-business via
    runner.dispatch_tick, so only businesses with dispatcher deployed are
    ever processed — see runner.py for why this is the single path, not a
    convention each employee remembers on its own."""
    from runner import dispatch_tick

    return dispatch_tick(session, "dispatcher", recommend_dispatch_for_business)
