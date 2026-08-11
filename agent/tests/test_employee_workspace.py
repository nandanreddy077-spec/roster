"""EmployeeWorkspace — the second view model (founder, 2026-07-29). It derives
from DepartmentWorkspace rather than composing itself from EMPLOYEE_RECORDS
and metrics directly, so "is this employee actually working" is decided
exactly once, by the department builder — the employee page can never
disagree with the page it was reached from."""

from db_models import Business, Job
from deployment import deploy_department, deploy_role
from workspace import EmployeeWorkspace, build_employee_workspace


def _business(session, email):
    b = Business(business_name="B", trade="hvac", email=email)
    session.add(b)
    session.commit()
    session.refresh(b)
    return b


def test_the_view_model_has_exactly_its_five_fields():
    """The same structural guard DepartmentStatus and METRIC_RECORDS already
    have: this page stays mission/status/outcomes/activity, not a growing
    analytics surface."""
    assert set(EmployeeWorkspace.__dataclass_fields__) == {
        "employee",
        "mission",
        "status",
        "outcomes",
        "activity",
    }


def test_unknown_department_returns_none(session):
    b = _business(session, "ew1@test.io")

    assert build_employee_workspace(session, b.id, "not_a_department", "frontdesk") is None


def test_a_department_the_business_has_not_deployed_returns_none(session):
    b = _business(session, "ew2@test.io")

    assert build_employee_workspace(session, b.id, "customer_service", "frontdesk") is None


def test_a_role_not_staffed_in_an_otherwise_active_department_returns_none(session):
    b = _business(session, "ew3@test.io")
    deploy_role(session, b.id, "frontdesk")  # reviews NOT deployed

    assert build_employee_workspace(session, b.id, "customer_service", "reviews") is None


def test_a_role_from_a_different_department_returns_none(session):
    """quote_chaser is real and deployed — just not under customer_service."""
    b = _business(session, "ew4@test.io")
    deploy_department(session, b.id, "customer_service")
    deploy_department(session, b.id, "sales")

    assert build_employee_workspace(session, b.id, "customer_service", "quote_chaser") is None


def test_a_deployed_employee_assembles_the_full_workspace(session):
    b = _business(session, "ew5@test.io")
    deploy_department(session, b.id, "customer_service")
    session.add(
        Job(
            business_id=b.id,
            customer_phone="+15125550100",
            service_type="AC repair",
            urgency="routine",
        )
    )
    session.commit()

    ws = build_employee_workspace(session, b.id, "customer_service", "frontdesk")

    assert ws.employee.key == "frontdesk"
    assert ws.mission == "Is Frontdesk answering customers?"
    assert ws.status == "Working"
    assert ("Jobs booked", 1) in ws.outcomes


def test_activity_is_not_capped_to_the_department_pages_preview_size(session):
    """The department workspace's employee card previews only 3 of an
    employee's activity rows (Task 6). This standalone page must not inherit
    that cap — it IS the drill-down."""
    b = _business(session, "ew6@test.io")
    deploy_department(session, b.id, "customer_service")
    for i in range(8):
        session.add(
            Job(
                business_id=b.id,
                customer_phone=f"+1512555{i:04d}",
                service_type=f"job {i}",
                urgency="routine",
            )
        )
    session.commit()

    ws = build_employee_workspace(session, b.id, "customer_service", "frontdesk")

    assert len(ws.activity) == 8


def test_workspace_never_crosses_businesses(session):
    a = _business(session, "ew7a@test.io")
    b = _business(session, "ew7b@test.io")
    deploy_department(session, a.id, "customer_service")

    assert build_employee_workspace(session, b.id, "customer_service", "frontdesk") is None


def test_sales_employee_workspace_answers_quote_chasers_question(session):
    b = _business(session, "ew8@test.io")
    deploy_department(session, b.id, "sales")

    ws = build_employee_workspace(session, b.id, "sales", "quote_chaser")

    assert ws.mission == "Is Quote Chaser recovering revenue?"
