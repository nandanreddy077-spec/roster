from fastapi.testclient import TestClient
from sqlmodel import Session

import app as app_module
import db as db_module
import portal as portal_module
from db_models import Client


def _signed_up_client(client: TestClient):
    client.post("/signup", data={"email": "owner@example.com", "password": "hunter22"})


def test_onboarding_business_requires_login(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    response = client.get("/onboarding/business", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_onboarding_business_saves_and_redirects(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    _signed_up_client(client)

    response = client.post(
        "/onboarding/business",
        data={
            "business_name": "Ridgeline Plumbing",
            "trade": "Plumbing",
            "services": "Drains, water heaters",
            "hours": "Mon-Sat 7am-7pm",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/onboarding/receptionist"

    with Session(test_engine) as session:
        saved = select_client_by_email("owner@example.com", session)
    assert saved.business_name == "Ridgeline Plumbing"
    assert saved.trade == "Plumbing"
    assert saved.services == ["Drains", "water heaters"]
    assert saved.hours == "Mon-Sat 7am-7pm"


def select_client_by_email(email, session):
    from sqlmodel import select
    return session.exec(select(Client).where(Client.email == email)).first()


def test_onboarding_receptionist_redirects_to_business_if_not_done(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    _signed_up_client(client)

    response = client.get("/onboarding/receptionist", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/onboarding/business"


def test_onboarding_receptionist_activates_and_redirects(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    client = TestClient(app_module.app)
    _signed_up_client(client)
    client.post(
        "/onboarding/business",
        data={"business_name": "Ridgeline Plumbing", "trade": "Plumbing", "services": "Drains", "hours": "9-5"},
    )

    response = client.post(
        "/onboarding/receptionist", data={"escalation_phone": "(555) 555-0101"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/activation/live"

    with Session(test_engine) as session:
        saved = select_client_by_email("owner@example.com", session)
    assert saved.escalation_phone == "(555) 555-0101"
    assert saved.frontdesk_live is True
