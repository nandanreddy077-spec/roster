import json

from fastapi.testclient import TestClient
from sqlmodel import Session

import app as app_module
from conftest import StubAgent
from db_models import Client


def test_voice_endpoint_unknown_number_returns_fallback(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client = TestClient(app_module.app)

    payload = {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "hello"}],
        "call": {
            "id": "call_1",
            "phoneNumber": {"number": "+10000000000"},
            "customer": {"number": "+15551234567"},
        },
    }
    response = client.post("/voice/chat/completions", json=payload)

    assert response.status_code == 200
    assert "isn't set up yet" in response.text


def test_voice_endpoint_known_client_streams_reply(monkeypatch, test_engine):
    with Session(test_engine) as session:
        session.add(
            Client(
                business_name="Test Co",
                trade="HVAC",
                services_json=json.dumps(["AC repair"]),
                hours="9-5",
                pricing_faq="n/a",
                escalation_phone="+15550000000",
                inbound_number="+10000000000",
            )
        )
        session.commit()

    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(
        app_module,
        "shared_agent",
        StubAgent(
            {
                "reply": "Sure, what's the issue?",
                "jobs": [],
                "new_messages": [
                    {"role": "assistant", "content": [{"type": "text", "text": "Sure, what's the issue?"}]}
                ],
                "pending_tool_call": None,
            }
        ),
    )

    test_client = TestClient(app_module.app)
    payload = {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "My AC broke"}],
        "call": {
            "id": "call_2",
            "phoneNumber": {"number": "+10000000000"},
            "customer": {"number": "+15551234567"},
        },
    }
    response = test_client.post("/voice/chat/completions", json=payload)

    assert response.status_code == 200
    assert "Sure, what's the issue?" in response.text


def test_voice_endpoint_speaks_reply_before_transfer(monkeypatch, test_engine):
    with Session(test_engine) as session:
        session.add(
            Client(
                business_name="Test Co",
                trade="HVAC",
                services_json=json.dumps(["AC repair"]),
                hours="9-5",
                pricing_faq="n/a",
                escalation_phone="+15550000000",
                inbound_number="+10000000000",
            )
        )
        session.commit()

    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(
        app_module,
        "shared_agent",
        StubAgent(
            {
                "reply": "Let me get someone on the line for you.",
                "jobs": [],
                "new_messages": [
                    {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Let me get someone on the line for you."}],
                    }
                ],
                "pending_tool_call": {"name": "transfer_call", "input": {"destination": "+15550000000"}},
            }
        ),
    )

    test_client = TestClient(app_module.app)
    payload = {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "I want to file a complaint"}],
        "call": {
            "id": "call_4",
            "phoneNumber": {"number": "+10000000000"},
            "customer": {"number": "+15551234567"},
        },
    }
    response = test_client.post("/voice/chat/completions", json=payload)

    assert response.status_code == 200
    assert "Let me get someone on the line for you." in response.text
    assert "transfer_call" in response.text


class RaisingAgent:
    def respond(self, *args, **kwargs):
        raise RuntimeError("boom")


def _voice_payload(call_id: str) -> dict:
    return {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "hello"}],
        "call": {
            "id": call_id,
            "phoneNumber": {"number": "+10000000000"},
            "customer": {"number": "+15551234567"},
        },
    }


def test_voice_endpoint_rejects_missing_header_when_secret_configured(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setenv("VAPI_SHARED_SECRET", "test-secret-value")

    test_client = TestClient(app_module.app)
    response = test_client.post("/voice/chat/completions", json=_voice_payload("call_auth_1"))

    assert response.status_code == 401


def test_voice_endpoint_rejects_wrong_header_when_secret_configured(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setenv("VAPI_SHARED_SECRET", "test-secret-value")

    test_client = TestClient(app_module.app)
    response = test_client.post(
        "/voice/chat/completions",
        json=_voice_payload("call_auth_2"),
        headers={"Authorization": "Bearer wrong-value"},
    )

    assert response.status_code == 401


def test_voice_endpoint_accepts_correct_header_when_secret_configured(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setenv("VAPI_SHARED_SECRET", "test-secret-value")

    test_client = TestClient(app_module.app)
    response = test_client.post(
        "/voice/chat/completions",
        json=_voice_payload("call_auth_3"),
        headers={"Authorization": "Bearer test-secret-value"},
    )

    assert response.status_code == 200
    assert "isn't set up yet" in response.text


def test_voice_endpoint_engine_error_falls_back_to_transfer(monkeypatch, test_engine):
    with Session(test_engine) as session:
        session.add(
            Client(
                business_name="Test Co",
                trade="HVAC",
                services_json=json.dumps(["AC repair"]),
                hours="9-5",
                pricing_faq="n/a",
                escalation_phone="+15559990000",
                inbound_number="+10000000000",
            )
        )
        session.commit()

    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(app_module, "shared_agent", RaisingAgent())

    test_client = TestClient(app_module.app)
    payload = {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "My AC broke"}],
        "call": {
            "id": "call_3",
            "phoneNumber": {"number": "+10000000000"},
            "customer": {"number": "+15551234567"},
        },
    }
    response = test_client.post("/voice/chat/completions", json=payload)

    assert response.status_code == 200
    assert "transfer_call" in response.text
    assert "+15559990000" in response.text
