"""The self-serve onboarding wizard is retired (2026-08-06).

It existed only to serve self-registration, which is now closed, so nothing
links here. This file used to test that the wizard worked; it now tests that
it can't run — which is the security property, not a formality.

The wizard's final POST called activate_frontdesk() -> buy_twilio_number(),
and its guards keyed on `frontdesk_live`. That column is False on every
FOUNDER-provisioned business, so a legitimate logged-in owner typing the URL
would have sailed past every guard and triggered a real number purchase for a
shop Roster had already set up by hand. That is the case the last test pins.
"""

import app as app_module
import db as db_module
import portal as portal_module
from conftest import login_as, provisioned_business
from db_models import Business
from fastapi.testclient import TestClient
from sqlmodel import Session


def _wire(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)


def test_every_wizard_route_redirects_to_the_dashboard(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    client = TestClient(app_module.app)
    for path in ("/onboarding/business", "/onboarding/receptionist"):
        for method in ("get", "post"):
            response = getattr(client, method)(path, follow_redirects=False)
            assert response.status_code == 303, f"{method.upper()} {path}"
            assert response.headers["location"] == portal_module.DASHBOARD_HOME


def test_the_wizard_writes_nothing(monkeypatch, test_engine):
    """It used to overwrite business_name/trade/hours/pricing from a form. A
    logged-in owner must not be able to rewrite what the founder captured."""
    _wire(monkeypatch, test_engine)
    business_id = provisioned_business(test_engine)
    client = TestClient(app_module.app)
    login_as(client, business_id)

    client.post(
        "/onboarding/business",
        data={
            "business_name": "Overwritten",
            "trade": "Nope",
            "services": "x",
            "hours": "x",
            "pricing_faq": "x",
        },
    )
    with Session(test_engine) as session:
        assert session.get(Business, business_id).business_name == "Ridgeline Plumbing"


def test_a_provisioned_owner_cannot_trigger_a_number_purchase(monkeypatch, test_engine):
    """The real bug this closes. frontdesk_live is False on a founder-
    provisioned shop, so the wizard's old "already live?" guard waved it
    through to activate_frontdesk() — which buys a Twilio number."""
    _wire(monkeypatch, test_engine)
    business_id = provisioned_business(test_engine, frontdesk_live=False, inbound_number=None)
    client = TestClient(app_module.app)
    login_as(client, business_id)

    def _explode(*a, **kw):
        raise AssertionError("buy_twilio_number must be unreachable from the portal")

    monkeypatch.setattr("activation.buy_twilio_number", _explode)

    response = client.post(
        "/onboarding/receptionist",
        data={"escalation_phone": "5555550101", "answer_mode": "primary"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with Session(test_engine) as session:
        business = session.get(Business, business_id)
        assert business.inbound_number is None
        assert business.frontdesk_live is False
