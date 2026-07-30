"""EMPLOYEE_RECORDS — the employee-level twin of METRIC_RECORDS.

For every employee it declares which record types belong to them, which
metrics they expose, and how drill-down resolves to underlying rows. Employee
pages render entirely from this registry, so no role-specific logic ever lands
in a route or a template.

No record in the database carries an employee_id (the only such column is on
`event`, which nothing publishes in the live path), so attribution here is by
DECLARED convention rather than by a foreign key — which is exactly why it has
to be declared rather than implied."""
from datetime import datetime, timedelta

import metrics
from db_models import (
    Business, DispatchPlan, Job, JobQualification, OwnerNotification, RecoveryCampaign,
    RecoveryJob, ReviewReply,
)
from metrics import (
    CALLS_ANSWERED,
    EMERGENCY_DISPATCHES,
    EMPLOYEE_RECORDS,
    ESCALATIONS,
    FINANCING_CANDIDATES,
    HIGH_PRIORITY_LEADS,
    JOBS_BOOKED,
    JOBS_DISPATCHED,
    LEADS_QUALIFIED,
    MANUAL_REVIEW_FLAGGED,
    MEMBERSHIP_CANDIDATES,
    POSSIBLE_SPAM_FLAGGED,
    QUOTES_CHASED,
    REVIEW_FOLLOWUPS_SENT,
    REVIEW_NEGATIVE_REPLIES,
    REVIEW_REQUESTS_SENT,
    REVIEW_RESPONSES,
    REVIEWS_SELF_REPORTED,
    SAME_DAY_DISPATCHES,
    employee_activity,
    employee_outcomes,
)


def _business(session, email):
    b = Business(business_name="B", trade="hvac", email=email)
    session.add(b)
    session.commit()
    session.refresh(b)
    return b


# --- the registry's own invariants --------------------------------------------


def test_every_declared_source_names_the_rows_it_resolves_to():
    """The drill-down invariant, one level down: an employee's record source
    must say which rows it is, or the number it produces is untraceable."""
    for role_key, entry in EMPLOYEE_RECORDS.items():
        assert entry.sources, f"{role_key} declares no record sources"
        for source in entry.sources:
            assert source.records.strip(), f"{role_key}.{source.key} names no rows"
            assert callable(source.fetch), f"{role_key}.{source.key} cannot resolve its rows"


def test_every_metric_an_employee_exposes_is_a_declared_metric():
    for role_key, entry in EMPLOYEE_RECORDS.items():
        for source in entry.sources:
            if source.metric is not None:
                assert source.metric in metrics.METRIC_RECORDS, (
                    f"{role_key} exposes {source.metric!r}, which declares no records"
                )


def test_every_declared_employee_is_a_real_registry_employee():
    from employees import REGISTRY

    known = {e.key for e in REGISTRY}
    for role_key in EMPLOYEE_RECORDS:
        assert role_key in known, f"{role_key!r} is not an employee in the registry"


def test_reviews_declares_requests_sent_and_nothing_about_reviews_received():
    """Founder decision, 2026-07-29: review requests are a real record we now
    write. Reviews RECEIVED is unknowable without a Google/Yelp integration,
    and inventing it would be the dashboard's first fabricated number."""
    reviews = EMPLOYEE_RECORDS["reviews"]
    exposed = {s.metric for s in reviews.sources}

    assert REVIEW_REQUESTS_SENT in exposed
    assert not any("received" in (m or "") for m in exposed)


# --- frontdesk ----------------------------------------------------------------


def test_frontdesk_counts_booked_jobs_excluding_tests(session):
    b = _business(session, "er1@test.io")
    session.add(Job(business_id=b.id, customer_phone="+15125550100",
                    service_type="AC repair", urgency="routine"))
    session.add(Job(business_id=b.id, customer_phone="portal-test",
                    service_type="AC repair", urgency="routine"))
    session.commit()

    assert employee_outcomes(session, b.id, "frontdesk")[JOBS_BOOKED] == 1


def test_frontdesk_counts_each_voice_call_once(session):
    """A call is one conversation, however many turns it took."""
    from db_models import Message

    b = _business(session, "er2@test.io")
    for thread, turns in (("xai-voice:c1", 3), ("xai-voice:c2", 2)):
        for _ in range(turns):
            session.add(Message(business_id=b.id, customer_phone=thread,
                                role="assistant", content_json='"hi"'))
    session.add(Message(business_id=b.id, customer_phone="+15125550100",
                        role="user", content_json='"sms not a call"'))
    session.commit()

    assert employee_outcomes(session, b.id, "frontdesk")[CALLS_ANSWERED] == 2


def test_frontdesk_counts_escalations_it_raised(session):
    b = _business(session, "er3@test.io")
    session.add(OwnerNotification(business_id=b.id, kind="escalation",
                                  source="alert_owner", message="m", delivered=True))
    session.add(OwnerNotification(business_id=b.id, kind="call_dropped",
                                  source="call_dropped", message="m", delivered=True))
    session.add(OwnerNotification(business_id=b.id, kind="job_booked",
                                  source="sms_booking", message="m", delivered=True))
    session.commit()

    assert employee_outcomes(session, b.id, "frontdesk")[ESCALATIONS] == 2


# --- reviews ------------------------------------------------------------------


def test_reviews_counts_only_requests_actually_sent(session):
    """review_requested_at is set where the SMS is sent, so this counts sends
    rather than inferring them from completion."""
    b = _business(session, "er4@test.io")
    session.add(Job(business_id=b.id, customer_phone="+1", service_type="x",
                    urgency="routine", review_requested_at=datetime.utcnow()))
    session.add(Job(business_id=b.id, customer_phone="+2", service_type="x",
                    urgency="routine", completed_at=datetime.utcnow()))
    session.commit()

    assert employee_outcomes(session, b.id, "reviews")[REVIEW_REQUESTS_SENT] == 1


def test_reviews_counts_followups_responses_self_reported_and_negative(session):
    b = _business(session, "er4b@test.io")
    other = _business(session, "er4c@test.io")
    session.add(Job(business_id=b.id, customer_phone="+1", service_type="x",
                    urgency="routine", review_followup_sent_at=datetime.utcnow()))
    session.add(ReviewReply(business_id=b.id, source_job_id=1, customer_phone="+1",
                            outcome="left_review", raw_reply_text="left a review!"))
    session.add(ReviewReply(business_id=b.id, source_job_id=1, customer_phone="+2",
                            outcome="negative", raw_reply_text="not happy"))
    session.add(ReviewReply(business_id=b.id, source_job_id=1, customer_phone="+3",
                            outcome="neutral", raw_reply_text="ok"))
    # a reply on a DIFFERENT business must never bleed into b's counts
    session.add(ReviewReply(business_id=other.id, source_job_id=1, customer_phone="+4",
                            outcome="left_review", raw_reply_text="great job!"))
    session.commit()

    outcomes = employee_outcomes(session, b.id, "reviews")
    assert outcomes[REVIEW_FOLLOWUPS_SENT] == 1
    assert outcomes[REVIEW_RESPONSES] == 3
    assert outcomes[REVIEWS_SELF_REPORTED] == 1
    assert outcomes[REVIEW_NEGATIVE_REPLIES] == 1


# --- scoping, windows and drill-down ------------------------------------------


def test_outcomes_never_cross_businesses(session):
    a = _business(session, "er5a@test.io")
    b = _business(session, "er5b@test.io")
    session.add(Job(business_id=a.id, customer_phone="+1", service_type="x", urgency="routine"))
    session.commit()

    assert employee_outcomes(session, b.id, "frontdesk")[JOBS_BOOKED] == 0


def test_a_since_window_narrows_the_count(session):
    b = _business(session, "er6@test.io")
    session.add(Job(business_id=b.id, customer_phone="+1", service_type="x",
                    urgency="routine", created_at=datetime.utcnow() - timedelta(days=5)))
    session.add(Job(business_id=b.id, customer_phone="+2", service_type="x",
                    urgency="routine"))
    session.commit()

    since = datetime.utcnow() - timedelta(days=1)
    assert employee_outcomes(session, b.id, "frontdesk")[JOBS_BOOKED] == 2
    assert employee_outcomes(session, b.id, "frontdesk", since=since)[JOBS_BOOKED] == 1


def test_an_undeclared_role_reports_nothing_rather_than_zeros(session):
    """financing has no engine yet (employees.py: still `planned`), so it
    correctly has no EMPLOYEE_RECORDS entry — dispatcher used to be this
    example too, until Dispatcher PR #1 (2026-07-30) gave it one."""
    b = _business(session, "er7@test.io")

    assert employee_outcomes(session, b.id, "financing") == {}


def test_activity_resolves_to_real_rows_newest_first(session):
    """The drill-down itself: the same declaration that produces the number
    produces the rows behind it."""
    b = _business(session, "er8@test.io")
    older = Job(business_id=b.id, customer_phone="+1", service_type="older",
                urgency="routine", created_at=datetime.utcnow() - timedelta(hours=2))
    newer = Job(business_id=b.id, customer_phone="+2", service_type="newer",
                urgency="routine")
    session.add(older)
    session.add(newer)
    session.commit()

    rows = employee_activity(session, b.id, "frontdesk")

    assert [r.when for r in rows][0] >= [r.when for r in rows][-1]
    assert "newer" in rows[0].summary


def test_activity_is_business_scoped(session):
    a = _business(session, "er9a@test.io")
    b = _business(session, "er9b@test.io")
    session.add(Job(business_id=a.id, customer_phone="+1", service_type="theirs",
                    urgency="routine"))
    session.commit()

    assert employee_activity(session, b.id, "frontdesk") == []


# --- department outcomes now derive from deployed employees -------------------


def test_department_outcomes_are_the_union_of_its_deployed_employees(session):
    b = _business(session, "er10@test.io")
    session.add(Job(business_id=b.id, customer_phone="+1", service_type="x", urgency="routine"))
    session.commit()

    both = metrics.department_outcomes(session, b.id, "customer_service",
                                       ["frontdesk", "reviews"])
    only_frontdesk = metrics.department_outcomes(session, b.id, "customer_service",
                                                 ["frontdesk"])

    assert JOBS_BOOKED in both and REVIEW_REQUESTS_SENT in both
    assert REVIEW_REQUESTS_SENT not in only_frontdesk, (
        "a department must not report numbers for an employee it hasn't deployed"
    )


def test_a_department_with_nothing_deployed_reports_nothing(session):
    b = _business(session, "er11@test.io")

    assert metrics.department_outcomes(session, b.id, "customer_service", []) == {}


def test_sales_outcomes_come_from_quote_chaser(session):
    b = _business(session, "er12@test.io")
    camp = RecoveryCampaign(business_id=b.id, face="quote", name="c", customer_list_json="[]")
    session.add(camp)
    session.commit()
    session.refresh(camp)
    session.add(RecoveryJob(campaign_id=camp.id, business_id=b.id, customer_phone="+1",
                            service_type="x", current_status="booked"))
    session.commit()

    assert metrics.department_outcomes(session, b.id, "sales", ["quote_chaser"])[QUOTES_CHASED] == 1


def test_lead_qualifier_counts_every_qualification_axis(session):
    b = _business(session, "er13@test.io")
    other = _business(session, "er13b@test.io")
    job1 = Job(business_id=b.id, customer_phone="+1", service_type="AC not cooling", urgency="routine")
    job2 = Job(business_id=b.id, customer_phone="+2", service_type="AC not cooling", urgency="routine")
    session.add(job1)
    session.add(job2)
    session.commit()
    session.refresh(job1)
    session.refresh(job2)
    session.add(JobQualification(
        business_id=b.id, source_job_id=job1.id, job_type="replacement",
        financing_candidate=True, membership_candidate=False, priority="high",
        possible_spam=False, reasoning="X",
    ))
    session.add(JobQualification(
        business_id=b.id, source_job_id=job2.id, job_type="repair",
        financing_candidate=False, membership_candidate=True, priority="normal",
        possible_spam=True, reasoning="Y",
    ))
    # a qualification on a DIFFERENT business must never bleed into b's counts
    other_job = Job(business_id=other.id, customer_phone="+3", service_type="x", urgency="routine")
    session.add(other_job)
    session.commit()
    session.refresh(other_job)
    session.add(JobQualification(
        business_id=other.id, source_job_id=other_job.id, job_type="replacement",
        financing_candidate=True, membership_candidate=False, priority="high",
        possible_spam=False, reasoning="Z",
    ))
    session.commit()

    outcomes = metrics.department_outcomes(session, b.id, "sales", ["lead_qualifier"])
    assert outcomes[LEADS_QUALIFIED] == 2
    assert outcomes[HIGH_PRIORITY_LEADS] == 1
    assert outcomes[FINANCING_CANDIDATES] == 1
    assert outcomes[MEMBERSHIP_CANDIDATES] == 1
    assert outcomes[POSSIBLE_SPAM_FLAGGED] == 1


def test_dispatcher_counts_every_dispatch_axis(session):
    b = _business(session, "er14@test.io")
    other = _business(session, "er14b@test.io")
    job1 = Job(business_id=b.id, customer_phone="+1", service_type="x", urgency="emergency")
    job2 = Job(business_id=b.id, customer_phone="+2", service_type="x", urgency="routine")
    session.add(job1)
    session.add(job2)
    session.commit()
    session.refresh(job1)
    session.refresh(job2)
    session.add(DispatchPlan(
        business_id=b.id, source_job_id=job1.id, dispatch_priority="emergency",
        scheduling_window="immediate", requires_dispatch_review=True, dispatch_reason="X",
    ))
    session.add(DispatchPlan(
        business_id=b.id, source_job_id=job2.id, dispatch_priority="normal",
        scheduling_window="flexible", requires_dispatch_review=False, dispatch_reason="Y",
    ))
    # a plan on a DIFFERENT business must never bleed into b's counts
    other_job = Job(business_id=other.id, customer_phone="+3", service_type="x", urgency="emergency")
    session.add(other_job)
    session.commit()
    session.refresh(other_job)
    session.add(DispatchPlan(
        business_id=other.id, source_job_id=other_job.id, dispatch_priority="emergency",
        scheduling_window="immediate", requires_dispatch_review=True, dispatch_reason="Z",
    ))
    session.commit()

    outcomes = metrics.department_outcomes(session, b.id, "operations", ["dispatcher"])
    assert outcomes[JOBS_DISPATCHED] == 2
    assert outcomes[EMERGENCY_DISPATCHES] == 1
    assert outcomes[SAME_DAY_DISPATCHES] == 0
    assert outcomes[MANUAL_REVIEW_FLAGGED] == 1
