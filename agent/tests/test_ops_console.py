"""The founder ops console.

Before Phase 4b the 'Agent roster' on this page was a fiction: Frontdesk was
hardcoded active for every business, and the other tiles derived 'active' from
unrelated config fields (a review link being set, a campaign existing). The
founder has never had a real deployment view — see audit B1."""
from sqlalchemy import event
from sqlmodel import Session, select
from starlette.testclient import TestClient

import app as app_module
from conftest import DASH_AUTH
from db_models import Business, Employee
from deployment import deploy_department, deploy_role


def _business(session, name="Test Co"):
    b = Business(business_name=name, trade="HVAC")
    session.add(b)
    session.commit()
    session.refresh(b)
    return b


def test_a_business_with_nothing_deployed_shows_no_staffed_departments(test_engine, monkeypatch):
    """B1: the page used to claim Frontdesk was active for every business,
    deployed or not."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id

    body = TestClient(app_module.app, headers=DASH_AUTH).get(f"/clients/{bid}").text

    assert "Not staffed" in body
    assert "Customer Service" in body  # the department is listed, just not staffed


def test_a_deployed_department_is_shown_as_staffed(test_engine, monkeypatch):
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id
        deploy_department(s, bid, "customer_service")

    body = TestClient(app_module.app, headers=DASH_AUTH).get(f"/clients/{bid}").text

    assert "Working: Frontdesk, Reviews" in body


def test_a_partially_staffed_department_offers_completion_not_a_fresh_deploy(test_engine, monkeypatch):
    """B9: the affordance must tell the truth about what the click will do."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id
        deploy_role(s, bid, "frontdesk")  # half of Customer Service

    body = TestClient(app_module.app, headers=DASH_AUTH).get(f"/clients/{bid}").text

    assert "Complete deployment" in body
    assert "Partially staffed" in body


def test_a_department_with_nothing_deployable_is_explained_not_offered(test_engine, monkeypatch):
    """B10: Operations/Finance/Marketing have zero live-or-internal employees
    today. Rendering a button that always errors would be a lie."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id

    body = TestClient(app_module.app, headers=DASH_AUTH).get(f"/clients/{bid}").text

    assert "No employees built yet" in body


def test_the_clients_list_shows_real_staffed_departments(test_engine, monkeypatch):
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        staffed = _business(s, "Staffed Co").id
        _business(s, "Empty Co")
        deploy_department(s, staffed, "customer_service")

    body = TestClient(app_module.app, headers=DASH_AUTH).get("/clients").text

    assert "Staffed Co" in body and "Empty Co" in body
    assert "Customer Service" in body


def test_the_clients_list_does_not_query_employees_per_business(test_engine, monkeypatch):
    """B6: one grouped query for every business's employees, matching the
    pattern the recovery counts already use. Asserting the query COUNT is what
    stops this regressing into an N+1 as the client list grows."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        for i in range(5):
            bid = _business(s, f"Co {i}").id
            deploy_role(s, bid, "frontdesk")

    statements = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(test_engine, "before_cursor_execute", _record)
    try:
        TestClient(app_module.app, headers=DASH_AUTH).get("/clients")
    finally:
        event.remove(test_engine, "before_cursor_execute", _record)

    employee_queries = [s_ for s_ in statements if "FROM employee" in s_]
    assert len(employee_queries) == 1, (
        f"expected ONE grouped employee query, got {len(employee_queries)} "
        "— this is an N+1 over the client list"
    )


# --- deploy by department ------------------------------------------------------


def _post_deploy(bid, **data):
    return TestClient(app_module.app, headers=DASH_AUTH).post(
        f"/clients/{bid}/employees/deploy", data=data, follow_redirects=False
    )


def test_deploying_a_department_creates_every_deployable_role(test_engine, monkeypatch):
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id

    r = _post_deploy(bid, department_key="customer_service")

    assert r.status_code == 303
    with Session(test_engine) as s:
        keys = {e.role_key for e in s.exec(
            select(Employee).where(Employee.business_id == bid)).all()}
        assert keys == {"frontdesk", "reviews"}


def test_deploying_the_same_department_twice_is_a_no_op(test_engine, monkeypatch):
    """B9: a double-submit must reach the same end state, not error and not
    duplicate."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id

    first = _post_deploy(bid, department_key="customer_service")
    second = _post_deploy(bid, department_key="customer_service")

    assert first.status_code == 303 and second.status_code == 303
    with Session(test_engine) as s:
        assert len(s.exec(select(Employee).where(Employee.business_id == bid)).all()) == 2


def test_deploying_a_department_with_nothing_deployable_does_not_500(test_engine, monkeypatch):
    """B10: deploy_department raises for these. The route must surface it as a
    message on the page, never as a stack trace."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id

    r = _post_deploy(bid, department_key="operations")

    assert r.status_code == 303
    assert "deploy_error" in r.headers["location"]
    with Session(test_engine) as s:
        assert s.exec(select(Employee).where(Employee.business_id == bid)).all() == []


def test_the_legacy_role_key_form_still_works(test_engine, monkeypatch):
    """role_key predates this and is still how a single employee is deployed;
    it must keep working (test_employee_deploy.py covers it too)."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id

    _post_deploy(bid, role_key="quote_chaser")

    with Session(test_engine) as s:
        assert [e.role_key for e in s.exec(
            select(Employee).where(Employee.business_id == bid)).all()] == ["quote_chaser"]


def test_deploying_a_department_then_following_the_redirect_shows_it_staffed(
    test_engine, monkeypatch
):
    """The founder's actual workflow, end to end: submit the form, follow the
    303, and see the result. Route-level and template-level tests both passing
    would still permit a page that renders stale data after a write — this is
    what catches that."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id
    client = TestClient(app_module.app, headers=DASH_AUTH)

    posted = client.post(
        f"/clients/{bid}/employees/deploy",
        data={"department_key": "customer_service"},
        follow_redirects=False,
    )
    assert posted.status_code == 303

    landed = client.get(posted.headers["location"])

    assert landed.status_code == 200
    assert "Working: Frontdesk, Reviews" in landed.text
    # and the deploy affordance is gone for that department, not offered again
    assert "Complete deployment" not in landed.text
