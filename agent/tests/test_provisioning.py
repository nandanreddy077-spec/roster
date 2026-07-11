from unittest.mock import MagicMock

import pytest

import provisioning
from provisioning import (
    ProvisioningError,
    attach_number_to_xai_trunk,
    buy_twilio_number,
    register_number_with_xai,
)


def test_buy_twilio_number_raises_without_credentials(monkeypatch):
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)

    with pytest.raises(ProvisioningError):
        buy_twilio_number()


def test_buy_twilio_number_raises_when_none_available(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC_test")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "token_test")

    fake_client = MagicMock()
    fake_client.available_phone_numbers.return_value.local.list.return_value = []
    monkeypatch.setattr(provisioning, "_twilio_client", lambda: fake_client)

    with pytest.raises(ProvisioningError):
        buy_twilio_number(area_code="415")


def test_buy_twilio_number_purchases_first_available(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC_test")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "token_test")

    available_number = MagicMock(phone_number="+14155550123")
    purchased = MagicMock(phone_number="+14155550123", sid="PN123")

    fake_client = MagicMock()
    fake_client.available_phone_numbers.return_value.local.list.return_value = [available_number]
    fake_client.incoming_phone_numbers.create.return_value = purchased
    monkeypatch.setattr(provisioning, "_twilio_client", lambda: fake_client)

    result = buy_twilio_number(area_code="415")

    assert result == {"phone_number": "+14155550123", "sid": "PN123"}
    fake_client.incoming_phone_numbers.create.assert_called_once_with(
        phone_number="+14155550123",
        sms_url="https://rosterhires.com/webhook/sms",
        sms_method="POST",
    )


def test_buy_twilio_number_uses_public_base_url_override(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC_test")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "token_test")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://staging.example.com")

    available_number = MagicMock(phone_number="+14155550123")
    purchased = MagicMock(phone_number="+14155550123", sid="PN123")

    fake_client = MagicMock()
    fake_client.available_phone_numbers.return_value.local.list.return_value = [available_number]
    fake_client.incoming_phone_numbers.create.return_value = purchased
    monkeypatch.setattr(provisioning, "_twilio_client", lambda: fake_client)

    buy_twilio_number(area_code="415")

    fake_client.incoming_phone_numbers.create.assert_called_once_with(
        phone_number="+14155550123",
        sms_url="https://staging.example.com/webhook/sms",
        sms_method="POST",
    )


def test_attach_number_reuses_existing_trunk(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC_test")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "token_test")

    existing_trunk = MagicMock(friendly_name=provisioning.XAI_TRUNK_FRIENDLY_NAME, sid="TK_existing")
    fake_client = MagicMock()
    fake_client.trunking.v1.trunks.list.return_value = [existing_trunk]
    monkeypatch.setattr(provisioning, "_twilio_client", lambda: fake_client)

    attach_number_to_xai_trunk("PN123", "+14155550123")

    fake_client.trunking.v1.trunks.assert_any_call("TK_existing")
    fake_client.trunking.v1.trunks.return_value.phone_numbers.create.assert_called_once_with(
        phone_number_sid="PN123"
    )
    fake_client.trunking.v1.trunks.create.assert_not_called()
    # xAI's origination URI is per-number (embeds the E.164 number), so it must
    # be added every time a number is attached, not just once at trunk creation.
    fake_client.trunking.v1.trunks.return_value.origination_urls.create.assert_called_once_with(
        friendly_name="xAI Voice Agent API - +14155550123",
        sip_url="sip:+14155550123@sip.voice.x.ai;transport=tls",
        weight=1,
        priority=1,
        enabled=True,
    )


def test_attach_number_creates_trunk_when_none_exists(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC_test")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "token_test")

    new_trunk = MagicMock(sid="TK_new")
    fake_client = MagicMock()
    fake_client.trunking.v1.trunks.list.return_value = []
    fake_client.trunking.v1.trunks.create.return_value = new_trunk
    monkeypatch.setattr(provisioning, "_twilio_client", lambda: fake_client)

    attach_number_to_xai_trunk("PN123", "+14155550123")

    fake_client.trunking.v1.trunks.create.assert_called_once_with(
        friendly_name=provisioning.XAI_TRUNK_FRIENDLY_NAME
    )
    fake_client.trunking.v1.trunks.return_value.phone_numbers.create.assert_called_once_with(
        phone_number_sid="PN123"
    )


def test_register_number_with_xai_is_not_implemented():
    """Deliberate — see provisioning.py's module docstring. This must keep
    raising until the real xAI registration endpoint is confirmed; it must
    never be "implemented" with a guessed URL."""
    with pytest.raises(NotImplementedError):
        register_number_with_xai("+14155550123")
