"""Twilio webhook authenticity: when TWILIO_AUTH_TOKEN is configured, inbound
Twilio webhooks must carry a valid X-Twilio-Signature or be rejected — so a
spoofed request can't drive the AI or trigger outbound SMS. In dev (no token)
the endpoints stay open, since there's no real Twilio traffic to forge."""

from sqlmodel import Session
from starlette.testclient import TestClient
from twilio.request_validator import RequestValidator

import app as app_module
import db as db_module
import portal as portal_module
import service
from db_models import Business

TOKEN = "test-auth-token-abc123"
BASE = "https://rosterhires.com"


def _app(test_engine, monkeypatch):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    monkeypatch.setenv("PUBLIC_BASE_URL", BASE)

    class QuietAgent:
        def respond(self, *a, **k):
            return {"reply": "Got it!", "jobs": [], "new_messages": [], "pending_tool_call": None}

    monkeypatch.setattr(service, "agent", QuietAgent())
    with Session(test_engine) as s:
        s.add(
            Business(business_name="Ridgeline", inbound_number="+15125550100", frontdesk_live=True)
        )
        s.commit()
    return TestClient(app_module.app)


def _sign(path, params):
    return RequestValidator(TOKEN).compute_signature(f"{BASE}{path}", params)


def test_sms_rejected_without_signature_when_token_set(test_engine, monkeypatch):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", TOKEN)
    client = _app(test_engine, monkeypatch)
    r = client.post(
        "/webhook/sms",
        data={"From": "+15550001111", "To": "+15125550100", "Body": "hi", "MessageSid": "SM1"},
    )
    assert r.status_code == 403


def test_sms_rejected_with_wrong_signature(test_engine, monkeypatch):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", TOKEN)
    client = _app(test_engine, monkeypatch)
    r = client.post(
        "/webhook/sms",
        data={"From": "+15550001111", "To": "+15125550100", "Body": "hi", "MessageSid": "SM1"},
        headers={"X-Twilio-Signature": "definitely-not-valid"},
    )
    assert r.status_code == 403


def test_sms_accepted_with_valid_signature(test_engine, monkeypatch):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", TOKEN)
    client = _app(test_engine, monkeypatch)
    params = {"From": "+15550001111", "To": "+15125550100", "Body": "hi", "MessageSid": "SM1"}
    r = client.post(
        "/webhook/sms", data=params, headers={"X-Twilio-Signature": _sign("/webhook/sms", params)}
    )
    assert r.status_code == 200
    assert "Got it!" in r.text


def test_sms_open_when_no_token_configured(test_engine, monkeypatch):
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    client = _app(test_engine, monkeypatch)
    r = client.post(
        "/webhook/sms",
        data={"From": "+15550001111", "To": "+15125550100", "Body": "hi", "MessageSid": "SM1"},
    )
    assert r.status_code == 200


def test_voice_status_rejects_bad_signature(test_engine, monkeypatch):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", TOKEN)
    client = _app(test_engine, monkeypatch)
    r = client.post(
        "/webhook/voice-status",
        data={"From": "+15550001111", "To": "+15125550100", "CallStatus": "no-answer"},
        headers={"X-Twilio-Signature": "nope"},
    )
    assert r.status_code == 403


def test_voice_status_accepts_valid_signature(test_engine, monkeypatch):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", TOKEN)
    client = _app(test_engine, monkeypatch)
    params = {"From": "+15550001111", "To": "+15125550100", "CallStatus": "completed"}
    r = client.post(
        "/webhook/voice-status",
        data=params,
        headers={"X-Twilio-Signature": _sign("/webhook/voice-status", params)},
    )
    assert r.status_code == 204


# ---- Production must fail closed when TWILIO_AUTH_TOKEN is missing (2026-08-10 pentest, VULN-0001)


def test_sms_rejected_when_no_token_configured_in_production(test_engine, monkeypatch):
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("ROSTER_ENV", "production")
    client = _app(test_engine, monkeypatch)
    r = client.post(
        "/webhook/sms",
        data={"From": "+15550001111", "To": "+15125550100", "Body": "hi", "MessageSid": "SM1"},
    )
    assert r.status_code == 403


def test_voice_status_rejected_when_no_token_configured_in_production(test_engine, monkeypatch):
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("ROSTER_ENV", "production")
    client = _app(test_engine, monkeypatch)
    r = client.post(
        "/webhook/voice-status",
        data={"From": "+15550001111", "To": "+15125550100", "CallStatus": "no-answer"},
    )
    assert r.status_code == 403


def test_sms_rejected_with_token_set_but_signature_header_missing(test_engine, monkeypatch):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", TOKEN)
    client = _app(test_engine, monkeypatch)
    r = client.post(
        "/webhook/sms",
        data={"From": "+15550001111", "To": "+15125550100", "Body": "hi", "MessageSid": "SM1"},
    )
    assert r.status_code == 403


# ---- /webhook/voice-status replay: CallSid must be claimed once (2026-08-10 pentest, VULN-0002)


def test_voice_status_replay_sends_missed_call_text_once(test_engine, monkeypatch):
    sent = []
    client = _app(test_engine, monkeypatch)
    monkeypatch.setattr(
        app_module,
        "sms_channel",
        type("S", (), {"send": staticmethod(lambda **kw: sent.append(kw))})(),
    )
    form = {
        "From": "+15550001111",
        "To": "+15125550100",
        "CallStatus": "no-answer",
        "CallSid": "CAdup1",
    }

    r1 = client.post("/webhook/voice-status", data=form)
    r2 = client.post("/webhook/voice-status", data=form)

    assert r1.status_code == 204 and r2.status_code == 204
    assert len(sent) == 1


def test_voice_status_missing_call_sid_rejected(test_engine, monkeypatch):
    client = _app(test_engine, monkeypatch)
    r = client.post(
        "/webhook/voice-status",
        data={"From": "+15550001111", "To": "+15125550100", "CallStatus": "no-answer"},
    )
    assert r.status_code == 400
