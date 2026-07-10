import json
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session, select

import app as app_module
import db as db_module
import portal as portal_module
from db_models import Client, Job


def _fully_onboarded_client(client: TestClient, monkeypatch):
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    client.post("/signup", data={"email": "owner@example.com", "password": "hunter22"})
    client.post(
        "/onboarding/business",
        data={"business_name": "Ridgeline Plumbing", "trade": "Plumbing", "services": "Drains", "hours": "9-5"},
    )
    client.post("/onboarding/receptionist", data={"escalation_phone": "(555) 555-0101"})


def test_dashboard_requires_login(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_dashboard_zero_state_before_any_jobs(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    _fully_onboarded_client(client, monkeypatch)

    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "waiting for your first call" in response.text
    assert "Quote Chaser" in response.text  # hire-next card


def test_dashboard_shows_outcomes_once_a_job_exists(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    _fully_onboarded_client(client, monkeypatch)

    with Session(test_engine) as session:
        owner = session.exec(select(Client).where(Client.email == "owner@example.com")).first()
        session.add(Job(client_id=owner.id, service_type="drain cleaning", urgency="routine"))
        session.commit()

    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "waiting for your first call" not in response.text


def test_roster_hire_queues_quote_chaser(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    _fully_onboarded_client(client, monkeypatch)

    response = client.post("/roster/hire", data={"role": "Quote Chaser"}, follow_redirects=False)
    assert response.status_code == 303

    with Session(test_engine) as session:
        owner = session.exec(select(Client).where(Client.email == "owner@example.com")).first()
        assert json.loads(owner.requested_roster) == ["Quote Chaser"]

    response = client.get("/dashboard")
    assert "Retention Manager" in response.text  # now the next hire-next card


def test_roster_hire_rejects_out_of_order_role(monkeypatch, test_engine):
    """Can't hire Retention Manager before Quote Chaser — enforces sequencing."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    _fully_onboarded_client(client, monkeypatch)

    client.post("/roster/hire", data={"role": "Retention Manager"})

    with Session(test_engine) as session:
        owner = session.exec(select(Client).where(Client.email == "owner@example.com")).first()
        assert owner.requested_roster is None


def test_source_banner_hidden_before_three_days(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    _fully_onboarded_client(client, monkeypatch)

    response = client.get("/dashboard")
    assert "Where" not in response.text or "hear about Roster" not in response.text


def test_source_banner_shown_after_three_days(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    _fully_onboarded_client(client, monkeypatch)

    with Session(test_engine) as session:
        owner = session.exec(select(Client).where(Client.email == "owner@example.com")).first()
        owner.activated_at = datetime.utcnow() - timedelta(days=4)
        session.add(owner)
        session.commit()

    response = client.get("/dashboard")
    assert "hear about Roster" in response.text


def test_dismissing_source_banner_hides_it_going_forward(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    _fully_onboarded_client(client, monkeypatch)

    with Session(test_engine) as session:
        owner = session.exec(select(Client).where(Client.email == "owner@example.com")).first()
        owner.activated_at = datetime.utcnow() - timedelta(days=4)
        session.add(owner)
        session.commit()

    client.post("/dashboard/source", data={"source": "A friend recommended us"})

    with Session(test_engine) as session:
        owner = session.exec(select(Client).where(Client.email == "owner@example.com")).first()
        assert owner.source == "A friend recommended us"
        assert owner.source_prompt_dismissed is True

    response = client.get("/dashboard")
    assert "hear about Roster" not in response.text
