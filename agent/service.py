"""Shared conversation logic used by both the dashboard chat and the SMS webhook.

One code path so a real customer text and a dashboard test message are handled
identically: load this customer's thread, run the agent, persist the turn, and
capture any booked jobs.
"""
import json
from typing import Any, Dict, List

from sqlmodel import Session, select

from db_models import Business, Job, Message
from engine import AgentEngine, merge_consecutive_roles
from repositories import get_or_create_customer
from trial_cap import can_respond, record_usage

agent = AgentEngine()


def _load_history(session: Session, client_id: int, customer_phone: str) -> List[Dict[str, Any]]:
    rows = session.exec(
        select(Message)
        .where(Message.business_id == client_id, Message.customer_phone == customer_phone)
        .order_by(Message.id)
    ).all()
    raw = [{"role": m.role, "content": json.loads(m.content_json)} for m in rows]
    return merge_consecutive_roles(raw)


def handle_customer_message(
    session: Session, client: Business, customer_phone: str, text: str
) -> Dict[str, Any]:
    """Run one customer turn through the agent. Persists messages and any jobs.

    Returns {"reply": str | None, "jobs": list[Job]}. `reply` is None once the
    client's trial spend cap (hard cap + soft buffer) is exhausted — the
    inbound message is still recorded, but no paid model call is made and no
    reply is sent. Does not send anything itself — the caller decides how the
    reply leaves the building (TwiML, REST, or UI).
    """
    history = _load_history(session, client.id, customer_phone)
    history.append({"role": "user", "content": [{"type": "text", "text": text}]})

    session.add(
        Message(
            business_id=client.id,
            customer_phone=customer_phone,
            role="user",
            content_json=json.dumps(text),
        )
    )
    session.commit()

    if not can_respond(client):
        return {"reply": None, "jobs": []}

    result = agent.respond(client.to_config(), history)
    record_usage(session, client)

    # The engine produced the full turn (assistant tool_use, tool_result, final
    # reply); persist each so the next turn loads valid alternating history.
    for nm in result["new_messages"]:
        session.add(
            Message(
                business_id=client.id,
                customer_phone=customer_phone,
                role=nm["role"],
                content_json=json.dumps(nm["content"]),
            )
        )

    captured: List[Job] = []
    for call in result["jobs"]:
        ji = call["input"]
        cust = get_or_create_customer(session, client.id, customer_phone, ji.get("customer_name"))
        job = Job(
            business_id=client.id,
            customer_id=cust.id,
            customer_phone=customer_phone,
            customer_name=ji.get("customer_name"),
            service_type=ji["service_type"],
            urgency=ji["urgency"],
            address=ji.get("address"),
            callback_number=ji.get("callback_number") or customer_phone,
            notes=ji.get("notes"),
        )
        session.add(job)
        captured.append(job)

    session.commit()
    return {"reply": result["reply"], "jobs": captured}
