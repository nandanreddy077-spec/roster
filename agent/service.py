"""Shared conversation logic used by both the dashboard chat and the SMS webhook.

One code path so a real customer text and a dashboard test message are handled
identically: load this customer's thread, run the agent, persist the turn, and
capture any booked jobs.
"""
import json
from typing import Any, Dict, List

from sqlmodel import Session, select

from bookings import book_job
from db_models import Business, Job, Message
from engine import AgentEngine, build_system_prompt, merge_consecutive_roles
from memory import build_customer_context
from notifications import notify_owner_of_booking
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
    session: Session, client: Business, customer_phone: str, text: str,
    external_id: str | None = None,
) -> Dict[str, Any]:
    """Run one customer turn through the agent. Persists messages and any jobs.

    Returns {"reply": str | None, "jobs": list[Job]}. `reply` is None once the
    client's trial spend cap (hard cap + soft buffer) is exhausted — the
    inbound message is still recorded, but no paid model call is made and no
    reply is sent. Does not send anything itself — the caller decides how the
    reply leaves the building (TwiML, REST, or UI).

    `external_id` is the provider's delivery id (Twilio MessageSid). When a
    webhook retry re-runs a turn, the inbound message is stored exactly once
    and history stays clean — jobs are already retry-safe via book_job's
    upsert.
    """
    already_stored = None
    if external_id:
        already_stored = session.exec(
            select(Message).where(
                Message.business_id == client.id,
                Message.external_id == external_id,
            )
        ).first()
    if already_stored is None:
        session.add(
            Message(
                business_id=client.id,
                customer_phone=customer_phone,
                role="user",
                content_json=json.dumps(text),
                external_id=external_id,
            )
        )
        session.commit()

    # Load AFTER the insert so history (ending in this user turn) is exactly
    # what's durable — a retried turn reconstructs the identical prompt.
    history = _load_history(session, client.id, customer_phone)

    if not can_respond(client):
        return {"reply": None, "jobs": []}

    # Real customer memory: a returning customer's name and recent jobs ride
    # into the prompt, so the agent recognizes them instead of re-asking.
    system = build_system_prompt(client.to_config())
    context = build_customer_context(session, client.id, customer_phone)
    if context:
        system = f"{system}\n\n{context}"

    result = agent.respond(client.to_config(), history, system_prompt=system)
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
    newly_created: List[Job] = []
    for call in result["jobs"]:
        ji = call["input"]
        cust = get_or_create_customer(session, client.id, customer_phone, ji.get("customer_name"))
        # Idempotent booking: a re-call with the same service on this thread
        # merges details into the existing open job instead of duplicating.
        job, created = book_job(session, client, customer_phone, customer_phone, ji, customer_id=cust.id)
        captured.append(job)
        if created:
            newly_created.append(job)

    session.commit()

    # Text the owner about each real NEW booking — the proof-of-work that
    # reaches an owner who never opens the dashboard. Detail-merges don't
    # re-text. Best-effort; a failed text can't affect the reply or the
    # already-committed job (see notifications.py).
    for job in newly_created:
        notify_owner_of_booking(client, job)

    return {"reply": result["reply"], "jobs": captured}
