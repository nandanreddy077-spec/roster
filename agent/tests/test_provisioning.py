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


def test_register_number_with_xai_posts_correct_request(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-test-key")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://rosterhires.com")
    monkeypatch.delenv("XAI_SIP_ALLOWED_ADDRESSES", raising=False)

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"phone_number": "+14155550123", "signing_secret": "whsec_abc123"}
        return resp

    monkeypatch.setattr(provisioning.httpx, "post", fake_post)

    result = register_number_with_xai("+14155550123")

    assert captured["url"] == "https://api.x.ai/v2/phone-numbers"
    assert captured["headers"]["Authorization"] == "Bearer xai-test-key"
    body = captured["json"]
    assert body["origin"] == "byo_trunk"
    assert body["phone_number"] == "+14155550123"
    assert body["webhook"]["url"] == "https://rosterhires.com/webhook/xai-incoming-call"
    # no sip_auth when no allowlist env is configured
    assert "sip_auth" not in body
    assert result["signing_secret"] == "whsec_abc123"


def test_register_number_with_xai_includes_allowlist_when_configured(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-test-key")
    monkeypatch.setenv("XAI_SIP_ALLOWED_ADDRESSES", "54.172.60.0/23, 54.244.51.0/24")

    def fake_post(url, headers=None, json=None, timeout=None):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"signing_secret": "whsec_abc123"}
        return resp

    monkeypatch.setattr(provisioning.httpx, "post", fake_post)

    register_number_with_xai("+14155550123")
    # captured via closure re-post
    calls = {}

    def capture_post(url, headers=None, json=None, timeout=None):
        calls["json"] = json
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"signing_secret": "whsec_abc123"}
        return resp

    monkeypatch.setattr(provisioning.httpx, "post", capture_post)
    register_number_with_xai("+14155550123")
    assert calls["json"]["sip_auth"]["allowed_addresses"] == ["54.172.60.0/23", "54.244.51.0/24"]


def test_register_number_with_xai_raises_without_api_key(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    with pytest.raises(ProvisioningError):
        register_number_with_xai("+14155550123")


def test_register_number_with_xai_raises_when_no_secret_in_response(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-test-key")

    def fake_post(url, headers=None, json=None, timeout=None):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"phone_number": "+14155550123"}  # no secret
        return resp

    monkeypatch.setattr(provisioning.httpx, "post", fake_post)

    with pytest.raises(ProvisioningError):
        register_number_with_xai("+14155550123")
