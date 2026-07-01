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
