"""The Employee Workspace route and template — the drill-down leaf of
Overview -> Department -> Employee -> Activity. Renders one EmployeeWorkspace
(workspace.py); the route never touches EMPLOYEE_RECORDS or metrics.py."""
from sqlmodel import Session
from starlette.testclient import TestClient

import app as app_module
import portal
from auth import hash_password
from db_models import Business, Job
from deployment import deploy_department

_EMAIL = iter(f"ew-{n}@test.io" for n in range(1000))


def _client_for(test_engine, monkeypatch, deploy=None, jobs=0):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(portal, "engine", test_engine)
    email = next(_EMAIL)
    with Session(test_engine) as s:
        b = Business(business_name="Ridgeline Plumbing", trade="Plumbing",
                     email=email, password_hash=hash_password("pw12345"))
        s.add(b)
        s.commit()
        s.refresh(b)
        bid = b.id
        if deploy:
            deploy_department(s, bid, deploy)
        for i in range(jobs):
            s.add(Job(business_id=bid, customer_phone=f"+1512555{i:04d}",
                      service_type="Drain cleaning", urgency="routine"))
        s.commit()
    client = TestClient(app_module.app)
    client.post("/login", data={"email": email, "password": "pw12345"})
    return client


def test_unknown_department_404s(test_engine, monkeypatch):
    client = _client_for(test_engine, monkeypatch)

    r = client.get("/v2/dashboard/departments/not_a_department/employees/frontdesk")

    assert r.status_code == 404


def test_a_role_not_staffed_404s(test_engine, monkeypatch):
    client = _client_for(test_engine, monkeypatch, deploy="customer_service")

    r = client.get("/v2/dashboard/departments/customer_service/employees/quote_chaser")

    assert r.status_code == 404


def test_the_page_renders_mission_and_status_before_activity(test_engine, monkeypatch):
    """The product requirement: the page answers its one question before
    showing any raw event — outcomes and mission first, in DOM order."""
    client = _client_for(test_engine, monkeypatch, deploy="customer_service", jobs=1)

    body = client.get("/v2/dashboard/departments/customer_service/employees/frontdesk").text

    assert "Is Frontdesk answering customers?" in body
    assert "Working" in body
    assert body.index("Is Frontdesk answering customers?") < body.index("Booked Drain cleaning")


def test_the_page_shows_the_full_activity_not_a_three_row_preview(test_engine, monkeypatch):
    client = _client_for(test_engine, monkeypatch, deploy="customer_service", jobs=5)

    body = client.get("/v2/dashboard/departments/customer_service/employees/frontdesk").text

    assert body.count("Booked Drain cleaning") == 5


def test_the_back_link_returns_to_the_department(test_engine, monkeypatch):
    client = _client_for(test_engine, monkeypatch, deploy="customer_service")

    body = client.get("/v2/dashboard/departments/customer_service/employees/frontdesk").text

    assert 'href="/v2/dashboard/departments/customer_service"' in body
    assert "Customer Service" in body


def test_no_ai_internal_term_appears(test_engine, monkeypatch):
    body = _client_for(test_engine, monkeypatch, deploy="sales").get(
        "/v2/dashboard/departments/sales/employees/quote_chaser"
    ).text

    for word in ("token", "model", "prompt", "LLM"):
        assert word.lower() not in body.lower()
