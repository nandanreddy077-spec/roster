"""The deployed app has exactly two faces: a public one (landing, hire flow,
webhooks) and a founder-only one (/clients dashboard, behind HTTP Basic).
These tests pin that boundary — the dashboard must never serve without
credentials, and the public surface must never *require* them."""
import base64

from fastapi.testclient import TestClient

import app as app_module


def _basic(password: str) -> dict:
    token = base64.b64encode(f"admin:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


# ---- Dashboard: locked without the right password ---------------------------


def test_clients_requires_auth(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "hunter2")
    client = TestClient(app_module.app)
    response = client.get("/clients")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Basic")


def test_clients_rejects_wrong_password(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "hunter2")
    client = TestClient(app_module.app)
    response = client.get("/clients", headers=_basic("wrong"))
    assert response.status_code == 401


def test_clients_allows_correct_password(monkeypatch, test_engine):
    monkeypatch.setenv("ADMIN_PASSWORD", "hunter2")
    monkeypatch.setattr(app_module, "engine", test_engine)
    client = TestClient(app_module.app)
    response = client.get("/clients", headers=_basic("hunter2"))
    assert response.status_code == 200


def test_clients_subpaths_are_protected(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "hunter2")
    client = TestClient(app_module.app)
    assert client.get("/clients/new").status_code == 401
    assert client.post("/clients/1/chat", data={"message": "hi"}).status_code == 401


def test_dashboard_fails_closed_without_password(monkeypatch):
    """Forgetting ADMIN_PASSWORD on a fresh deploy must lock the dashboard,
    not open it — client conversations are behind this door."""
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    client = TestClient(app_module.app)
    response = client.get("/clients")
    assert response.status_code == 503
    assert "ADMIN_PASSWORD" in response.text


# ---- Public surface: stays public -------------------------------------------


def test_landing_served_at_root(monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    client = TestClient(app_module.app)
    response = client.get("/")
    assert response.status_code == 200
    assert "Roster" in response.text
    assert response.headers["content-type"].startswith("text/html")


def test_landing_stylesheet_served(monkeypatch):
    client = TestClient(app_module.app)
    response = client.get("/styles.css")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")


def test_hire_flow_is_public(monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    client = TestClient(app_module.app)
    response = client.get("/hire")
    assert response.status_code == 200
    assert "Frontdesk" in response.text


def test_hire_submit_is_public(monkeypatch, test_engine):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr(app_module, "engine", test_engine)
    client = TestClient(app_module.app)
    response = client.post(
        "/hire",
        data={
            "business_name": "Auth Test Plumbing",
            "inbound_number": "(555) 555-0100",
            "trade": "Plumbing",
            "services": "Drains",
            "hours": "9-5",
            "answer_mode": "backup",
            "pricing_answer": "$79 service call",
            "escalation_rule": "emergencies",
            "owner_name": "Pat",
            "escalation_phone": "(555) 555-0101",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/hire/done/")


def test_sms_webhook_is_public(monkeypatch, test_engine):
    """Twilio can't do Basic auth — the webhook authenticates by routing to a
    known inbound number and must not be caught by the dashboard gate."""
    monkeypatch.setenv("ADMIN_PASSWORD", "hunter2")
    monkeypatch.setattr(app_module, "engine", test_engine)
    client = TestClient(app_module.app)
    response = client.post(
        "/webhook/sms",
        data={"From": "+15550001111", "To": "+15559998888", "Body": "hello"},
    )
    # Unknown number → polite TwiML fallback, but crucially not 401/503.
    assert response.status_code == 200
    assert "isn't set up" in response.text
