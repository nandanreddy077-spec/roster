"""Self-registration is closed (2026-08-06).

Roster provisions every business through the founder console after a discovery
call. These tests pin that shut, because the open version was not merely
redundant: /signup led into a wizard whose final POST called
activate_frontdesk() -> buy_twilio_number(), so any stranger could make Roster
buy a phone number for a business ops had never heard of."""
from fastapi.testclient import TestClient
from sqlmodel import Session, select

import app as app_module
import db as db_module
import portal as portal_module
from db_models import Business


def _wire(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)


def test_the_signup_page_is_gone(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    client = TestClient(app_module.app)
    response = client.get("/signup", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == portal_module.SIGNUP_CLOSED_REDIRECT


def test_posting_to_signup_creates_nothing(monkeypatch, test_engine):
    """Closed as a ROUTE, not just as a form — a stale bookmark or a scripted
    POST must not be able to create a business either."""
    _wire(monkeypatch, test_engine)
    client = TestClient(app_module.app)
    response = client.post(
        "/signup",
        data={"email": "stranger@example.com", "password": "hunter22"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with Session(test_engine) as session:
        assert session.exec(select(Business)).all() == []


def test_a_stranger_cannot_reach_the_route_that_spends_money(monkeypatch, test_engine):
    """The whole point, end to end: with no way to get a session, there is no
    way to reach the route that buys a Twilio number."""
    _wire(monkeypatch, test_engine)
    client = TestClient(app_module.app)
    client.post("/signup", data={"email": "stranger@example.com", "password": "hunter22"})

    response = client.post(
        "/onboarding/receptionist",
        data={"escalation_phone": "5555550101"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with Session(test_engine) as session:
        assert session.exec(select(Business)).all() == []
