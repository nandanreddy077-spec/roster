"""Shared conversation logic used by both the dashboard chat and the SMS webhook.

One code path so a real customer text and a dashboard test message are handled
identically: load this customer's thread, run the agent, persist the turn, and
capture any booked jobs.
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from bookings import book_job, record_escalation
from db_models import ORIGIN_INBOUND, ORIGIN_MISSED_CALL, Business, Job, Message
from engine import (
    LOG_JOB_TOOL,
    TRANSFER_CALL_TOOL,
    AgentEngine,
    build_system_prompt,
    merge_consecutive_roles,
)
from memory import build_customer_context
from notifications import (
    KIND_ESCALATION,
    KIND_JOB_BOOKED,
    SOURCE_ALERT_OWNER,
    SOURCE_SMS_BOOKING,
    build_escalation_message,
    build_owner_message,
    is_test_thread,
    notify_owner_of_booking,
    notify_owner_of_escalation,
    record_owner_notification,
)
from repositories import get_or_create_customer
from sqlmodel import Session, select
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
    session: Session,
    client: Business,
    customer_phone: str,
    text: str,
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

    # Both tools, matching the voice path (xai_voice_adapter.build_session_update).
    # Without alert_owner here the SMS prompt's emergency instruction was a
    # promise the channel could not keep: the model told a gas-leak customer
    # "I'm alerting our team" while the owner received an ordinary
    # "just booked a job" text, indistinguishable from a routine call.
    result = agent.respond(
        client.to_config(),
        history,
        tools=[LOG_JOB_TOOL, TRANSFER_CALL_TOOL],
        system_prompt=system,
    )
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
        # Roster only ever speaks first on the missed-call text-back, so a
        # thread whose earliest message is ours IS a recovered missed call —
        # the single most valuable thing this product does, and until now
        # indistinguishable from a customer who simply texted in. Read off
        # the history already in hand; no extra query, no string matching on
        # the opener.
        origin = (
            ORIGIN_MISSED_CALL
            if history and history[0].get("role") == "assistant"
            else ORIGIN_INBOUND
        )
        job, created = book_job(
            session, client, customer_phone, customer_phone, ji, customer_id=cust.id, origin=origin
        )
        captured.append(job)
        if created:
            newly_created.append(job)

    session.commit()

    # Text the owner about each real NEW booking — the proof-of-work that
    # reaches an owner who never opens the dashboard. Detail-merges don't
    # re-text. Best-effort; a failed text can't affect the reply or the
    # already-committed job (see notifications.py).
    for job in newly_created:
        delivered = notify_owner_of_booking(client, job)
        # Log real bookings only — a dashboard test must not appear in the
        # owner's notifications feed, exactly as it doesn't get an SMS.
        if not is_test_thread(job.customer_phone):
            record_owner_notification(
                session,
                client.id,
                KIND_JOB_BOOKED,
                SOURCE_SMS_BOOKING,
                build_owner_message(job, "Frontdesk"),
                delivered,
            )

    # Strictly after the booking commit above, same rule the voice path follows:
    # the lead is already durable, so a failed page can never cost us the job.
    # `.get` not `[...]`: existing test doubles return results without this key.
    reply = result["reply"]
    pending = result.get("pending_tool_call")
    if pending and pending.get("name") == TRANSFER_CALL_TOOL["name"]:
        reply = _escalate(session, client, customer_phone, pending.get("input") or {}, reply)

    return {"reply": reply, "jobs": captured}


def _escalate(
    session: Session,
    client: Business,
    customer_phone: str,
    args: Dict[str, Any],
    reply: str,
) -> str:
    """Execute an alert_owner tool call from the SMS path and return the reply
    the customer should actually receive.

    Unlike voice — where the tool result is fed back and the model can report a
    failed page itself (see engine.build_voice_system_prompt) — the engine ends
    the turn on a passthrough tool, so the model's text was already written
    before we knew whether the owner was reached. The honest correction is
    therefore appended deterministically here rather than trusted to a second
    model turn: it costs no extra API call and cannot itself hallucinate.
    """
    reason = args.get("reason") or "customer needs the owner"
    customer = get_or_create_customer(session, client.id, customer_phone)
    job, should_notify = record_escalation(
        session, client, customer_phone, customer_phone, reason, customer_id=customer.id
    )

    # The owner's own dashboard test must never page them, exactly as it
    # never produces a booking text (notify_owner_of_booking's same check).
    if is_test_thread(customer_phone):
        return reply
    # Already paged successfully for this emergency inside the dedup window —
    # the owner does know, so the model's "I've alerted them" stays true.
    if not should_notify:
        return reply

    alerted = notify_owner_of_escalation(client, customer_phone, reason)
    record_owner_notification(
        session,
        client.id,
        KIND_ESCALATION,
        SOURCE_ALERT_OWNER,
        build_escalation_message(client, customer_phone, reason),
        alerted,
    )
    if alerted:
        job.owner_alerted_at = datetime.utcnow()
        session.add(job)
        session.commit()
        return reply
    return _alert_failed_reply(reply, client.escalation_phone)


def _alert_failed_reply(reply: str, escalation_phone: Optional[str]) -> str:
    """Never leave a customer holding a promise we failed to keep. The model
    has already said help is coming; if the page did not go out, say so plainly
    and give them something they can actually act on."""
    if escalation_phone:
        correction = (
            "I wasn't able to reach the team by text just now — please call "
            f"{escalation_phone} directly."
        )
    else:
        correction = (
            "I wasn't able to reach the team by text just now. If anyone is in "
            "danger, please call 911."
        )
    return f"{reply}\n\n{correction}" if reply else correction
