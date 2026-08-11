import app as app_module
import db as db_module
import portal as portal_module
from conftest import login_as, provisioned_business
from fastapi.testclient import TestClient


def _live_client(client: TestClient, test_engine):
    """A shop the founder provisioned and activated. Built directly now that
    the self-serve wizard is retired — there is no flow to walk."""
    business_id = provisioned_business(
        test_engine, frontdesk_live=True, inbound_number="+15125550123"
    )
    login_as(client, business_id)
    return business_id


def test_activation_live_redirects_if_not_yet_live(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    login_as(client, provisioned_business(test_engine, frontdesk_live=False))

    response = client.get("/activation/live", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/onboarding/business"


def test_activation_live_shows_role_name_and_checklist(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    client = TestClient(app_module.app)
    _live_client(client, test_engine)

    response = client.get("/activation/live")
    assert response.status_code == 200
    assert "Receptionist" in response.text  # Plumbing -> "Receptionist" per roles.py
    assert "Ridgeline Plumbing" in response.text
    assert "Answer" in response.text


def test_activation_live_shows_connect_your_line(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    client = TestClient(app_module.app)
    _live_client(client, test_engine)

    response = client.get("/activation/live")
    assert response.status_code == 200
    # The forwarding step is how calls actually reach the receptionist — it must
    # be surfaced. No Twilio creds in test → no inbound_number → the
    # "we're setting up your number, here's what forwarding will do" variant.
    assert "Connect your line" in response.text
    assert "forward" in response.text.lower()
