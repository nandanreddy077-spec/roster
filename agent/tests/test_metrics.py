"""Outcome metrics — one definition, used by every surface.

Blueprint §6 puts an outcomes strip at the top of every department page, so
these numbers are about to be rendered in six more places. Before Phase 5 the
two surfaces disagreed: the customer dashboard excluded test-thread jobs, the
founder console counted nothing at all and showed test jobs as real work.

Facts only, keyed by a stable metric key — each surface supplies its own
wording, the same rule DepartmentStatus follows."""
import metrics
from db_models import Business, Job, RecoveryCampaign, RecoveryJob, ReferralLead
from metrics import JOBS_BOOKED, QUOTES_CHASED, QUOTES_RECOVERED, booked_jobs, department_outcomes


def _business(session, email):
    b = Business(business_name="B", trade="hvac", email=email)
    session.add(b)
    session.commit()
    session.refresh(b)
    return b


def _job(session, business_id, phone="+15125550100", **kw):
    j = Job(business_id=business_id, customer_phone=phone,
            service_type=kw.pop("service_type", "AC repair"),
            urgency=kw.pop("urgency", "routine"), **kw)
    session.add(j)
    session.commit()
    return j


def test_booked_jobs_counts_real_customer_jobs(session):
    b = _business(session, "m1@test.io")
    _job(session, b.id)
    _job(session, b.id, phone="+15125550101")

    assert booked_jobs(session, b.id) == 2


def test_booked_jobs_excludes_the_owners_own_tests(session):
    """The owner testing their own AI is not revenue. Both surfaces must agree
    on this — before Phase 5 only the customer dashboard applied it."""
    b = _business(session, "m2@test.io")
    _job(session, b.id)
    _job(session, b.id, phone="portal-test")
    _job(session, b.id, phone="dashboard")

    assert booked_jobs(session, b.id) == 1


def test_booked_jobs_never_counts_another_business(session):
    a = _business(session, "m3a@test.io")
    b = _business(session, "m3b@test.io")
    _job(session, a.id)

    assert booked_jobs(session, b.id) == 0


def test_booked_jobs_on_an_empty_business_is_zero(session):
    b = _business(session, "m4@test.io")

    assert booked_jobs(session, b.id) == 0


def test_customer_service_outcomes_report_booked_jobs(session):
    """Frontdesk now declares three metrics (Task 5's EMPLOYEE_RECORDS): jobs,
    calls, escalations. A true zero count is not a fabricated zero — the
    honesty rule forbids reporting a metric with NO attributable records at
    all, not reporting that a real, measurable thing happened zero times."""
    b = _business(session, "m5@test.io")
    _job(session, b.id)

    outcomes = department_outcomes(session, b.id, "customer_service", ["frontdesk"])
    assert outcomes[JOBS_BOOKED] == 1
    assert outcomes[metrics.CALLS_ANSWERED] == 0
    assert outcomes[metrics.ESCALATIONS] == 0


def test_sales_outcomes_report_quotes_chased_and_recovered(session):
    """department_outcomes derives from the DEPLOYED employees (Task 5) — a
    department can never report a number for an employee it hasn't deployed."""
    b = _business(session, "m6@test.io")
    camp = RecoveryCampaign(business_id=b.id, face="quote", name="June quotes",
                            customer_list_json="[]")
    session.add(camp)
    session.commit()
    session.refresh(camp)
    session.add(RecoveryJob(campaign_id=camp.id, business_id=b.id,
                            customer_phone="+1", service_type="AC install",
                            current_status="booked"))
    session.add(RecoveryJob(campaign_id=camp.id, business_id=b.id,
                            customer_phone="+2", service_type="Furnace",
                            current_status="no_response"))
    session.commit()

    assert department_outcomes(session, b.id, "sales", ["quote_chaser"]) == {
        QUOTES_CHASED: 2, QUOTES_RECOVERED: 1,
    }


def test_a_department_with_no_engine_reports_no_metrics_not_zeros(session):
    """Honesty: Finance has no engine (collections/financing are both still
    `planned`), so it has nothing to report even if role keys were passed
    for it. Showing '0 invoices collected' would imply a department that ran
    and achieved nothing, rather than one that was never built. Operations
    used to be this example too, until Dispatcher PR #1 (2026-07-30) gave it
    an engine."""
    b = _business(session, "m7@test.io")

    assert department_outcomes(session, b.id, "finance", ["collections", "financing"]) == {}


def test_outcomes_for_an_unknown_department_are_empty(session):
    b = _business(session, "m8@test.io")

    assert department_outcomes(session, b.id, "not_a_department", []) == {}


def test_department_outcomes_reports_nothing_for_an_undeployed_employee(session):
    """The Task 5 invariant, directly: passing a role_key that isn't actually
    deployed must not be how a caller gets numbers — this module trusts its
    caller to pass only deployed roles, but reports real records for whatever
    it's given, deployed or not, which is why portal.py passes status.staffed
    rather than every deployable role."""
    b = _business(session, "m8b@test.io")

    assert department_outcomes(session, b.id, "sales", []) == {}


def test_metric_keys_carry_no_wording(session):
    """Facts only, same rule as DepartmentStatus: each surface maps these keys
    to its own label, so neither audience's voice leaks into the other's."""
    b = _business(session, "m9@test.io")
    _job(session, b.id)

    keys = department_outcomes(session, b.id, "customer_service", ["frontdesk", "reviews"])
    for key in keys:
        assert key.islower() and " " not in key, f"{key!r} looks like a label, not a key"


def test_every_metric_declares_what_records_it_drills_into(session):
    """THE drill-down invariant (founder, 2026-07-29): every customer-visible
    number represents concrete underlying records the owner could one day be
    shown — never an opaque summary.

    A metric cannot ship without declaring its records, which rules out
    derived scores by construction: a number that can't name the rows behind
    it can't be declared, so it can't ship."""
    from departments import REGISTRY

    from departments import deployable_employees_for

    b = _business(session, "m11@test.io")
    for department in REGISTRY:
        role_keys = [e.key for e in deployable_employees_for(department.key)]
        for key in department_outcomes(session, b.id, department.key, role_keys):
            assert key in metrics.METRIC_RECORDS, (
                f"{key!r} is rendered to customers but declares no drill-down "
                "records — see metrics.METRIC_RECORDS"
            )


def test_no_metric_declares_an_empty_record_source(session):
    for key, records in metrics.METRIC_RECORDS.items():
        assert records.strip(), f"{key!r} declares no records"


def test_both_surfaces_derive_the_same_count_from_this_module(session, monkeypatch):
    """C4: the customer dashboard's job count must come from here, not from a
    second computation in portal.py that can drift."""
    import portal

    b = _business(session, "m10@test.io")
    _job(session, b.id)
    _job(session, b.id, phone="portal-test")

    assert portal.metrics.booked_jobs is metrics.booked_jobs
    assert booked_jobs(session, b.id) == 1
