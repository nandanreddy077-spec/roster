"""DepartmentWorkspace — the one view model the Department Workspace template
renders (founder, 2026-07-29). It joins DepartmentStatus + EMPLOYEE_RECORDS +
METRIC_RECORDS exactly once, so the template never has to; no route or
template contains the join logic tested here."""
from sqlmodel import Session

from db_models import Business, Employee, Job
from deployment import deploy_department, deploy_role
from workspace import ACTIVE_STATES, CUSTOMER_STATE_LABELS, METRIC_LABELS, build_department_workspace


def _business(session, email):
    b = Business(business_name="B", trade="hvac", email=email)
    session.add(b)
    session.commit()
    session.refresh(b)
    return b


def test_unknown_department_key_returns_none(session):
    b = _business(session, "w1@test.io")

    assert build_department_workspace(session, b.id, "not_a_department") is None


def test_a_department_the_business_has_not_deployed_returns_none(session):
    """The route 404s rather than rendering an empty workspace for a
    department the customer doesn't have (blueprint: open your own office,
    not a directory of ones you might rent)."""
    b = _business(session, "w2@test.io")

    assert build_department_workspace(session, b.id, "customer_service") is None


def test_a_department_with_no_deployable_employees_returns_none(session):
    b = _business(session, "w3@test.io")

    assert build_department_workspace(session, b.id, "operations") is None


def test_an_active_department_assembles_the_full_workspace(session):
    b = _business(session, "w4@test.io")
    deploy_department(session, b.id, "customer_service")
    session.add(Job(business_id=b.id, customer_phone="+15125550100",
                    service_type="AC repair", urgency="routine"))
    session.commit()

    ws = build_department_workspace(session, b.id, "customer_service")

    assert ws.department.key == "customer_service"
    assert ws.question == ws.department.question
    assert ws.is_active is True
    assert ws.health_label == "Working"


def test_a_partially_staffed_department_is_still_active_and_working(session):
    """C7, carried into the workspace: half-staffed IS doing work."""
    b = _business(session, "w5@test.io")
    deploy_role(session, b.id, "frontdesk")

    ws = build_department_workspace(session, b.id, "customer_service")

    assert ws.is_active is True
    assert ws.health_label == "Working"
    assert {e.role_key for e in ws.employees} == {"frontdesk"}


def test_employees_come_from_employee_records_with_their_own_outcomes(session):
    b = _business(session, "w6@test.io")
    deploy_department(session, b.id, "customer_service")
    session.add(Job(business_id=b.id, customer_phone="+15125550100",
                    service_type="AC repair", urgency="routine"))
    session.commit()

    ws = build_department_workspace(session, b.id, "customer_service")
    frontdesk = next(e for e in ws.employees if e.role_key == "frontdesk")

    assert frontdesk.display_name == "Frontdesk"
    assert (METRIC_LABELS["jobs_booked"], 1) in frontdesk.outcomes


def test_department_outcomes_are_the_union_of_its_employees(session):
    """Never more than the union — a workspace must not report a number for
    an employee it hasn't deployed, even implicitly."""
    b = _business(session, "w7@test.io")
    deploy_role(session, b.id, "frontdesk")  # reviews NOT deployed

    ws = build_department_workspace(session, b.id, "customer_service")

    labels = dict(ws.outcomes)
    assert METRIC_LABELS["jobs_booked"] in labels
    assert METRIC_LABELS["review_requests_sent"] not in labels


def test_department_activity_is_the_union_of_its_employees_newest_first(session):
    from datetime import datetime, timedelta

    b = _business(session, "w8@test.io")
    deploy_department(session, b.id, "customer_service")
    session.add(Job(business_id=b.id, customer_phone="+1", service_type="older",
                    urgency="routine", created_at=datetime.utcnow() - timedelta(hours=3)))
    session.add(Job(business_id=b.id, customer_phone="+2", service_type="newer",
                    urgency="routine"))
    session.commit()

    ws = build_department_workspace(session, b.id, "customer_service")

    assert "newer" in ws.activity[0].summary


def test_a_fired_employee_is_absent_from_the_workspace(session):
    """Proves the workspace reuses DepartmentStatus's existing filter rather
    than a second, independent one that could disagree with it."""
    from sqlmodel import select

    b = _business(session, "w9@test.io")
    deploy_department(session, b.id, "customer_service")
    row = session.exec(
        select(Employee).where(Employee.business_id == b.id, Employee.role_key == "frontdesk")
    ).first()
    row.status = "fired"
    session.add(row)
    session.commit()

    ws = build_department_workspace(session, b.id, "customer_service")

    assert "frontdesk" not in {e.role_key for e in ws.employees}
    assert ws.is_active is True  # Reviews alone still keeps it working


def test_workspace_never_crosses_businesses(session):
    a = _business(session, "w10a@test.io")
    b = _business(session, "w10b@test.io")
    deploy_department(session, a.id, "customer_service")
    session.add(Job(business_id=a.id, customer_phone="+1", service_type="theirs",
                    urgency="routine"))
    session.commit()

    assert build_department_workspace(session, b.id, "customer_service") is None


def test_sales_workspace_uses_quote_chaser(session):
    b = _business(session, "w11@test.io")
    deploy_department(session, b.id, "sales")

    ws = build_department_workspace(session, b.id, "sales")

    assert {e.role_key for e in ws.employees} == {"quote_chaser"}


def test_customer_state_labels_and_active_states_are_consistent():
    """Every state DepartmentStatus can produce has a customer label, and the
    ACTIVE_STATES set agrees with which ones read as Working."""
    assert set(CUSTOMER_STATE_LABELS) == {"staffed", "partial", "empty", "unavailable"}
    assert set(ACTIVE_STATES) == {"staffed", "partial"}
    for state in ACTIVE_STATES:
        assert CUSTOMER_STATE_LABELS[state] == "Working"
