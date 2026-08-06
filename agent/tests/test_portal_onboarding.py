from fastapi.testclient import TestClient
from sqlmodel import Session

import app as app_module
import db as db_module
import portal as portal_module
from db_models import Business


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
            "pricing_faq": "Diagnostic visit: $89, waived if repaired same day.",
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
    assert saved.pricing_faq == "Diagnostic visit: $89, waived if repaired same day."


def select_client_by_email(email, session):
    from sqlmodel import select
    return session.exec(select(Business).where(Business.email == email)).first()


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
        data={
            "business_name": "Ridgeline Plumbing",
            "trade": "Plumbing",
            "services": "Drains",
            "hours": "9-5",
            "pricing_faq": "Diagnostic visit: $89.",
        },
    )

    response = client.post(
        "/onboarding/receptionist", data={"escalation_phone": "(555) 555-0101"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/activation/live"

    with Session(test_engine) as session:
        saved = select_client_by_email("owner@example.com", session)
    # Stored E.164, not as typed: this is the number we page the owner on when
    # a live call escalates, and Twilio silently resolves a non-E.164 number
    # against the sender's country.
    assert saved.escalation_phone == "+15555550101"
    assert saved.frontdesk_live is True
    assert saved.pricing_faq == "Diagnostic visit: $89."


def test_onboarding_receptionist_saves_answer_mode_and_business_phone(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    client = TestClient(app_module.app)
    _signed_up_client(client)
    client.post(
        "/onboarding/business",
        data={
            "business_name": "Ridgeline Plumbing",
            "trade": "Plumbing",
            "services": "Drains",
            "hours": "9-5",
            "pricing_faq": "Diagnostic visit: $89.",
        },
    )

    client.post(
        "/onboarding/receptionist",
        data={
            "escalation_phone": "(555) 555-0101",
            "answer_mode": "primary",
            "business_phone": "(555) 999-1000",
        },
    )

    with Session(test_engine) as session:
        saved = select_client_by_email("owner@example.com", session)
    assert saved.answer_mode == "primary"
    assert saved.business_phone == "+15559991000"


def test_onboarding_receptionist_defaults_answer_mode_to_backup_on_bad_value(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    client = TestClient(app_module.app)
    _signed_up_client(client)
    client.post(
        "/onboarding/business",
        data={
            "business_name": "Ridgeline Plumbing",
            "trade": "Plumbing",
            "services": "Drains",
            "hours": "9-5",
            "pricing_faq": "Diagnostic visit: $89.",
        },
    )
    client.post(
        "/onboarding/receptionist",
        data={"escalation_phone": "x", "answer_mode": "garbage"},
    )
    with Session(test_engine) as session:
        saved = select_client_by_email("owner@example.com", session)
    assert saved.answer_mode == "backup"


def test_onboarding_business_requires_pricing_faq(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    _signed_up_client(client)

    response = client.post(
        "/onboarding/business",
        data={"business_name": "Ridgeline Plumbing", "trade": "Plumbing", "services": "Drains", "hours": "9-5"},
    )
    assert response.status_code == 422
