from datetime import datetime

from sqlmodel import Session

from activation import activate_frontdesk
from db_models import Client


def test_activate_frontdesk_marks_live_even_without_twilio_creds(test_engine, monkeypatch):
    """No TWILIO_ACCOUNT_SID/TOKEN in dev/test — activation must still
    complete (SMS/voice number can be provisioned later by the founder),
    matching the tolerance the existing /clients/{id}/provision-number route
    already has for ProvisioningError."""
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)

    with Session(test_engine) as session:
        client = Client(email="owner@example.com", password_hash="x", business_name="Ridgeline")
        session.add(client)
        session.commit()
        session.refresh(client)

        activate_frontdesk(session, client)

        assert client.frontdesk_live is True
        assert isinstance(client.activated_at, datetime)
        assert client.inbound_number is None  # no creds → nothing purchased, but still live


def test_activate_frontdesk_skips_provisioning_if_number_already_set(test_engine):
    with Session(test_engine) as session:
        client = Client(
            email="owner@example.com", password_hash="x", business_name="Ridgeline",
            inbound_number="+15550001111",
        )
        session.add(client)
        session.commit()
        session.refresh(client)

        activate_frontdesk(session, client)

        assert client.inbound_number == "+15550001111"
        assert client.frontdesk_live is True
