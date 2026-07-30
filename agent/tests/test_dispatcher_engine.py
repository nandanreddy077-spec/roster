"""Dispatcher's classification logic — pure functions, no DB, no LLM. Every
output is a lookup over Job.urgency and JobQualification's already-structured
fields; nothing here reads free text.
"""
from db_models import Job, JobQualification
from dispatcher_engine import (
    classify_dispatch_priority,
    classify_requires_dispatch_review,
    classify_scheduling_window,
    plan,
)
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


def _job(**overrides) -> Job:
    defaults = dict(
        business_id=1, service_type="AC not cooling", urgency="routine",
        callback_number="+15551234567", address="123 Main St",
    )
    defaults.update(overrides)
    return Job(**defaults)


def _qualification(**overrides) -> JobQualification:
    defaults = dict(
        business_id=1, source_job_id=1, job_type="repair",
        financing_candidate=False, membership_candidate=False,
        priority="normal", possible_spam=False, reasoning="X",
    )
    defaults.update(overrides)
    return JobQualification(**defaults)


# ---- dispatch_priority ------------------------------------------------------

def test_dispatch_priority_emergency_urgency():
    job = _job(urgency="emergency")
    priority, reason = classify_dispatch_priority(job, _qualification())
    assert priority == "emergency"
    assert reason == REASON_PRIORITY_URGENCY_EMERGENCY


def test_dispatch_priority_same_day_urgency():
    job = _job(urgency="same_day")
    priority, reason = classify_dispatch_priority(job, _qualification())
    assert priority == "same_day"
    assert reason == REASON_PRIORITY_URGENCY_SAME_DAY


def test_dispatch_priority_bumped_to_same_day_for_high_qualifier_priority():
    job = _job(urgency="routine")
    priority, reason = classify_dispatch_priority(job, _qualification(priority="high"))
    assert priority == "same_day"
    assert reason == REASON_PRIORITY_QUALIFIER_HIGH_BUMP


def test_dispatch_priority_normal_with_no_signal():
    job = _job(urgency="routine")
    priority, reason = classify_dispatch_priority(job, _qualification(priority="normal"))
    assert priority == "normal"
    assert reason == REASON_PRIORITY_NORMAL_NO_SIGNAL


# ---- scheduling_window --------------------------------------------------

def test_scheduling_window_immediate_for_emergency_priority():
    window, reason = classify_scheduling_window("repair", "emergency")
    assert window == "immediate"
    assert reason == REASON_WINDOW_EMERGENCY_IMMEDIATE


def test_scheduling_window_today_for_same_day_priority():
    window, reason = classify_scheduling_window("repair", "same_day")
    assert window == "today"
    assert reason == REASON_WINDOW_SAME_DAY_TODAY


def test_scheduling_window_tomorrow_for_normal_priority_repair():
    window, reason = classify_scheduling_window("repair", "normal")
    assert window == "tomorrow"
    assert reason == REASON_WINDOW_REPAIR_TOMORROW


def test_scheduling_window_flexible_for_normal_priority_maintenance():
    window, reason = classify_scheduling_window("maintenance", "normal")
    assert window == "flexible"
    assert reason == REASON_WINDOW_FLEXIBLE_JOB_TYPE


def test_scheduling_window_flexible_for_normal_priority_estimate_or_replacement():
    for job_type in ("estimate", "replacement"):
        window, reason = classify_scheduling_window(job_type, "normal")
        assert window == "flexible"
        assert reason == REASON_WINDOW_FLEXIBLE_JOB_TYPE


# ---- requires_dispatch_review -----------------------------------------------

def test_requires_dispatch_review_true_for_possible_spam():
    job = _job()
    flagged, reason = classify_requires_dispatch_review(job, _qualification(possible_spam=True), "normal")
    assert flagged is True
    assert reason == REASON_REVIEW_POSSIBLE_SPAM


def test_requires_dispatch_review_true_for_missing_callback_number():
    job = _job(callback_number=None)
    flagged, reason = classify_requires_dispatch_review(job, _qualification(), "normal")
    assert flagged is True
    assert reason == REASON_REVIEW_MISSING_CALLBACK


def test_requires_dispatch_review_true_for_missing_address_when_urgent():
    job = _job(address=None)
    flagged, reason = classify_requires_dispatch_review(job, _qualification(), "emergency")
    assert flagged is True
    assert reason == REASON_REVIEW_MISSING_ADDRESS


def test_requires_dispatch_review_false_for_missing_address_when_not_urgent():
    """A missing address on a flexible/normal-priority job isn't urgent
    enough to need a human's attention before it's even scheduled."""
    job = _job(address=None)
    flagged, reason = classify_requires_dispatch_review(job, _qualification(), "normal")
    assert flagged is False
    assert reason == REASON_REVIEW_NO_SIGNAL


def test_requires_dispatch_review_true_for_unconfirmed_emergency_alert():
    job = _job(urgency="emergency", owner_alerted_at=None)
    flagged, reason = classify_requires_dispatch_review(job, _qualification(), "emergency")
    assert flagged is True
    assert reason == REASON_REVIEW_EMERGENCY_ALERT_UNCONFIRMED


def test_requires_dispatch_review_false_for_a_clean_job():
    job = _job()
    flagged, reason = classify_requires_dispatch_review(job, _qualification(), "normal")
    assert flagged is False
    assert reason == REASON_REVIEW_NO_SIGNAL


# ---- plan (the orchestrator) ------------------------------------------------

def test_plan_combines_every_axis_and_joins_reasoning():
    job = _job(urgency="emergency", owner_alerted_at=None)
    result = plan(job, _qualification(job_type="repair"))

    assert result["dispatch_priority"] == "emergency"
    assert result["scheduling_window"] == "immediate"
    assert result["requires_dispatch_review"] is True
    codes = result["dispatch_reason"].split(",")
    assert REASON_PRIORITY_URGENCY_EMERGENCY in codes
    assert REASON_WINDOW_EMERGENCY_IMMEDIATE in codes
    assert REASON_REVIEW_EMERGENCY_ALERT_UNCONFIRMED in codes


def test_plan_reasoning_is_structured_codes_not_english_prose():
    job = _job()
    result = plan(job, _qualification())
    for code in result["dispatch_reason"].split(","):
        assert " " not in code, f"dispatch_reason code {code!r} looks like a sentence, not an enum"
        assert code == code.upper()
