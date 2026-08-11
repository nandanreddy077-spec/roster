"""Lead Qualifier's classification logic — pure functions, no DB, no LLM.
Every case is a plain input/output table: no StubAgent, nothing to
monkeypatch, since there is no AgentEngine dependency at all.
"""

from db_models import Customer, Job
from lead_qualifier_engine import (
    classify_financing_candidate,
    classify_job_type,
    classify_membership_candidate,
    classify_possible_spam,
    classify_priority,
    qualify,
)
from lead_qualifier_rules import (
    REASON_FINANCING_KEYWORD_MATCH,
    REASON_FINANCING_NO_SIGNAL,
    REASON_FINANCING_REPLACEMENT_JOB_TYPE,
    REASON_JOB_TYPE_ESTIMATE_DEFAULT,
    REASON_JOB_TYPE_MAINTENANCE_KEYWORD,
    REASON_JOB_TYPE_REPAIR_DEFAULT,
    REASON_JOB_TYPE_REPLACEMENT_KEYWORD,
    REASON_MEMBERSHIP_ELIGIBLE_NO_PLAN,
    REASON_MEMBERSHIP_EMERGENCY_EXCLUDED,
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
)


def _job(**overrides) -> Job:
    defaults = dict(
        business_id=1,
        service_type="AC not cooling",
        urgency="routine",
        callback_number="+15551234567",
        is_estimate=False,
        notes=None,
    )
    defaults.update(overrides)
    return Job(**defaults)


# ---- job_type -----------------------------------------------------------


def test_job_type_estimate_without_replacement_language_is_estimate():
    job = _job(is_estimate=True, service_type="how much for a price check")
    job_type, reason = classify_job_type(job)
    assert job_type == "estimate"
    assert reason == REASON_JOB_TYPE_ESTIMATE_DEFAULT


def test_job_type_estimate_with_replacement_language_is_replacement():
    job = _job(is_estimate=True, service_type="want to replace the whole unit")
    job_type, reason = classify_job_type(job)
    assert job_type == "replacement"
    assert reason == REASON_JOB_TYPE_REPLACEMENT_KEYWORD


def test_job_type_replacement_keyword_can_come_from_notes():
    job = _job(is_estimate=True, service_type="AC", notes="wants a new system installed")
    job_type, reason = classify_job_type(job)
    assert job_type == "replacement"
    assert reason == REASON_JOB_TYPE_REPLACEMENT_KEYWORD


def test_job_type_not_estimate_without_maintenance_language_is_repair():
    job = _job(is_estimate=False, service_type="AC not cooling")
    job_type, reason = classify_job_type(job)
    assert job_type == "repair"
    assert reason == REASON_JOB_TYPE_REPAIR_DEFAULT


def test_job_type_not_estimate_with_maintenance_language_is_maintenance():
    job = _job(is_estimate=False, service_type="annual tune-up")
    job_type, reason = classify_job_type(job)
    assert job_type == "maintenance"
    assert reason == REASON_JOB_TYPE_MAINTENANCE_KEYWORD


# ---- financing_candidate --------------------------------------------------


def test_financing_candidate_true_for_replacement_job_type():
    job = _job(service_type="AC")
    candidate, reason = classify_financing_candidate(job, "replacement")
    assert candidate is True
    assert reason == REASON_FINANCING_REPLACEMENT_JOB_TYPE


def test_financing_candidate_true_for_financing_keyword_on_repair():
    job = _job(service_type="AC not cooling", notes="asked if we offer a payment plan")
    candidate, reason = classify_financing_candidate(job, "repair")
    assert candidate is True
    assert reason == REASON_FINANCING_KEYWORD_MATCH


def test_financing_candidate_false_without_signal():
    job = _job(service_type="AC not cooling")
    candidate, reason = classify_financing_candidate(job, "repair")
    assert candidate is False
    assert reason == REASON_FINANCING_NO_SIGNAL


# ---- membership_candidate --------------------------------------------------


def test_membership_candidate_true_when_no_plan_and_repair():
    candidate, reason = classify_membership_candidate(_job(), "repair", None)
    assert candidate is True
    assert reason == REASON_MEMBERSHIP_ELIGIBLE_NO_PLAN


def test_membership_candidate_true_when_no_plan_and_maintenance():
    candidate, reason = classify_membership_candidate(_job(), "maintenance", None)
    assert candidate is True
    assert reason == REASON_MEMBERSHIP_ELIGIBLE_NO_PLAN


def test_membership_candidate_false_when_already_on_a_plan():
    customer = Customer(business_id=1, phone="+1", plan_notes="Quarterly plan")
    candidate, reason = classify_membership_candidate(_job(), "repair", customer)
    assert candidate is False
    assert reason == REASON_MEMBERSHIP_HAS_PLAN


def test_membership_candidate_false_for_estimate_job_type():
    candidate, reason = classify_membership_candidate(_job(), "estimate", None)
    assert candidate is False
    assert reason == REASON_MEMBERSHIP_JOB_TYPE_EXCLUDED


def test_membership_candidate_false_for_replacement_job_type():
    candidate, reason = classify_membership_candidate(_job(), "replacement", None)
    assert candidate is False
    assert reason == REASON_MEMBERSHIP_JOB_TYPE_EXCLUDED


# ---- priority ---------------------------------------------------------------


def test_priority_high_for_emergency_urgency():
    job = _job(urgency="emergency")
    priority, reason = classify_priority(job, "repair", False)
    assert priority == "high"
    assert reason == REASON_PRIORITY_URGENCY


def test_priority_high_for_same_day_urgency():
    job = _job(urgency="same_day")
    priority, reason = classify_priority(job, "repair", False)
    assert priority == "high"
    assert reason == REASON_PRIORITY_URGENCY


def test_priority_high_for_replacement_job_type_even_if_routine():
    job = _job(urgency="routine")
    priority, reason = classify_priority(job, "replacement", False)
    assert priority == "high"
    assert reason == REASON_PRIORITY_JOB_TYPE_REPLACEMENT


def test_priority_high_for_financing_candidate_even_if_routine_repair():
    job = _job(urgency="routine")
    priority, reason = classify_priority(job, "repair", True)
    assert priority == "high"
    assert reason == REASON_PRIORITY_FINANCING_CANDIDATE


def test_priority_normal_with_no_signal():
    job = _job(urgency="routine")
    priority, reason = classify_priority(job, "repair", False)
    assert priority == "normal"
    assert reason == REASON_PRIORITY_NORMAL_NO_SIGNAL


# ---- possible_spam ------------------------------------------------------


def test_possible_spam_true_for_missing_callback_number():
    job = _job(callback_number=None)
    spam, reason = classify_possible_spam(job)
    assert spam is True
    assert reason == REASON_SPAM_INVALID_CALLBACK


def test_possible_spam_true_for_malformed_callback_number():
    job = _job(callback_number="abc")
    spam, reason = classify_possible_spam(job)
    assert spam is True
    assert reason == REASON_SPAM_INVALID_CALLBACK


def test_possible_spam_true_for_blank_service_type():
    job = _job(service_type="   ")
    spam, reason = classify_possible_spam(job)
    assert spam is True
    assert reason == REASON_SPAM_BLANK_SERVICE_TYPE


def test_possible_spam_true_for_solicitation_keyword():
    job = _job(service_type="want to offer you SEO services")
    spam, reason = classify_possible_spam(job)
    assert spam is True
    assert reason == REASON_SPAM_KEYWORD_MATCH


def test_possible_spam_false_for_a_clean_job():
    job = _job()
    spam, reason = classify_possible_spam(job)
    assert spam is False
    assert reason == REASON_SPAM_CLEAN_NO_SIGNAL


# ---- qualify (the orchestrator) --------------------------------------------


def test_qualify_combines_every_axis_and_joins_reasoning():
    job = _job(is_estimate=True, service_type="want to replace the whole unit", urgency="routine")
    result = qualify(job, None)

    assert result["job_type"] == "replacement"
    assert result["financing_candidate"] is True
    assert result["membership_candidate"] is False
    assert result["priority"] == "high"
    assert result["possible_spam"] is False
    codes = result["reasoning"].split(",")
    assert REASON_JOB_TYPE_REPLACEMENT_KEYWORD in codes
    assert REASON_FINANCING_REPLACEMENT_JOB_TYPE in codes
    assert REASON_MEMBERSHIP_JOB_TYPE_EXCLUDED in codes
    assert REASON_PRIORITY_JOB_TYPE_REPLACEMENT in codes
    assert REASON_SPAM_CLEAN_NO_SIGNAL in codes


def test_qualify_reasoning_is_structured_codes_not_english_prose():
    """The founder explicitly asked for rule IDs, not sentences — this pins
    that down so a future edit can't silently regress into prose."""
    job = _job()
    result = qualify(job, None)
    for code in result["reasoning"].split(","):
        assert " " not in code, f"reasoning code {code!r} looks like a sentence, not an enum"
        assert code == code.upper()


def test_no_membership_pitch_off_the_back_of_an_emergency():
    """An emergency repair is still a "repair", so the job-type check passed it
    and Membership Agent texted a $19/mo plan offer to a customer whose garage
    had flooded the day before. Found in the 2026-08-10 end-to-end run."""
    candidate, reason = classify_membership_candidate(
        _job(service_type="water heater burst, flooding garage", urgency="emergency"),
        "repair",
        None,
    )
    assert candidate is False
    assert reason == REASON_MEMBERSHIP_EMERGENCY_EXCLUDED


def test_a_same_day_job_is_still_worth_a_membership_offer():
    """Only a genuine emergency is excluded — narrowing this to every urgent
    job would quietly delete most of Membership Agent's pipeline."""
    candidate, _ = classify_membership_candidate(
        _job(urgency="same_day"),
        "repair",
        None,
    )
    assert candidate is True
