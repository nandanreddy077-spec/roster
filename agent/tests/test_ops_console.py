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
