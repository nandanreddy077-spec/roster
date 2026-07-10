from fastapi.testclient import TestClient

import app as app_module
import db as db_module
import portal as portal_module


def _fully_onboarded_client(client: TestClient):
    client.post("/signup", data={"email": "owner@example.com", "password": "hunter22"})
    client.post(
        "/onboarding/business",
        data={"business_name": "Ridgeline Plumbing", "trade": "Plumbing", "services": "Drains", "hours": "9-5"},
    )
    client.post("/onboarding/receptionist", data={"escalation_phone": "(555) 555-0101"})


def test_activation_live_redirects_if_not_yet_live(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    client.post("/signup", data={"email": "owner@example.com", "password": "hunter22"})

    response = client.get("/activation/live", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/onboarding/business"


def test_activation_live_shows_role_name_and_checklist(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    client = TestClient(app_module.app)
    _fully_onboarded_client(client)

    response = client.get("/activation/live")
    assert response.status_code == 200
    assert "Receptionist" in response.text  # Plumbing -> "Receptionist" per roles.py
    assert "Ridgeline Plumbing" in response.text
    assert "Answer" in response.text
