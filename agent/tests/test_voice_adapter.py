import json

from sqlmodel import select

from conftest import StubAgent
from db_models import Client, Job, Message


def make_client(session) -> Client:
    client = Client(
        business_name="Test Co",
        trade="HVAC",
        services_json=json.dumps(["AC repair"]),
        hours="9-5",
        pricing_faq="n/a",
        escalation_phone="+15550000000",
    )
    session.add(client)
    session.commit()
    session.refresh(client)
    return client


def test_to_engine_history_drops_system_message():
    from voice_adapter import VapiMessage, _to_engine_history

    messages = [
        VapiMessage(role="system", content="you are an assistant"),
        VapiMessage(role="user", content="hi"),
    ]
    history = _to_engine_history(messages)

    assert len(history) == 1
    assert history[0]["role"] == "user"
    assert history[0]["content"][0]["text"] == "hi"


def test_handle_voice_turn_persists_jobs_and_messages(session):
    from voice_adapter import VapiCall, VapiChatRequest, VapiCustomer, VapiMessage, VapiPhoneNumber, handle_voice_turn

    client = make_client(session)
    stub_result = {
        "reply": "Got it, I've logged that.",
        "jobs": [{"id": "tu_1", "input": {"service_type": "AC repair", "urgency": "routine"}}],
        "new_messages": [{"role": "assistant", "content": [{"type": "text", "text": "Got it, I've logged that."}]}],
        "pending_tool_call": None,
    }
    agent = StubAgent(stub_result)
    request = VapiChatRequest(
        model="claude-sonnet-4-6",
        messages=[VapiMessage(role="user", content="My AC is broken")],
        call=VapiCall(
            id="call_1",
            phoneNumber=VapiPhoneNumber(number="+19998887777"),
            customer=VapiCustomer(number="+15551234567"),
        ),
    )

    result = handle_voice_turn(session, agent, client, request)

    assert result["reply"] == "Got it, I've logged that."
    assert result["pending_tool_call"] is None

    jobs = session.exec(select(Job).where(Job.client_id == client.id)).all()
    assert len(jobs) == 1
    assert jobs[0].callback_number == "+15551234567"

    messages = session.exec(select(Message).where(Message.client_id == client.id)).all()
    roles = [m.role for m in messages]
    assert "user" in roles
    assert "assistant" in roles
