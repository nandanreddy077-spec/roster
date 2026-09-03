"""Roster spends real money buying Twilio numbers, so no code path may reach
buy_twilio_number for a business that has neither a verified payment method nor
an explicit founder unlock (docs/PRODUCTION_READINESS.md P1-1).

This is the gate that lets self-serve /signup reopen without reopening the
abuse vector that closed it on 2026-08-06 (a stranger making Roster spend
money). Founder-only route today; the self-serve wizard will call the same
buy path.
"""

from datetime import datetime

import app as app_module
import db as db_module
import portal as portal_module
import pytest
from conftest import DASH_AUTH, provisioned_business
from db_models import Business
from fastapi.testclient import TestClient
from provisioning import provisioning_allowed
from sqlmodel import Session


def _biz(**kw) -> Business:
    return Business(business_name="Kestrel HVAC", trade="HVAC", **kw)


# ---- provisioning_allowed(), in isolation --------------------------------


def test_a_fresh_business_may_not_provision():
    assert provisioning_allowed(_biz()) is False


def test_a_verified_payment_method_allows_provisioning():
    assert provisioning_allowed(_biz(payment_method_verified_at=datetime.utcnow())) is True


def test_a_founder_unlock_allows_provisioning():
    assert provisioning_allowed(_biz(provisioning_unlocked_at=datetime.utcnow())) is True


# ---- the route enforces it ----------------------------------------------


def _wire(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)


@pytest.fixture
def no_buy(monkeypatch):
    """buy_twilio_number must never be called in these tests."""

    def _boom(*a, **k):
        raise AssertionError("no Twilio number may be bought without a gate")

    monkeypatch.setattr(app_module, "buy_twilio_number", _boom)


def test_provision_number_is_refused_without_payment_or_unlock(monkeypatch, test_engine, no_buy):
    """The brief's Phase 0 acceptance test, built now: provisioning cannot fire
    without a gate, even when every other field is valid."""
    _wire(monkeypatch, test_engine)
    bid = provisioned_business(test_engine)  # full config, no payment, no unlock

    resp = TestClient(app_module.app).post(
        f"/clients/{bid}/provision-number",
        data={"area_code": "512"},
        headers=DASH_AUTH,
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "provision_error" in resp.headers["location"]
    with Session(test_engine) as s:
        b = s.get(Business, bid)
        assert b.twilio_number_sid is None
        assert b.provisioning_started_at is None  # not even claimed


def test_unlock_provisioning_sets_the_timestamp_and_is_idempotent(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    bid = provisioned_business(test_engine)
    client = TestClient(app_module.app)

    client.post(f"/clients/{bid}/unlock-provisioning", headers=DASH_AUTH, follow_redirects=False)
    with Session(test_engine) as s:
        first = s.get(Business, bid).provisioning_unlocked_at
    assert first is not None

    client.post(f"/clients/{bid}/unlock-provisioning", headers=DASH_AUTH, follow_redirects=False)
    with Session(test_engine) as s:
        assert s.get(Business, bid).provisioning_unlocked_at == first  # unchanged


def test_unlock_then_provision_is_allowed(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    monkeypatch.setattr(
        app_module,
        "buy_twilio_number",
        lambda area_code=None: {"phone_number": "+15125550111", "sid": "PN9"},
    )
    monkeypatch.setattr(app_module, "provision_voice", lambda *a, **k: None)

    bid = provisioned_business(test_engine)
    client = TestClient(app_module.app)
    client.post(f"/clients/{bid}/unlock-provisioning", headers=DASH_AUTH, follow_redirects=False)
    client.post(
        f"/clients/{bid}/provision-number",
        data={"area_code": "512"},
        headers=DASH_AUTH,
        follow_redirects=False,
    )
    with Session(test_engine) as s:
        assert s.get(Business, bid).twilio_number_sid == "PN9"


def test_a_verified_payment_method_also_allows_provisioning(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    monkeypatch.setattr(
        app_module,
        "buy_twilio_number",
        lambda area_code=None: {"phone_number": "+15125550122", "sid": "PN8"},
    )
    monkeypatch.setattr(app_module, "provision_voice", lambda *a, **k: None)

    bid = provisioned_business(test_engine, payment_method_verified_at=datetime.utcnow())
    TestClient(app_module.app).post(
        f"/clients/{bid}/provision-number",
        data={"area_code": "512"},
        headers=DASH_AUTH,
        follow_redirects=False,
    )
    with Session(test_engine) as s:
        assert s.get(Business, bid).twilio_number_sid == "PN8"


def test_unlock_provisioning_on_a_missing_client_is_404(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    r = TestClient(app_module.app).post(
        "/clients/424242/unlock-provisioning", headers=DASH_AUTH, follow_redirects=False
    )
    assert r.status_code == 404
