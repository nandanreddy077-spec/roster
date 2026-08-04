"""Founder wires xAI's free (or BYO) voice number to a pilot shop.

xAI provisions numbers in its console, not via API, so there's nothing to
automate — the founder pastes the number + its webhook signing secret and this
attaches them so an inbound call routes to this business and verifies. This is
what unblocks the first real voice call without Twilio.
"""
from sqlmodel import Session

import app as app_module
import db as db_module
import portal as portal_module
import provisioning as provisioning_module
from conftest import DASH_AUTH
from db_models import Business
from starlette.testclient import TestClient


def _client(test_engine, monkeypatch):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    with Session(test_engine) as s:
        b = Business(business_name="Ridgeline Plumbing", escalation_phone="512-555-0148")
        s.add(b); s.commit(); s.refresh(b)
        bid = b.id
    return TestClient(app_module.app), bid


def test_attach_sets_number_and_secret_and_routes(test_engine, monkeypatch):
    client, bid = _client(test_engine, monkeypatch)

    r = client.post(f"/clients/{bid}/attach-xai-number",
                    data={"xai_phone_number": "+15125550123",
                          "xai_signing_secret": "whsec_abc123"},
                    headers=DASH_AUTH, follow_redirects=False)

    assert r.status_code == 303
    with Session(test_engine) as s:
        b = s.get(Business, bid)
    assert b.xai_phone_number == "+15125550123"
    assert b.xai_signing_secret == "whsec_abc123"
    assert b.inbound_number == "+15125550123"  # defaulted so the number shows/routes


def test_attach_does_not_clobber_existing_inbound_number(test_engine, monkeypatch):
    client, bid = _client(test_engine, monkeypatch)
    with Session(test_engine) as s:
        b = s.get(Business, bid); b.inbound_number = "+15559998888"; s.add(b); s.commit()

    client.post(f"/clients/{bid}/attach-xai-number",
                data={"xai_phone_number": "+15125550123", "xai_signing_secret": "s"},
                headers=DASH_AUTH, follow_redirects=False)

    with Session(test_engine) as s:
        assert s.get(Business, bid).inbound_number == "+15559998888"


def test_attach_ignores_blank_submit(test_engine, monkeypatch):
    client, bid = _client(test_engine, monkeypatch)

    client.post(f"/clients/{bid}/attach-xai-number",
                data={"xai_phone_number": "  ", "xai_signing_secret": "  "},
                headers=DASH_AUTH, follow_redirects=False)

    with Session(test_engine) as s:
        b = s.get(Business, bid)
    assert b.xai_phone_number is None
    assert b.xai_signing_secret is None


def test_attach_requires_admin_auth(test_engine, monkeypatch):
    client, bid = _client(test_engine, monkeypatch)
    r = client.post(f"/clients/{bid}/attach-xai-number",
                    data={"xai_phone_number": "+15125550123", "xai_signing_secret": "s"},
                    follow_redirects=False)
    assert r.status_code == 401


def test_client_detail_offers_retry_when_number_bought_but_voice_incomplete(test_engine, monkeypatch):
    """A real Twilio purchase (twilio_number_sid set) with xAI registration
    still incomplete (xai_phone_number None) must not be a dead end — the
    founder needs the manual attach-xai-number form to retry."""
    client, bid = _client(test_engine, monkeypatch)
    with Session(test_engine) as s:
        b = s.get(Business, bid)
        b.inbound_number = "+15125550199"
        b.twilio_number_sid = "PN_fake_sid"
        s.add(b); s.commit()

    r = client.get(f"/clients/{bid}", headers=DASH_AUTH)

    assert r.status_code == 200
    assert "didn't complete" in r.text
    assert f'action="/clients/{bid}/attach-xai-number"' in r.text


def test_client_detail_does_not_claim_bought_for_manually_typed_number(test_engine, monkeypatch):
    """inbound_number can be typed by hand at business creation (an existing
    business line, for call forwarding) with no Twilio purchase involved
    (twilio_number_sid stays None). The page must not claim a number was
    'bought' in that case — it must still offer the buy/attach forms."""
    client, bid = _client(test_engine, monkeypatch)
    with Session(test_engine) as s:
        b = s.get(Business, bid); b.inbound_number = "+17702881238"; s.add(b); s.commit()

    r = client.get(f"/clients/{bid}", headers=DASH_AUTH)

    assert r.status_code == 200
    assert "bought" not in r.text
    assert "on file" in r.text
    assert f'action="/clients/{bid}/provision-number"' in r.text


def test_retry_xai_registration_succeeds_without_rebuying_number(test_engine, monkeypatch):
    """provision-number always buys a fresh Twilio number -- wasteful to retry
    with when only the xAI half failed. retry-xai-registration must reuse the
    existing purchase instead."""
    client, bid = _client(test_engine, monkeypatch)
    with Session(test_engine) as s:
        b = s.get(Business, bid)
        b.inbound_number = "+16187473488"
        b.twilio_number_sid = "PN_real_sid"
        s.add(b); s.commit()

    def _boom_if_called(*args, **kwargs):
        raise AssertionError("must not re-purchase a number on retry")

    monkeypatch.setattr(app_module, "buy_twilio_number", _boom_if_called)
    # Patched on `provisioning`, not `app`: the route now delegates to
    # provisioning.provision_voice, so this exercises the real ordering
    # (secret persisted before the retryable trunk step) rather than stubbing
    # the whole voice half out of the route.
    monkeypatch.setattr(
        provisioning_module, "register_number_with_xai",
        lambda phone_number: {"signing_secret": "whsec_retry123"},
    )
    monkeypatch.setattr(provisioning_module, "attach_number_to_xai_trunk",
                        lambda sid, phone_number: None)

    r = client.post(f"/clients/{bid}/retry-xai-registration", headers=DASH_AUTH, follow_redirects=False)

    assert r.status_code == 303
    with Session(test_engine) as s:
        b = s.get(Business, bid)
    assert b.xai_phone_number == "+16187473488"
    assert b.xai_signing_secret == "whsec_retry123"
    assert b.inbound_number == "+16187473488"  # unchanged, no new number


def test_retry_xai_registration_requires_an_existing_purchase(test_engine, monkeypatch):
    client, bid = _client(test_engine, monkeypatch)  # no inbound_number/twilio_number_sid

    r = client.post(f"/clients/{bid}/retry-xai-registration", headers=DASH_AUTH)

    assert r.status_code == 400


def test_retry_xai_registration_surfaces_error_without_crashing(test_engine, monkeypatch):
    client, bid = _client(test_engine, monkeypatch)
    with Session(test_engine) as s:
        b = s.get(Business, bid)
        b.inbound_number = "+16187473488"
        b.twilio_number_sid = "PN_real_sid"
        s.add(b); s.commit()

    from provisioning import ProvisioningError

    def _fail(phone_number):
        raise ProvisioningError("xAI registration returned no signing secret — webhook keys: ['url']")

    monkeypatch.setattr(provisioning_module, "register_number_with_xai", _fail)

    r = client.post(f"/clients/{bid}/retry-xai-registration", headers=DASH_AUTH, follow_redirects=False)

    assert r.status_code == 303
    assert "provision_error" in r.headers["location"]
    with Session(test_engine) as s:
        assert s.get(Business, bid).xai_phone_number is None


def test_retry_xai_registration_requires_admin_auth(test_engine, monkeypatch):
    client, bid = _client(test_engine, monkeypatch)
    r = client.post(f"/clients/{bid}/retry-xai-registration", follow_redirects=False)
    assert r.status_code == 401
