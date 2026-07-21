"""Adapter for xAI's Grok Voice Agent API (wss://api.x.ai/v1/realtime) — the
live-voice provider (Vapi has been removed; this is the only voice path).

Holds one WebSocket session open for the *whole call* and streams audio
directly — we only speak on the wire when a tool (log_job / alert_owner) is
invoked. This module owns that WebSocket session's lifecycle.

A client only gets live voice once its number is registered with xAI and its
Twilio trunk (or an xAI-issued number) points at xAI's SIP endpoint — see
agent/README.md. SMS agents (Frontdesk text-back, Chaser, Rebooker, Renewals,
Referrals, Reviews) are unaffected — they still run on Twilio SMS.

Webhook shape confirmed from xAI's now-public docs
(docs.x.ai/developers/model-capabilities/audio/voice-agent/sip):
`realtime.call.incoming` arrives as {"type": "realtime.call.incoming", "data":
{"call_id": ..., "sip_headers": [{"name": "From"/"To", "value": "+1..."}]}},
verified over three headers — `webhook-id`, `webhook-timestamp`,
`webhook-signature` — which is the Svix "standard webhooks" convention (also
used by Clerk, OpenAI, etc.), not a plain HMAC-of-body. Still unconfirmed:
whether the signing secret is `whsec_`-prefixed base64 (Svix's usual format)
and whether the signature header can contain multiple space-separated
`v1,<sig>` entries — the Svix spec allows for key rotation, but xAI's docs
didn't spell this out. Confirm against a real webhook delivery before relying
on this in production. The number-registration call that hands you this
secret is implemented in provisioning.py's `register_number_with_xai`.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
from typing import Any, Dict, List, Optional

import websockets
from sqlmodel import Session

from bookings import book_job
from memory import build_customer_context
from call_trace import CallTrace
from db_models import Business, Job, Message
from engine import LOG_JOB_TOOL, TRANSFER_CALL_TOOL, build_voice_system_prompt
from notifications import notify_owner_of_booking, notify_owner_of_escalation

REALTIME_URL = "wss://api.x.ai/v1/realtime"
VOICE_THREAD_PREFIX = "xai-voice:"
DEFAULT_VOICE = "eve"

# call_id is attacker-supplied (it rides in on the unverified webhook body) and
# then flows into a capture *filename*, a DB dedup key, and the voice thread id.
# Constrain it to a filename-safe token right here at the trust boundary so a
# value like "../../../../tmp/x" can never steer any of those downstream uses.
_VALID_CALL_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def parse_incoming_call_webhook(payload: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Pull call_id/to/from out of the confirmed realtime.call.incoming shape.
    Returns None if this isn't that event type, is missing what we need, or
    carries a call_id outside the safe `[A-Za-z0-9_-]{1,128}` charset."""
    if payload.get("type") != "realtime.call.incoming":
        return None
    data = payload.get("data", {})
    call_id = data.get("call_id")
    headers = {h.get("name"): h.get("value") for h in data.get("sip_headers", [])}
    to_number = headers.get("To")
    from_number = headers.get("From")
    if not call_id or not to_number:
        return None
    if not isinstance(call_id, str) or not _VALID_CALL_ID.match(call_id):
        return None
    return {"call_id": call_id, "to": to_number, "from": from_number or "unknown"}


def verify_webhook_signature(
    webhook_id: Optional[str],
    webhook_timestamp: Optional[str],
    raw_body: bytes,
    signature_header: Optional[str],
    signing_secret: str,
) -> bool:
    """Svix-style "standard webhooks" verification: sign `{id}.{timestamp}.{body}`
    with the (base64, often whsec_-prefixed) secret, compare against any of the
    space-separated `v1,<sig>` values in the signature header."""
    if not webhook_id or not webhook_timestamp or not signature_header:
        return False
    secret = signing_secret[len("whsec_"):] if signing_secret.startswith("whsec_") else signing_secret
    try:
        secret_bytes = base64.b64decode(secret, validate=True)
    except (ValueError, TypeError):
        return False
    signed_content = f"{webhook_id}.{webhook_timestamp}.".encode() + raw_body
    expected = base64.b64encode(hmac.new(secret_bytes, signed_content, hashlib.sha256).digest()).decode()

    for candidate in signature_header.split():
        _, _, sig = candidate.partition(",")
        if hmac.compare_digest(sig or candidate, expected):
            return True
    return False


def _translate_tool(tool: Dict[str, Any]) -> Dict[str, Any]:
    """engine.py's tools use Anthropic's {name, description, input_schema}
    shape; Grok's Realtime API (like OpenAI's) wants {type: "function", name,
    description, parameters}."""
    return {
        "type": "function",
        "name": tool["name"],
        "description": tool["description"],
        "parameters": tool["input_schema"],
    }


def build_session_update(client: Business, customer_context: str = "") -> Dict[str, Any]:
    instructions = build_voice_system_prompt(client.to_config())
    if customer_context:
        # Returning-caller memory: name + recent jobs, business-scoped (see
        # memory.build_customer_context) — the receptionist recognizes them.
        instructions = f"{instructions}\n\n{customer_context}"
    return {
        "type": "session.update",
        "session": {
            "voice": DEFAULT_VOICE,
            "instructions": instructions,
            "turn_detection": {"type": "server_vad"},
            "tools": [_translate_tool(LOG_JOB_TOOL), _translate_tool(TRANSFER_CALL_TOOL)],
        },
    }


def _thread_id(call_id: str) -> str:
    return f"{VOICE_THREAD_PREFIX}{call_id}"


async def _persist_job(session: Session, client: Business, thread: str, caller_number: str, args: Dict[str, Any]) -> Job:
    # Idempotent: the tool description invites re-calls with new details, and
    # webhook/LLM retries can replay this — book_job merges into the existing
    # open job for this call's thread instead of inserting a duplicate.
    job, created = book_job(session, client, thread, caller_number, args)
    # Same owner-text as the SMS path, for a NEW voice-booked job only — a
    # detail-merge never re-texts the owner. Offloaded to a thread: this SMS
    # send is a blocking HTTP call, and blocking the event loop here would
    # stall every other in-progress call's audio on the same process.
    if created:
        await asyncio.to_thread(notify_owner_of_booking, client, job)
    return job


async def _handle_function_call(
    ws, session: Session, client: Business, thread: str, caller_number: str,
    event: Dict[str, Any], trace: CallTrace,
) -> None:
    name = event["name"]
    call_id = event["call_id"]
    args = json.loads(event["arguments"]) if event.get("arguments") else {}
    trace.stage("tool_invoked", tool=name)

    if name == LOG_JOB_TOOL["name"]:
        job = await _persist_job(session, client, thread, caller_number, args)
        trace.stage("job_persisted", job_id=job.id)
        # _persist_job already fired the owner text (best-effort) — mark the
        # stage here so the latency trace shows when the notification went out.
        trace.stage("owner_notified", job_id=job.id)
        result = {"status": "logged"}
    elif name == TRANSFER_CALL_TOOL["name"]:
        # Honest escalation: we cannot redirect a live call, so (1) persist the
        # lead FIRST so an emergency caller is never lost even if everything
        # after fails, (2) urgently text the owner, (3) tell the model the
        # truth about whether that text went out, plus the owner's number so
        # the caller can be given a real next step either way.
        reason = args.get("reason") or "caller needs the owner"
        job = Job(
            business_id=client.id,
            customer_phone=thread,
            service_type="Escalated call",
            urgency="emergency",
            callback_number=caller_number,
            notes=reason,
        )
        session.add(job)
        session.commit()
        trace.stage("job_persisted", job_id=job.id, escalation=True)
        alerted = await asyncio.to_thread(notify_owner_of_escalation, client, caller_number, reason)
        trace.stage("owner_alerted" if alerted else "owner_alert_failed")
        result = {
            "status": "owner_alerted" if alerted else "alert_failed",
            "owner_number": client.escalation_phone,
        }
    else:
        result = {"status": "unknown_tool"}

    await ws.send(json.dumps({
        "type": "conversation.item.create",
        "item": {
            "type": "function_call_output",
            "call_id": call_id,
            "output": json.dumps(result),
        },
    }))
    await ws.send(json.dumps({"type": "response.create"}))


def _extract_transcript(response_done_event: Dict[str, Any]) -> str:
    """Best-effort: pull spoken text out of a response.done event's output
    items. Field names follow the OpenAI-Realtime-compatible shape Grok
    documents itself against; confirm against a real payload."""
    parts: List[str] = []
    for item in response_done_event.get("response", {}).get("output", []):
        for content in item.get("content", []):
            transcript = content.get("transcript") or content.get("text")
            if transcript:
                parts.append(transcript)
    return " ".join(parts).strip()


def _default_connect(call_id: str):
    """The real xAI realtime connection. Kept separate so run_call can be
    driven in tests with a scripted WebSocket and no XAI_API_KEY."""
    api_key = os.environ["XAI_API_KEY"]
    url = f"{REALTIME_URL}?call_id={call_id}"
    return websockets.connect(url, additional_headers={"Authorization": f"Bearer {api_key}"})


async def run_call(call_id: str, client: Business, caller_number: str, session_factory,
                   connect=None, trace: Optional[CallTrace] = None) -> None:
    """Owns one live call end-to-end. Runs as a background task kicked off by
    the /webhook/xai-incoming-call handler — must not block that handler's
    response to xAI.

    `connect(call_id)` returns the realtime WebSocket as an async context
    manager (injectable for tests). `trace` records every event and stage with
    a millisecond offset for boundary debugging; a capturing one is created if
    not supplied."""
    connect = connect or _default_connect
    if trace is None:
        from db import DATA_DIR
        trace = CallTrace(call_id, capture_dir=DATA_DIR / "call_captures")
    thread = _thread_id(call_id)

    try:
        await _run_call_session(connect, call_id, client, caller_number, session_factory, thread, trace)
    except Exception as e:
        # A mid-call crash (WS drop, DB error, bad payload) must never vanish
        # silently: record it, and text the owner the caller's number so the
        # human relationship survives the software failure.
        trace.stage("call_failed", error=repr(e))
        await asyncio.to_thread(
            notify_owner_of_escalation,
            client, caller_number,
            f"the AI call with this customer dropped mid-call — call them back",
        )
    finally:
        trace.close()


async def _run_call_session(connect, call_id, client, caller_number, session_factory, thread, trace) -> None:
    with session_factory() as session:
        customer_context = build_customer_context(session, client.id, caller_number)
    async with connect(call_id) as ws:
        trace.stage("ws_connected")
        await ws.send(json.dumps(build_session_update(client, customer_context)))
        await ws.send(json.dumps({"type": "response.create"}))

        first_response_seen = False
        flagged_types: set = set()
        async for raw in ws:
            event = json.loads(raw)
            trace.event(event)
            etype = event.get("type")

            if etype == "response.function_call_arguments.done":
                with session_factory() as session:
                    session_client = session.get(Business, client.id)
                    await _handle_function_call(ws, session, session_client, thread, caller_number, event, trace)

            elif etype == "response.done":
                if not first_response_seen:
                    trace.stage("first_ai_response")
                    first_response_seen = True
                transcript = _extract_transcript(event)
                if transcript:
                    with session_factory() as session:
                        session.add(Message(
                            business_id=client.id,
                            customer_phone=thread,
                            role="assistant",
                            content_json=json.dumps([{"type": "text", "text": transcript}]),
                        ))
                        session.commit()

            else:
                if etype not in flagged_types:
                    trace.stage("unexpected_event", type=etype)
                    flagged_types.add(etype)

        trace.stage("call_completed")
