"""Adapter that lets Vapi's Custom LLM integration drive the same AgentEngine
that answers SMS. Vapi speaks an OpenAI-compatible chat format; this module
translates between that and AgentEngine.respond().
"""
import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel
from sqlmodel import Session

from db_models import Client, Job, Message
from engine import LOG_JOB_TOOL, TRANSFER_CALL_TOOL, build_voice_system_prompt

VOICE_THREAD_PREFIX = "voice:"
VOICE_MAX_ITERS = 2  # tighter than the SMS agent's 4 - every round-trip adds live latency


class VapiMessage(BaseModel):
    role: str
    content: str = ""


class VapiCustomer(BaseModel):
    number: Optional[str] = None


class VapiPhoneNumber(BaseModel):
    number: Optional[str] = None


class VapiCall(BaseModel):
    id: str
    phoneNumber: Optional[VapiPhoneNumber] = None
    customer: Optional[VapiCustomer] = None


class VapiChatRequest(BaseModel):
    model: str
    messages: List[VapiMessage]
    call: VapiCall
    customer: Optional[VapiCustomer] = None


def _thread_id(caller_number: str) -> str:
    return f"{VOICE_THREAD_PREFIX}{caller_number}"


def _to_engine_history(vapi_messages: List[VapiMessage]) -> List[Dict[str, Any]]:
    """Vapi resends the full transcript each turn as plain {role, content} pairs;
    convert to the content-block shape AgentEngine expects, dropping the system
    message (we build our own system prompt from the Client config instead)."""
    history = []
    for m in vapi_messages:
        if m.role == "system":
            continue
        history.append({"role": m.role, "content": [{"type": "text", "text": m.content}]})
    return history


def handle_voice_turn(session: Session, agent: Any, client: Client, request: VapiChatRequest) -> Dict[str, Any]:
    """Run one voice turn through the agent and persist it like any other channel.

    Returns {"reply": str, "pending_tool_call": dict | None}.
    """
    caller_number = (request.call.customer.number if request.call.customer else None) or "unknown"
    thread = _thread_id(caller_number)

    history = _to_engine_history(request.messages)
    system_prompt = build_voice_system_prompt(client.to_config())

    if history and history[-1]["role"] == "user":
        session.add(
            Message(
                client_id=client.id,
                customer_phone=thread,
                role="user",
                content_json=json.dumps(history[-1]["content"][0]["text"]),
            )
        )

    result = agent.respond(
        client.to_config(),
        history,
        tools=[LOG_JOB_TOOL, TRANSFER_CALL_TOOL],
        system_prompt=system_prompt,
        max_iters=VOICE_MAX_ITERS,
    )

    for nm in result["new_messages"]:
        session.add(
            Message(
                client_id=client.id,
                customer_phone=thread,
                role=nm["role"],
                content_json=json.dumps(nm["content"]),
            )
        )

    for call in result["jobs"]:
        ji = call["input"]
        session.add(
            Job(
                client_id=client.id,
                customer_phone=thread,
                customer_name=ji.get("customer_name"),
                service_type=ji["service_type"],
                urgency=ji["urgency"],
                address=ji.get("address"),
                callback_number=ji.get("callback_number") or caller_number,
                notes=ji.get("notes"),
            )
        )

    session.commit()
    return {"reply": result["reply"], "pending_tool_call": result["pending_tool_call"]}
