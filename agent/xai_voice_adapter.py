"""Adapter for xAI's Grok Voice Agent API (wss://api.x.ai/v1/realtime) — the
live-voice provider (Vapi has been removed; this is the only voice path).

Holds one WebSocket session open for the *whole call* and streams audio
directly — we only speak on the wire when a tool (log_job / transfer_call) is
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
import base64
import hashlib
import hmac
import json
import os
from typing import Any, Dict, List, Optional

import websockets
from sqlmodel import Session

from db_models import Business, Job, Message
from engine import LOG_JOB_TOOL, TRANSFER_CALL_TOOL, build_voice_system_prompt

REALTIME_URL = "wss://api.x.ai/v1/realtime"
VOICE_THREAD_PREFIX = "xai-voice:"
DEFAULT_VOICE = "eve"


def parse_incoming_call_webhook(payload: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Pull call_id/to/from out of the confirmed realtime.call.incoming shape.
    Returns None if this isn't that event type or is missing what we need."""
    if payload.get("type") != "realtime.call.incoming":
        return None
    data = payload.get("data", {})
    call_id = data.get("call_id")
    headers = {h.get("name"): h.get("value") for h in data.get("sip_headers", [])}
    to_number = headers.get("To")
    from_number = headers.get("From")
    if not call_id or not to_number:
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
    secret_bytes = base64.b64decode(secret)
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


def build_session_update(client: Business) -> Dict[str, Any]:
    return {
        "type": "session.update",
        "session": {
            "voice": DEFAULT_VOICE,
            "instructions": build_voice_system_prompt(client.to_config()),
            "turn_detection": {"type": "server_vad"},
            "tools": [_translate_tool(LOG_JOB_TOOL), _translate_tool(TRANSFER_CALL_TOOL)],
        },
    }


def _thread_id(call_id: str) -> str:
    return f"{VOICE_THREAD_PREFIX}{call_id}"


def _persist_job(session: Session, client: Business, thread: str, caller_number: str, args: Dict[str, Any]) -> Job:
    job = Job(
        business_id=client.id,
        customer_phone=thread,
        customer_name=args.get("customer_name"),
        service_type=args["service_type"],
        urgency=args["urgency"],
        address=args.get("address"),
        callback_number=args.get("callback_number") or caller_number,
        notes=args.get("notes"),
    )
    session.add(job)
    session.commit()
    return job


async def _handle_function_call(
    ws, session: Session, client: Business, thread: str, caller_number: str, event: Dict[str, Any]
) -> None:
    name = event["name"]
    call_id = event["call_id"]
    args = json.loads(event["arguments"]) if event.get("arguments") else {}

    if name == LOG_JOB_TOOL["name"]:
        _persist_job(session, client, thread, caller_number, args)
        result = {"status": "logged"}
    elif name == TRANSFER_CALL_TOOL["name"]:
        # The actual call transfer is xAI/SIP-side (out of scope here); we
        # just acknowledge so the model can tell the caller what's happening.
        result = {"status": "transferring", "destination": args.get("destination")}
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


async def run_call(call_id: str, client: Business, caller_number: str, session_factory) -> None:
    """Owns one live call end-to-end. Runs as a background task kicked off by
    the /webhook/xai-incoming-call handler — must not block that handler's
    response to xAI."""
    api_key = os.environ["XAI_API_KEY"]
    thread = _thread_id(call_id)
    url = f"{REALTIME_URL}?call_id={call_id}"

    async with websockets.connect(url, additional_headers={"Authorization": f"Bearer {api_key}"}) as ws:
        await ws.send(json.dumps(build_session_update(client)))
        await ws.send(json.dumps({"type": "response.create"}))

        async for raw in ws:
            event = json.loads(raw)
            etype = event.get("type")

            if etype == "response.function_call_arguments.done":
                with session_factory() as session:
                    session_client = session.get(Business, client.id)
                    await _handle_function_call(ws, session, session_client, thread, caller_number, event)

            elif etype == "response.done":
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
