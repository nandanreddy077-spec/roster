"""Request-access funnel: the landing's only conversion path while Twilio KYC
is pending. A home-service owner fills the form, we store the lead and the
founder follows up + hand-onboards. Replaces self-serve signup + phone CTAs."""

import app as app_module
import db as db_module
import portal as portal_module
from conftest import DASH_AUTH
from db_models import AccessRequest
from sqlmodel import Session, select
from starlette.testclient import TestClient


def _client(test_engine, monkeypatch):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    return TestClient(app_module.app)


def test_request_access_stores_lead_and_thanks(test_engine, monkeypatch):
    client = _client(test_engine, monkeypatch)

    r = client.post(
        "/request-access",
        data={
            "name": "Sam Rivera",
            "business_name": "Ridgeline Plumbing",
            "phone": "512-555-0148",
            "trade": "Plumbing",
        },
        follow_redirects=False,
    )

    assert r.status_code == 303 and r.headers["location"] == "/thanks"
    with Session(test_engine) as s:
        leads = s.exec(select(AccessRequest)).all()
    assert len(leads) == 1
    assert leads[0].name == "Sam Rivera"
    assert leads[0].business_name == "Ridgeline Plumbing"
    assert leads[0].phone == "512-555-0148"
    assert leads[0].trade == "Plumbing"


def test_thanks_page_renders(test_engine, monkeypatch):
    client = _client(test_engine, monkeypatch)
    r = client.get("/thanks")
    assert r.status_code == 200
    assert "reach out" in r.text.lower() or "in touch" in r.text.lower()


def test_business_name_optional(test_engine, monkeypatch):
    client = _client(test_engine, monkeypatch)
    r = client.post(
        "/request-access",
        data={"name": "Pat", "phone": "512-555-0100", "trade": "Roofing"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    with Session(test_engine) as s:
        assert s.exec(select(AccessRequest)).first().business_name == ""


def test_request_appears_in_founder_dashboard(test_engine, monkeypatch):
    client = _client(test_engine, monkeypatch)
    client.post(
        "/request-access",
        data={
            "name": "Dana Lee",
            "business_name": "Lee HVAC",
            "phone": "901-555-7777",
            "trade": "HVAC",
        },
        follow_redirects=False,
    )

    r = client.get("/clients", headers=DASH_AUTH)
    assert r.status_code == 200
    assert "Dana Lee" in r.text
    assert "901-555-7777" in r.text
    assert "Lee HVAC" in r.text


def test_request_access_is_public_no_auth_needed(test_engine, monkeypatch):
    """The form must be reachable by anonymous visitors (it's on the landing)."""
    client = _client(test_engine, monkeypatch)
    r = client.post(
        "/request-access", data={"name": "X", "phone": "1", "trade": "HVAC"}, follow_redirects=False
    )
    assert r.status_code == 303  # not 401/403
