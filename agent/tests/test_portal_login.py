from fastapi.testclient import TestClient

import app as app_module
import db as db_module
import portal as portal_module


def test_login_form_renders():
    client = TestClient(app_module.app)
    response = client.get("/login")
    assert response.status_code == 200


def test_login_sends_an_unfinished_signup_back_to_onboarding(monkeypatch, test_engine):
    """A bare signup has no business name, no number and nobody deployed. It
    belongs in onboarding, not on an empty dashboard — the old unconditional
    "/dashboard" redirect hid that, because /dashboard did the bouncing."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    client.post("/signup", data={"email": "owner@example.com", "password": "hunter22"})
    client.post("/logout")

    response = client.post(
        "/login", data={"email": "owner@example.com", "password": "hunter22"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/onboarding/business"


def test_login_sends_a_founder_provisioned_shop_to_the_dashboard(monkeypatch, test_engine):
    """The shop Roster set up by hand never ran the onboarding wizard, so
    frontdesk_live is False — but it HAS a deployed employee, and that is what
    decides. Before this, a hand-provisioned owner was bounced into a wizard
    for setup that was already finished."""
    from sqlmodel import Session

    from db_models import Business
    from deployment import deploy_role
    from auth import hash_password

    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    with Session(test_engine) as session:
        business = Business(
            email="hired@example.com", password_hash=hash_password("hunter22"),
            business_name="Ridgeline Plumbing", frontdesk_live=False,
        )
        session.add(business)
        session.commit()
        session.refresh(business)
        deploy_role(session, business.id, "frontdesk")

    client = TestClient(app_module.app)
    response = client.post(
        "/login", data={"email": "hired@example.com", "password": "hunter22"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == portal_module.DASHBOARD_HOME


def test_login_rejects_wrong_password(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    client.post("/signup", data={"email": "owner@example.com", "password": "hunter22"})

    response = client.post("/login", data={"email": "owner@example.com", "password": "wrong"})
    assert response.status_code == 400
    assert "Invalid email or password" in response.text


def test_login_rejects_unknown_email(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    response = client.post("/login", data={"email": "nobody@example.com", "password": "whatever"})
    assert response.status_code == 400
    assert "Invalid email or password" in response.text
