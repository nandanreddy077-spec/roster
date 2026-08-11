"""The single deployment path. Before this module, three separate places
created Employee rows (db.py's backfill x2, portal.py's _hire_employee) and
two more marked a business deployed without creating one at all
(activation.activate_frontdesk, app.deploy_employee) — audit F1."""

import pytest
from db_models import Business, Employee
from deployment import deploy_department, deploy_role
from sqlmodel import select


def _business(session, email):
    b = Business(business_name="B", trade="hvac", email=email)
    session.add(b)
    session.commit()
    session.refresh(b)
    return b


def test_deploy_role_creates_the_employee_row(session):
    b = _business(session, "dep1@test.io")

    row = deploy_role(session, b.id, "frontdesk")

    assert row is not None
    assert row.id is not None
    assert row.role_key == "frontdesk"
    assert row.status == "active"


def test_deploying_the_same_role_twice_creates_one_row(session):
    """I2. Idempotent: the second call is a no-op that reports it created
    nothing, so a caller can tell a fresh deploy from a repeat."""
    b = _business(session, "dep2@test.io")

    first = deploy_role(session, b.id, "frontdesk")
    second = deploy_role(session, b.id, "frontdesk")

    assert first is not None
    assert second is None
    assert len(session.exec(select(Employee).where(Employee.business_id == b.id)).all()) == 1


def test_deploying_a_planned_employee_is_rejected(session):
    """I5 / audit F4. `financing` has no engine — status `planned` in the
    registry. Creating a row for it would make active_departments_for()
    report Finance as STAFFED, and the customer's dashboard would show a
    department that cannot do anything. (`dispatcher` used to be this
    example, until it gained a real deterministic engine — 2026-07-30,
    Critical Finding #2 fix.)"""
    b = _business(session, "dep3@test.io")

    with pytest.raises(ValueError):
        deploy_role(session, b.id, "financing")

    assert session.exec(select(Employee).where(Employee.business_id == b.id)).all() == []


def test_deploying_an_unknown_role_is_rejected(session):
    b = _business(session, "dep4@test.io")

    with pytest.raises(ValueError):
        deploy_role(session, b.id, "not_a_role")


def test_deploying_for_one_business_never_touches_another(session):
    """I12. Business isolation is the security boundary (platform PRD §12)."""
    a = _business(session, "dep5a@test.io")
    b = _business(session, "dep5b@test.io")

    deploy_role(session, a.id, "frontdesk")

    assert session.exec(select(Employee).where(Employee.business_id == b.id)).all() == []


def test_deploy_department_creates_every_deployable_role(session):
    """Customer Service = frontdesk (live) + reviews (internal). `support` is
    planned and must be skipped, not deployed."""
    b = _business(session, "dep6@test.io")

    created = deploy_department(session, b.id, "customer_service")

    keys = {e.role_key for e in created}
    assert keys == {"frontdesk", "reviews"}
    assert "support" not in keys


def test_deploy_department_is_idempotent(session):
    b = _business(session, "dep7@test.io")

    deploy_department(session, b.id, "customer_service")
    second = deploy_department(session, b.id, "customer_service")

    assert second == []
    assert len(session.exec(select(Employee).where(Employee.business_id == b.id)).all()) == 2


def test_re_running_a_partial_deployment_completes_it(session):
    """I10 — recovery with no special path. A crash after the first role
    leaves the department half-staffed; re-running the same action fills only
    the gap, and reports only what it filled."""
    b = _business(session, "dep8@test.io")
    deploy_role(session, b.id, "frontdesk")  # simulate a partial deploy

    created = deploy_department(session, b.id, "customer_service")

    assert [e.role_key for e in created] == ["reviews"]
    assert len(session.exec(select(Employee).where(Employee.business_id == b.id)).all()) == 2


def test_deploy_department_rejects_a_department_with_nothing_deployable(session):
    """audit F4: Finance and Marketing are hireable in the registry but have
    zero live/internal employees today. Refusing loudly is what stops the
    ops console from 'deploying' vaporware. (Operations used to be a third
    example here too, until Dispatcher gained a real engine — 2026-07-30,
    Critical Finding #2 fix.)"""
    b = _business(session, "dep9@test.io")

    with pytest.raises(ValueError):
        deploy_department(session, b.id, "finance")


def test_deploy_department_rejects_leadership(session):
    b = _business(session, "dep10@test.io")

    with pytest.raises(ValueError):
        deploy_department(session, b.id, "leadership")


def test_every_deployable_role_resolves_to_a_department(session):
    """I4. A deployed role whose key doesn't resolve would vanish from every
    department view — the exact failure Phase 1's alias exists to prevent,
    asserted here against what deployment can actually write."""
    from departments import REGISTRY, department_for_role, deployable_employees_for

    for department in REGISTRY:
        for employee in deployable_employees_for(department.key):
            assert department_for_role(employee.key) is not None, employee.key


# --- every path now creates rows immediately (I1) -----------------------------


def test_activate_frontdesk_creates_the_employee_row_immediately(session):
    """I1 / audit F1. The second of the two paths that marked a business
    deployed without creating a row — the dashboard would have shown zero
    departments until the application restarted."""
    from activation import activate_frontdesk

    b = _business(session, "wire1@test.io")
    activate_frontdesk(session, b)

    rows = session.exec(select(Employee).where(Employee.business_id == b.id)).all()
    assert [r.role_key for r in rows] == ["frontdesk"]


def test_frontdesk_live_and_the_employee_row_agree(session):
    """I14. Two fields encode the same fact; after activation they must not
    disagree. runner.is_active moves onto the Employee row in Task 4, leaving
    frontdesk_live as the activation-lifecycle flag the portal redirects on."""
    from activation import activate_frontdesk
    from departments import active_departments_for

    b = _business(session, "wire2@test.io")
    activate_frontdesk(session, b)
    session.refresh(b)

    employees = session.exec(select(Employee).where(Employee.business_id == b.id)).all()
    assert b.frontdesk_live is True
    assert [d.key for d in active_departments_for(employees)] == ["customer_service"]


def test_backfill_is_a_no_op_once_deployment_creates_rows(session):
    """I8. The boot backfill must not double-create what deployment already
    made, and must not resurrect anything."""
    from activation import activate_frontdesk
    from db import _backfill_employees

    b = _business(session, "wire3@test.io")
    activate_frontdesk(session, b)

    _backfill_employees(session.get_bind())

    rows = session.exec(select(Employee).where(Employee.business_id == b.id)).all()
    assert len(rows) == 1


# --- is_active parity (I6) ----------------------------------------------------


def test_is_active_parity_frontdesk(session):
    """I6: same answers before and after the migration. Frontdesk was read
    from Business.frontdesk_live; it now comes from the Employee row that
    activate_frontdesk creates alongside that flag."""
    from activation import activate_frontdesk
    from runner import is_active

    b = _business(session, "parity1@test.io")
    assert is_active(session, b, "frontdesk") is False

    activate_frontdesk(session, b)
    assert is_active(session, b, "frontdesk") is True


def test_is_active_parity_deployed_role(session):
    from runner import is_active

    b = _business(session, "parity2@test.io")
    assert is_active(session, b, "quote_chaser") is False

    deploy_role(session, b.id, "quote_chaser")
    assert is_active(session, b, "quote_chaser") is True


def test_is_active_parity_retention_legacy_key(session):
    """The stored key is "retention" (pinned by test_employee_model.py), but
    callers ask for "retention_manager". Both must answer the same, or the
    migration silently loses an employee (audit F8/I13)."""
    from runner import is_active

    b = _business(session, "parity3@test.io")
    deploy_role(session, b.id, "retention")

    assert is_active(session, b, "retention") is True
    assert is_active(session, b, "retention_manager") is True


def test_is_active_ignores_a_fired_employee(session):
    from runner import is_active

    b = _business(session, "parity4@test.io")
    row = deploy_role(session, b.id, "quote_chaser")
    row.status = "fired"
    session.add(row)
    session.commit()

    assert is_active(session, b, "quote_chaser") is False


def test_is_active_never_crosses_businesses(session):
    """I12 at the engine layer."""
    from runner import is_active

    a = _business(session, "parity5a@test.io")
    b = _business(session, "parity5b@test.io")
    deploy_role(session, a.id, "quote_chaser")

    assert is_active(session, b, "quote_chaser") is False
