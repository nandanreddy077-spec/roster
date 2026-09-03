"""Roster buys Twilio numbers, so a concurrent double-submit must buy exactly
one (docs/PRODUCTION_READINESS.md P0-1).

The old guard — `if client.twilio_number_sid: return` — is a read-then-act
check that only catches the SEQUENTIAL repeat (a refresh, or a click after the
first finished). Two requests in flight at once both read None and both call
buy_twilio_number(). FastAPI runs sync handlers in a threadpool even at
--workers 1, so the race is real. claim_provisioning() is the atomic fix.
"""

from datetime import datetime, timedelta

import app as app_module
import db as db_module
import portal as portal_module
import pytest
from conftest import DASH_AUTH, provisioned_business
from db_models import Business
from fastapi.testclient import TestClient
from provisioning import _PROVISIONING_CLAIM_TTL, claim_provisioning
from sqlmodel import Session


@pytest.fixture
def biz(session):
    b = Business(business_name="Kestrel HVAC", trade="HVAC")
    session.add(b)
    session.commit()
    session.refresh(b)
    return b


# ---- the claim, in isolation ----------------------------------------------


def test_first_claim_wins_and_the_second_loses(session, biz):
    assert claim_provisioning(session, biz.id) is True
    assert claim_provisioning(session, biz.id) is False


def test_a_business_that_already_has_a_number_cannot_be_claimed(session, biz):
    biz.twilio_number_sid = "PNalready"
    session.add(biz)
    session.commit()
    assert claim_provisioning(session, biz.id) is False


def test_a_fresh_claim_blocks_re_claim(session, biz):
    biz.provisioning_started_at = datetime.utcnow() - timedelta(seconds=30)
    session.add(biz)
    session.commit()
    assert claim_provisioning(session, biz.id) is False


def test_an_abandoned_claim_with_no_number_is_re_claimable(session, biz):
    # A process that died mid-purchase. Wedging provisioning forever is worse
    # than the small window where a genuinely-slow request is double-run.
    biz.provisioning_started_at = datetime.utcnow() - _PROVISIONING_CLAIM_TTL - timedelta(minutes=1)
    session.add(biz)
    session.commit()
    assert claim_provisioning(session, biz.id) is True


def test_claiming_an_unknown_business_is_false_not_an_error(session):
    assert claim_provisioning(session, 999999) is False


# ---- the route: buy exactly once -----------------------------------------


def _wire(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)


def test_provision_number_buys_once_across_repeated_posts(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    calls = {"n": 0}

    def _buy(area_code=None):
        calls["n"] += 1
        return {"phone_number": f"+1512555010{calls['n']}", "sid": f"PN{calls['n']}"}

    monkeypatch.setattr(app_module, "buy_twilio_number", _buy)
    monkeypatch.setattr(app_module, "provision_voice", lambda *a, **k: None)

    bid = provisioned_business(test_engine, provisioning_unlocked_at=datetime.utcnow())
    client = TestClient(app_module.app)
    for _ in range(4):
        client.post(
            f"/clients/{bid}/provision-number",
            data={"area_code": "512"},
            headers=DASH_AUTH,
            follow_redirects=False,
        )

    assert calls["n"] == 1
    with Session(test_engine) as s:
        b = s.get(Business, bid)
        assert b.twilio_number_sid == "PN1"
        assert b.inbound_number == "+15125550101"


def test_a_failed_purchase_releases_the_claim_for_retry(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    from provisioning import ProvisioningError

    attempts = {"n": 0}

    def _flaky_buy(area_code=None):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ProvisioningError("no numbers available for 512")
        return {"phone_number": "+15125550199", "sid": "PN9"}

    monkeypatch.setattr(app_module, "buy_twilio_number", _flaky_buy)
    monkeypatch.setattr(app_module, "provision_voice", lambda *a, **k: None)

    bid = provisioned_business(test_engine, provisioning_unlocked_at=datetime.utcnow())
    client = TestClient(app_module.app)

    r1 = client.post(
        f"/clients/{bid}/provision-number",
        data={"area_code": "512"},
        headers=DASH_AUTH,
        follow_redirects=False,
    )
    assert "provision_error" in r1.headers["location"]
    with Session(test_engine) as s:
        assert s.get(Business, bid).provisioning_started_at is None  # claim released

    # Retry succeeds immediately — not blocked by a stale claim.
    client.post(
        f"/clients/{bid}/provision-number",
        data={"area_code": "512"},
        headers=DASH_AUTH,
        follow_redirects=False,
    )
    with Session(test_engine) as s:
        assert s.get(Business, bid).twilio_number_sid == "PN9"
    assert attempts["n"] == 2


def test_provision_number_on_a_missing_client_is_404(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    client = TestClient(app_module.app)
    r = client.post(
        "/clients/424242/provision-number",
        data={"area_code": "512"},
        headers=DASH_AUTH,
        follow_redirects=False,
    )
    assert r.status_code == 404
