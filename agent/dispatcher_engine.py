"""Dispatcher's classification logic — deterministic, no AgentEngine, no
prompts, no tool schemas, no trial_cap. Every output is a lookup over
Job.urgency and JobQualification's already-structured fields; this module
never re-derives what Frontdesk or Lead Qualifier already decided, and it
never reads free text at all (2026-07-30 design review).

Reason codes live in dispatcher_rules.py — this module contains only the
classification functions.
"""
from typing import Tuple

from db_models import Job, JobQualification
from dispatcher_rules import (
    REASON_PRIORITY_NORMAL_NO_SIGNAL,
    REASON_PRIORITY_QUALIFIER_HIGH_BUMP,
    REASON_PRIORITY_URGENCY_EMERGENCY,
    REASON_PRIORITY_URGENCY_SAME_DAY,
    REASON_REVIEW_EMERGENCY_ALERT_UNCONFIRMED,
    REASON_REVIEW_MISSING_ADDRESS,
    REASON_REVIEW_MISSING_CALLBACK,
    REASON_REVIEW_NO_SIGNAL,
    REASON_REVIEW_POSSIBLE_SPAM,
    REASON_WINDOW_EMERGENCY_IMMEDIATE,
    REASON_WINDOW_FLEXIBLE_JOB_TYPE,
    REASON_WINDOW_REPAIR_TOMORROW,
    REASON_WINDOW_SAME_DAY_TODAY,
)

URGENT_DISPATCH_PRIORITIES = ("emergency", "same_day")


def classify_dispatch_priority(job: Job, qualification: JobQualification) -> Tuple[str, str]:
    if job.urgency == "emergency":
        return "emergency", REASON_PRIORITY_URGENCY_EMERGENCY
    if job.urgency == "same_day":
        return "same_day", REASON_PRIORITY_URGENCY_SAME_DAY
    if qualification.priority == "high":
        return "same_day", REASON_PRIORITY_QUALIFIER_HIGH_BUMP
    return "normal", REASON_PRIORITY_NORMAL_NO_SIGNAL


def classify_scheduling_window(job_type: str, dispatch_priority: str) -> Tuple[str, str]:
    if dispatch_priority == "emergency":
        return "immediate", REASON_WINDOW_EMERGENCY_IMMEDIATE
    if dispatch_priority == "same_day":
        return "today", REASON_WINDOW_SAME_DAY_TODAY
    if job_type == "repair":
        return "tomorrow", REASON_WINDOW_REPAIR_TOMORROW
    return "flexible", REASON_WINDOW_FLEXIBLE_JOB_TYPE


def classify_requires_dispatch_review(
    job: Job, qualification: JobQualification, dispatch_priority: str
) -> Tuple[bool, str]:
    if qualification.possible_spam:
        return True, REASON_REVIEW_POSSIBLE_SPAM
    if not (job.callback_number or "").strip():
        return True, REASON_REVIEW_MISSING_CALLBACK
    if not (job.address or "").strip() and dispatch_priority in URGENT_DISPATCH_PRIORITIES:
        return True, REASON_REVIEW_MISSING_ADDRESS
    if job.urgency == "emergency" and job.owner_alerted_at is None:
        return True, REASON_REVIEW_EMERGENCY_ALERT_UNCONFIRMED
    return False, REASON_REVIEW_NO_SIGNAL


def plan(job: Job, qualification: JobQualification) -> dict:
    """Run every axis and combine into one DispatchPlan-shaped dict.
    dispatch_reason is a comma-joined list of structured rule-code enums
    (never English prose) — one per axis, in a fixed order."""
    dispatch_priority, priority_reason = classify_dispatch_priority(job, qualification)
    scheduling_window, window_reason = classify_scheduling_window(qualification.job_type, dispatch_priority)
    requires_review, review_reason = classify_requires_dispatch_review(job, qualification, dispatch_priority)

    return {
        "dispatch_priority": dispatch_priority,
        "scheduling_window": scheduling_window,
        "requires_dispatch_review": requires_review,
        "dispatch_reason": ",".join([priority_reason, window_reason, review_reason]),
    }
