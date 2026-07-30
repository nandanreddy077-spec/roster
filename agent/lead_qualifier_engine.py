"""Lead Qualifier's classification logic — deterministic, no AgentEngine, no
prompts, no tool schemas, no trial_cap. Every output is derived from data
Frontdesk/the founder already captured (Job.urgency, Job.is_estimate,
Job.service_type, Job.notes, Customer.plan_notes); this module never
re-decides what Frontdesk already decided, it only adds the axes nothing
else captures (2026-07-30 design review).

Keyword tables and reason codes live in lead_qualifier_rules.py — this
module contains only the classification functions.
"""
import re
from typing import Optional, Tuple

from db_models import Customer, Job
from lead_qualifier_rules import (
    FINANCING_KEYWORDS,
    MAINTENANCE_KEYWORDS,
    REASON_FINANCING_KEYWORD_MATCH,
    REASON_FINANCING_NO_SIGNAL,
    REASON_FINANCING_REPLACEMENT_JOB_TYPE,
    REASON_JOB_TYPE_ESTIMATE_DEFAULT,
    REASON_JOB_TYPE_MAINTENANCE_KEYWORD,
    REASON_JOB_TYPE_REPAIR_DEFAULT,
    REASON_JOB_TYPE_REPLACEMENT_KEYWORD,
    REASON_MEMBERSHIP_ELIGIBLE_NO_PLAN,
    REASON_MEMBERSHIP_HAS_PLAN,
    REASON_MEMBERSHIP_JOB_TYPE_EXCLUDED,
    REASON_PRIORITY_FINANCING_CANDIDATE,
    REASON_PRIORITY_JOB_TYPE_REPLACEMENT,
    REASON_PRIORITY_NORMAL_NO_SIGNAL,
    REASON_PRIORITY_URGENCY,
    REASON_SPAM_BLANK_SERVICE_TYPE,
    REASON_SPAM_CLEAN_NO_SIGNAL,
    REASON_SPAM_INVALID_CALLBACK,
    REASON_SPAM_KEYWORD_MATCH,
    REPLACEMENT_KEYWORDS,
    SPAM_KEYWORDS,
)

MEMBERSHIP_ELIGIBLE_JOB_TYPES = ("repair", "maintenance")
HIGH_PRIORITY_URGENCIES = ("emergency", "same_day")


def _job_text(job: Job) -> str:
    return f"{job.service_type or ''} {job.notes or ''}".lower()


def _matches(text: str, keywords) -> bool:
    return any(kw in text for kw in keywords)


def _looks_like_phone(number: Optional[str]) -> bool:
    if not number:
        return False
    digits = re.sub(r"\D", "", number)
    return 7 <= len(digits) <= 15


def classify_job_type(job: Job) -> Tuple[str, str]:
    text = _job_text(job)
    if job.is_estimate:
        if _matches(text, REPLACEMENT_KEYWORDS):
            return "replacement", REASON_JOB_TYPE_REPLACEMENT_KEYWORD
        return "estimate", REASON_JOB_TYPE_ESTIMATE_DEFAULT
    if _matches(text, MAINTENANCE_KEYWORDS):
        return "maintenance", REASON_JOB_TYPE_MAINTENANCE_KEYWORD
    return "repair", REASON_JOB_TYPE_REPAIR_DEFAULT


def classify_financing_candidate(job: Job, job_type: str) -> Tuple[bool, str]:
    if job_type == "replacement":
        return True, REASON_FINANCING_REPLACEMENT_JOB_TYPE
    if _matches(_job_text(job), FINANCING_KEYWORDS):
        return True, REASON_FINANCING_KEYWORD_MATCH
    return False, REASON_FINANCING_NO_SIGNAL


def classify_membership_candidate(job_type: str, customer: Optional[Customer]) -> Tuple[bool, str]:
    if customer and customer.plan_notes:
        return False, REASON_MEMBERSHIP_HAS_PLAN
    if job_type not in MEMBERSHIP_ELIGIBLE_JOB_TYPES:
        return False, REASON_MEMBERSHIP_JOB_TYPE_EXCLUDED
    return True, REASON_MEMBERSHIP_ELIGIBLE_NO_PLAN


def classify_priority(job: Job, job_type: str, financing_candidate: bool) -> Tuple[str, str]:
    if job.urgency in HIGH_PRIORITY_URGENCIES:
        return "high", REASON_PRIORITY_URGENCY
    if job_type == "replacement":
        return "high", REASON_PRIORITY_JOB_TYPE_REPLACEMENT
    if financing_candidate:
        return "high", REASON_PRIORITY_FINANCING_CANDIDATE
    return "normal", REASON_PRIORITY_NORMAL_NO_SIGNAL


def classify_possible_spam(job: Job) -> Tuple[bool, str]:
    if not _looks_like_phone(job.callback_number):
        return True, REASON_SPAM_INVALID_CALLBACK
    if not (job.service_type or "").strip():
        return True, REASON_SPAM_BLANK_SERVICE_TYPE
    if _matches(_job_text(job), SPAM_KEYWORDS):
        return True, REASON_SPAM_KEYWORD_MATCH
    return False, REASON_SPAM_CLEAN_NO_SIGNAL


def qualify(job: Job, customer: Optional[Customer]) -> dict:
    """Run every axis and combine into one JobQualification-shaped dict.
    `reasoning` is a comma-joined list of structured rule-code enums (never
    English prose) — one per axis, in a fixed order, so it's trivially
    parseable for analytics."""
    job_type, job_type_reason = classify_job_type(job)
    financing_candidate, financing_reason = classify_financing_candidate(job, job_type)
    membership_candidate, membership_reason = classify_membership_candidate(job_type, customer)
    priority, priority_reason = classify_priority(job, job_type, financing_candidate)
    possible_spam, spam_reason = classify_possible_spam(job)

    return {
        "job_type": job_type,
        "financing_candidate": financing_candidate,
        "membership_candidate": membership_candidate,
        "priority": priority,
        "possible_spam": possible_spam,
        "reasoning": ",".join([
            job_type_reason, financing_reason, membership_reason, priority_reason, spam_reason,
        ]),
    }
