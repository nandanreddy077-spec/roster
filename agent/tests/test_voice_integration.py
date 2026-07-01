import json

from sqlmodel import select

from conftest import StubAgent
from db_models import Client, Job, Message
from voice_adapter import VapiCall, VapiChatRequest, VapiCustomer, VapiMessage, VapiPhoneNumber, handle_voice_turn


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


def test_job_logged_during_voice_turn_persists_under_voice_thread(session):
    client = make_client(session)
    caller_number = "+15551234567"
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
            customer=VapiCustomer(number=caller_number),
        ),
    )

    handle_voice_turn(session, agent, client, request)

    jobs = session.exec(select(Job).where(Job.client_id == client.id)).all()
    assert len(jobs) == 1
    assert jobs[0].customer_phone.startswith("voice:")
    assert jobs[0].customer_phone == f"voice:{caller_number}"
    # Not persisted under the dashboard thread.
    assert jobs[0].customer_phone != "dashboard"


def test_multi_turn_voice_conversation_accumulates_messages_without_loss_or_dup(session):
    client = make_client(session)
    caller_number = "+15551234567"

    # --- Turn 1 ---
    turn1_stub = StubAgent(
        {
            "reply": "Sure, what's going on with it?",
            "jobs": [],
            "new_messages": [
                {"role": "assistant", "content": [{"type": "text", "text": "Sure, what's going on with it?"}]}
            ],
            "pending_tool_call": None,
        }
    )
    turn1_request = VapiChatRequest(
        model="claude-sonnet-4-6",
        messages=[VapiMessage(role="user", content="My AC is broken")],
        call=VapiCall(
            id="call_1",
            phoneNumber=VapiPhoneNumber(number="+19998887777"),
            customer=VapiCustomer(number=caller_number),
        ),
    )
    handle_voice_turn(session, turn1_stub, client, turn1_request)

    # --- Turn 2 --- Vapi resends the full transcript so far, plus the new user line.
    turn2_stub = StubAgent(
        {
            "reply": "Got it, someone will be out today.",
            "jobs": [{"id": "tu_2", "input": {"service_type": "AC repair", "urgency": "same_day"}}],
            "new_messages": [
                {"role": "assistant", "content": [{"type": "text", "text": "Got it, someone will be out today."}]}
            ],
            "pending_tool_call": None,
        }
    )
    turn2_request = VapiChatRequest(
        model="claude-sonnet-4-6",
        messages=[
            VapiMessage(role="user", content="My AC is broken"),
            VapiMessage(role="assistant", content="Sure, what's going on with it?"),
            VapiMessage(role="user", content="It's in the living room, blowing warm air"),
        ],
        call=VapiCall(
            id="call_1",
            phoneNumber=VapiPhoneNumber(number="+19998887777"),
            customer=VapiCustomer(number=caller_number),
        ),
    )
    handle_voice_turn(session, turn2_stub, client, turn2_request)

    thread = f"voice:{caller_number}"
    messages = session.exec(
        select(Message).where(Message.client_id == client.id, Message.customer_phone == thread).order_by(Message.id)
    ).all()

    texts = [json.loads(m.content_json) if m.role == "user" else m.content_json for m in messages]
    roles = [m.role for m in messages]

    # Exactly 4 rows: turn1 user + turn1 assistant + turn2 user + turn2 assistant.
    # No duplication of turn 1's messages when turn 2 resends the full transcript.
    assert roles == ["user", "assistant", "user", "assistant"]

    user_rows = [m for m in messages if m.role == "user"]
    assert json.loads(user_rows[0].content_json) == "My AC is broken"
    assert json.loads(user_rows[1].content_json) == "It's in the living room, blowing warm air"

    assistant_rows = [m for m in messages if m.role == "assistant"]
    assert len(assistant_rows) == 2

    jobs = session.exec(select(Job).where(Job.client_id == client.id)).all()
    assert len(jobs) == 1
    assert jobs[0].customer_phone == thread
