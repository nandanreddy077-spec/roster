"""The Department Workspace route and template. The template renders one
DepartmentWorkspace object (workspace.py); these tests check what actually
appears in the HTML, and that no route logic recomputes what the view model
already assembled."""
from sqlmodel import Session
from starlette.testclient import TestClient

import app as app_module
import portal
from auth import hash_password
from db_models import Business, Job
from deployment import deploy_department

_EMAIL = iter(f"dw-{n}@test.io" for n in range(1000))


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

    r = client.get("/v2/dashboard/departments/not_a_department")

    assert r.status_code == 404


def test_a_department_the_business_does_not_have_404s(test_engine, monkeypatch):
    client = _client_for(test_engine, monkeypatch)

    r = client.get("/v2/dashboard/departments/customer_service")

    assert r.status_code == 404


def test_the_workspace_renders_the_question_and_employees(test_engine, monkeypatch):
    client = _client_for(test_engine, monkeypatch, deploy="customer_service", jobs=2)

    r = client.get("/v2/dashboard/departments/customer_service")

    assert r.status_code == 200
    assert "Are customers being looked after?" in r.text
    assert "Frontdesk" in r.text
    assert "Reviews" in r.text


def test_outcomes_precede_employee_detail_in_dom_order(test_engine, monkeypatch):
    """Blueprint §2's hierarchy — outcomes first, employees second — made
    testable rather than merely intended."""
    client = _client_for(test_engine, monkeypatch, deploy="customer_service", jobs=1)

    body = client.get("/v2/dashboard/departments/customer_service").text

    assert body.index("Jobs booked") < body.index("Frontdesk")


def test_employee_cards_are_not_yet_links(test_engine, monkeypatch):
    """Task 7 adds the click-through once its route exists. Shipping a link
    to a 404 now would repeat the Phase 4b 'no dead controls' mistake."""
    client = _client_for(test_engine, monkeypatch, deploy="customer_service")

    body = client.get("/v2/dashboard/departments/customer_service").text

    assert 'href="/v2/dashboard/departments/customer_service/employees/frontdesk"' not in body


def test_no_ai_internal_term_appears(test_engine, monkeypatch):
    body = _client_for(test_engine, monkeypatch, deploy="sales").get(
        "/v2/dashboard/departments/sales"
    ).text

    for word in ("token", "model", "prompt", "LLM"):
        assert word.lower() not in body.lower()
