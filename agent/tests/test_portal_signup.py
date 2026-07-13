from fastapi.testclient import TestClient
from sqlmodel import Session, select

import app as app_module
import db as db_module
import portal as portal_module
from db_models import Business


def test_signup_form_renders(monkeypatch):
    client = TestClient(app_module.app)
    response = client.get("/signup")
    assert response.status_code == 200
    assert "Roster" in response.text


def test_signup_creates_client_and_starts_session(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    response = client.post(
        "/signup",
        data={"email": "owner@example.com", "password": "hunter22"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/onboarding/business"

    with Session(test_engine) as session:
        created = session.exec(select(Business).where(Business.email == "owner@example.com")).first()
        assert created is not None
        assert created.password_hash != "hunter22"


def test_signup_rejects_duplicate_email(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    client.post("/signup", data={"email": "owner@example.com", "password": "hunter22"})
    response = client.post("/signup", data={"email": "owner@example.com", "password": "different"})
    assert response.status_code == 400
    assert "already registered" in response.text
