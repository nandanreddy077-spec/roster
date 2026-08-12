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

from db_models import Business, DispatchPlan, Job, JobQualification
from dispatcher_engine import plan
from employee_outcome import report_employee_blocked
from runner import is_active
from sqlmodel import Session, select


def recommend_dispatch_for_business(session: Session, business: Business) -> List[DispatchPlan]:
    """Plan every Job belonging to ONE business that has a JobQualification
    but no DispatchPlan yet. Idempotent — safe to call every tick, gated by
    source_job_id the same way qualify_new_jobs/enroll_completed_estimates
    gate their own work.

    Called only via recommend_dispatch below (runner.dispatch_tick) — never
    call this directly for a business you haven't already confirmed has
    dispatcher deployed."""
    planned: List[DispatchPlan] = []
    # A DEPENDENCY precondition, not a configuration one (Milestone B).
    # Dispatcher only ever plans jobs Lead Qualifier has already qualified, so
    # a business that hired Dispatcher alone gets a permanent, silent no-op.
    #
    # Deliberately NOT "is lead_qualifier deployed?" — that check is stricter
    # than reality and reports a healthy employee as broken. Qualifications
    # outlive the employee that wrote them, so a business whose jobs are all
    # already qualified is working fine even with Lead Qualifier fired. The
    # honest condition is the one the owner actually cares about: **is work
    # stranded** — jobs this employee can never plan because nothing will ever
    # qualify them. Caught by the existing dispatcher tests, which qualify
    # their jobs by hand and correctly expect plans to come out.
    stranded = 0
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
            stranded += 1
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

    # Reported after the loop, not before it: only now do we know whether any
    # work is genuinely stuck. One report per business per tick (and deduped
    # to one alert ever, per cause, by report_employee_blocked) — never one
    # per stranded job, which would be the same fact many times.
    if stranded and not is_active(session, business, "lead_qualifier"):
        report_employee_blocked(
            session,
            business,
            "dispatcher",
            "lead_qualifier_not_deployed",
            f"Dispatcher can't plan {stranded} job(s) because Lead Qualifier isn't hired",
        )

    return planned


def recommend_dispatch(session: Session) -> List[DispatchPlan]:
    """Entry point called by recovery_tick.py. Dispatches per-business via
    runner.dispatch_tick, so only businesses with dispatcher deployed are
    ever processed — see runner.py for why this is the single path, not a
    convention each employee remembers on its own."""
    from runner import dispatch_tick

    return dispatch_tick(session, "dispatcher", recommend_dispatch_for_business)
