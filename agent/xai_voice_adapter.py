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
from datetime import datetime
from typing import Any, Dict, List, Optional

import websockets
from bookings import book_job, record_escalation
from call_trace import CallTrace
from db_models import Business, Job, Message
from engine import LOG_JOB_TOOL, TRANSFER_CALL_TOOL, build_voice_system_prompt
from memory import build_customer_context
from notifications import (
    KIND_CALL_DROPPED,
    KIND_ESCALATION,
    KIND_JOB_BOOKED,
    SOURCE_ALERT_OWNER,
    SOURCE_CALL_DROPPED,
    SOURCE_VOICE_BOOKING,
    VOICE_TEST_THREAD_PREFIX,
    build_escalation_message,
    build_owner_message,
    is_test_thread,
    notify_owner_of_booking,
    notify_owner_of_escalation,
    record_owner_notification,
)
from repositories import get_or_create_customer
from sqlmodel import Session, select

REALTIME_URL = "wss://api.x.ai/v1/realtime"
VOICE_THREAD_PREFIX = "xai-voice:"
DEFAULT_VOICE = os.environ.get("XAI_VOICE", "eve")

# Turn-taking is what makes a voice feel human — more than the voice itself.
# xAI's defaults are tuned for clean studio audio, not a phone line:
#
#   threshold 0.85 (default) means only LOUD speech registers. A caller in a
#   truck, outside, or simply softly spoken gets ignored, and the AI talks over
#   them or sits waiting. 0.55 registers ordinary phone speech while staying
#   above line noise.
#
#   silence_duration_ms is the gap before the AI decides you've finished. Long
#   gaps are the classic robot tell — the caller stops, then waits, then starts
#   again just as the AI begins. 500ms is about a natural conversational beat.
#
# Both are deliberately conservative rather than aggressive: cutting a caller
# off mid-sentence reads as ruder, and less human, than a slight pause.
TURN_DETECTION = {
    "type": "server_vad",
    "threshold": 0.55,
    "silence_duration_ms": 500,
    "prefix_padding_ms": 300,
}

# Home-services vocabulary the transcriber would otherwise mangle. Kept short
# and trade-general — this is the shared list every business gets, not a
# per-customer glossary (xAI caps it at 100 terms).
TRADE_KEYTERMS = [
    "HVAC",
    "AC unit",
    "condenser",
    "compressor",
    "evaporator coil",
    "furnace",
    "heat pump",
    "thermostat",
    "ductwork",
    "refrigerant",
    "freon",
    "air handler",
    "P-trap",
    "water heater",
    "tankless",
    "sump pump",
    "garbage disposal",
    "shutoff valve",
    "main line",
    "sewer line",
    "septic",
    "drain snake",
    "flapper valve",
    "pressure regulator",
    "backflow",
    "breaker",
    "panel",
    "GFCI",
    "outlet",
    "estimate",
    "quote",
    "diagnostic fee",
    "service call",
]

# Hard safety cap on one call's total duration (connect through close) — a
# stuck/zombie connection (or a caller who never hangs up) must not pin a
# WebSocket and its asyncio task open forever, or run up unmetered xAI
# Realtime API cost indefinitely. 10 minutes is generous for a real home-
# service conversation while still bounding the worst case. Overridable per
# call via run_call's own `max_duration_seconds` param (tests use a tiny
# value; this constant is what production actually runs with).
MAX_CALL_DURATION_SECONDS = 600

# Hard safety cap on one call's cumulative token usage — a stuck loop, an
# unusually verbose model, or a caller who keeps a call going without ever
# hitting the duration cap must still have unmetered xAI cost bounded some
# other way. TOKENS, not a fabricated cents-per-token rate: trial_cap.py's
# own per-turn cost estimate is already "not real per-token billing... just
# deterministic and easy to test" (its own words) — inventing a conversion
# rate here would be no more honest, so the budget is expressed in the unit
# the events actually report. 20,000 is a generous ceiling for a real
# conversation. Overridable per call via run_call's own `max_call_tokens`
# param, same injectable pattern as `max_duration_seconds`.
MAX_CALL_TOKEN_BUDGET = 20_000

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
    secret = (
        signing_secret[len("whsec_") :] if signing_secret.startswith("whsec_") else signing_secret
    )
    try:
        secret_bytes = base64.b64decode(secret, validate=True)
    except (ValueError, TypeError):
        return False
    signed_content = f"{webhook_id}.{webhook_timestamp}.".encode() + raw_body
    expected = base64.b64encode(
        hmac.new(secret_bytes, signed_content, hashlib.sha256).digest()
    ).decode()

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
            "turn_detection": TURN_DETECTION,
            "audio": {
                "input": {
                    # Trade vocabulary, so the caller doesn't have to repeat
                    # themselves. Mishearing "P-trap" as "pea trap" is the
                    # single loudest tell that a machine is on the line.
                    "transcription": {"keyterms": TRADE_KEYTERMS},
                },
            },
            "tools": [_translate_tool(LOG_JOB_TOOL), _translate_tool(TRANSFER_CALL_TOOL)],
        },
    }


def _thread_id(call_id: str, is_test_call: bool = False) -> str:
    # is_test_call opt-in only, default False. The prefix alone carries the
    # test/real distinction from here on — every downstream function decides
    # whether to notify a real owner by checking is_test_thread(thread),
    # never by threading a second boolean through every signature.
    prefix = VOICE_TEST_THREAD_PREFIX if is_test_call else VOICE_THREAD_PREFIX
    return f"{prefix}{call_id}"


async def _persist_job(
    session: Session, client: Business, thread: str, caller_number: str, args: Dict[str, Any]
) -> Job:
    # Customer identity, same guarantee the SMS path already gives every
    # booking (service.py) — keyed on the caller's real phone number, never
    # `thread` (xai-voice:{call_id} is unique per call, not per customer), so
    # a repeat caller resolves to the same Customer every time, and reuses
    # whatever Customer row an SMS conversation with this same number already
    # created (get_or_create_customer keys on (business_id, phone) alone).
    customer = get_or_create_customer(session, client.id, caller_number, args.get("customer_name"))
    # Idempotent: the tool description invites re-calls with new details, and
    # webhook/LLM retries can replay this — book_job merges into the existing
    # open job for this call's thread instead of inserting a duplicate.
    job, created = book_job(session, client, thread, caller_number, args, customer_id=customer.id)
    # Same owner-text as the SMS path, for a NEW voice-booked job only — a
    # detail-merge never re-texts the owner. Offloaded to a thread: this SMS
    # send is a blocking HTTP call, and blocking the event loop here would
    # stall every other in-progress call's audio on the same process.
    #
    # Test mode (thread carries VOICE_TEST_THREAD_PREFIX): never notify or
    # log a real owner alert for test activity — the same guarantee
    # service.py's SMS path already gives its own portal-test thread.
    if created and not is_test_thread(thread):
        delivered = await asyncio.to_thread(notify_owner_of_booking, client, job)
        # The log write stays on THIS thread with the session already in
        # scope: a Session is not thread-safe and must never cross the
        # to_thread boundary the blocking SMS send goes through.
        record_owner_notification(
            session,
            client.id,
            KIND_JOB_BOOKED,
            SOURCE_VOICE_BOOKING,
            build_owner_message(job, "Frontdesk"),
            delivered,
        )
    return job


# A single failing tool invocation must not end an otherwise healthy call
# (2026-07-29 audit, "narrow per-tool error recovery") — the caller may still
# need help with something else. Recoverable: malformed tool-call arguments,
# and any exception raised while a tool actually executes (a booking DB
# hiccup, an unexpected bug) — both are reported to the model as a plain
# {"status": "error", ...} over the SAME function_call_output round-trip a
# success uses, and the loop continues to the next event exactly as before.
# Unrecoverable, by design, is anything OUTSIDE this function's try/except:
# if `ws.send` itself fails, the transport is dead and there is no way to
# tell the model or the caller anything — that still propagates up to
# run_call's existing top-level crash handler, unchanged.
GENERIC_TOOL_ERROR = {
    "status": "error",
    "detail": "Something went wrong on our end. Please try again.",
}


async def _send_tool_result(ws, call_id: str, result: Dict[str, Any]) -> None:
    await ws.send(
        json.dumps(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(result),
                },
            }
        )
    )
    await ws.send(json.dumps({"type": "response.create"}))


async def _handle_function_call(
    ws,
    session: Session,
    client: Business,
    thread: str,
    caller_number: str,
    event: Dict[str, Any],
    trace: CallTrace,
) -> None:
    name = event["name"]
    call_id = event["call_id"]

    try:
        args = json.loads(event["arguments"]) if event.get("arguments") else {}
    except (TypeError, ValueError) as e:
        # Malformed arguments never reached a real tool invocation — traced
        # distinctly from an execution failure below, so the two read apart
        # in the capture (a bad payload vs. our own code breaking).
        trace.stage("tool_call_failed", tool=name, reason="malformed_arguments", error=repr(e))
        await _send_tool_result(
            ws,
            call_id,
            {
                "status": "error",
                "detail": "Could not read that. Could you say it again?",
            },
        )
        return

    trace.stage("tool_invoked", tool=name)

    try:
        if name == LOG_JOB_TOOL["name"]:
            job = await _persist_job(session, client, thread, caller_number, args)
            trace.stage("job_persisted", job_id=job.id)
            # _persist_job already skipped the owner text for a test-mode
            # thread (see there) — trace the actual outcome honestly rather
            # than always claiming "notified".
            if is_test_thread(thread):
                trace.stage("owner_notification_skipped", job_id=job.id, reason="test_mode")
            else:
                trace.stage("owner_notified", job_id=job.id)
            result = {"status": "logged"}
        elif name == TRANSFER_CALL_TOOL["name"]:
            # Honest escalation: we cannot redirect a live call, so (1) persist
            # the lead FIRST so an emergency caller is never lost even if
            # everything after fails, (2) urgently text the owner, (3) tell
            # the model the truth about whether that text went out, plus the
            # owner's number so the caller can be given a real next step
            # either way.
            #
            # Idempotent per call thread (record_escalation): a repeat
            # alert_owner in the same conversation must not double-page the
            # owner, but a retry after a FAILED page must still go through —
            # should_notify tracks exactly that (2026-07-29 audit finding).
            reason = args.get("reason") or "caller needs the owner"
            # Same identity guarantee as log_job (see _persist_job) — a Job
            # is a Job regardless of which tool created it, and an escalation
            # call is from a real, identifiable caller too. alert_owner
            # carries no name.
            customer = get_or_create_customer(session, client.id, caller_number)
            job, should_notify = record_escalation(
                session, client, thread, caller_number, reason, customer_id=customer.id
            )
            trace.stage("job_persisted", job_id=job.id, escalation=True)
            if is_test_thread(thread):
                # Test mode: exercise the full escalation path (Job
                # persisted, idempotency intact) without ever paging a real
                # owner. Honest to the model too — nothing failed, there's
                # simply no real owner to page.
                trace.stage("owner_notification_skipped", job_id=job.id, reason="test_mode")
                status = "owner_alerted"
            elif should_notify:
                alerted = await asyncio.to_thread(
                    notify_owner_of_escalation, client, caller_number, reason
                )
                trace.stage("owner_alerted" if alerted else "owner_alert_failed")
                # Strictly after `alerted` is decided: this must never
                # influence what the model — and therefore the caller — is
                # told.
                record_owner_notification(
                    session,
                    client.id,
                    KIND_ESCALATION,
                    SOURCE_ALERT_OWNER,
                    build_escalation_message(client, caller_number, reason),
                    alerted,
                )
                if alerted:
                    job.owner_alerted_at = datetime.utcnow()
                    session.add(job)
                    session.commit()
                status = "owner_alerted" if alerted else "alert_failed"
            else:
                # Already successfully paged for this call — honest, not a
                # fresh alert: the owner does already know, so this isn't a lie.
                trace.stage("escalation_deduped", job_id=job.id)
                status = "owner_alerted"
            result = {"status": status, "owner_number": client.escalation_phone}
        else:
            result = {"status": "unknown_tool"}
    except Exception as e:
        # Recoverable by design (see module note above this function): a
        # booking/escalation failure ends the TOOL CALL, never the call.
        trace.stage("tool_call_failed", tool=name, reason="execution_error", error=repr(e))
        result = dict(GENERIC_TOOL_ERROR)

    await _send_tool_result(ws, call_id, result)


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


def _extract_response_tokens(response_done_event: Dict[str, Any]) -> int:
    """Pull total token usage out of a response.done event.

    CONFIRMED against a live xAI realtime session (2026-08-04): usage rides at
    the TOP LEVEL of the event, and `response.usage` — the OpenAI-Realtime-
    compatible location this originally assumed — is an empty dict:

        {"type": "response.done",
         "response": {..., "usage": {}},
         "usage": {"input_tokens": 10, "output_tokens": 277,
                   "total_tokens": 287, ...}}

    Reading only the nested one returned 0 for every turn, so
    `total_tokens_used` never incremented and MAX_CALL_TOKEN_BUDGET could
    never fire — the per-call cost cap was inert. Both locations are checked,
    confirmed one first, so a future version that populates the nested field
    still works.

    Malformed or missing usage is treated as zero and never raises: a
    usage-reporting hiccup must not break the call the budget exists to
    protect.
    """
    response = response_done_event.get("response")
    candidates = (
        response_done_event.get("usage"),
        response.get("usage") if isinstance(response, dict) else None,
    )
    for usage in candidates:
        if not isinstance(usage, dict) or not usage:
            continue
        total = usage.get("total_tokens")
        if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
            return total
        parts = [
            v
            for v in (usage.get("input_tokens"), usage.get("output_tokens"))
            if isinstance(v, int) and not isinstance(v, bool) and v >= 0
        ]
        if parts:
            return sum(parts)
    return 0


class _CallBudgetExceeded(Exception):
    """Internal signal only, raised inside the event loop when cumulative
    token usage crosses the configured per-call budget. Caught by run_call,
    which ends the call the same honest way a timeout does — a distinct
    reason, not a generic crash. Never crosses a module boundary as a public
    contract; existing solely so the detection point (inside the loop, where
    the token count is known) and the reaction (owner notification, in
    run_call) can stay where each naturally belongs."""


# The caller's side of the conversation. Same OpenAI-Realtime-compatible
# assumption _extract_transcript already makes for the assistant side —
# unconfirmed against a real xAI payload until a live call is captured.
# `.delta` carries the transcript as it's still being recognized (streaming,
# partial); only `.completed` is durable, same final-only rule the assistant
# side already follows for response.done.
USER_TRANSCRIPT_COMPLETED = "conversation.item.input_audio_transcription.completed"
USER_TRANSCRIPT_DELTA = "conversation.item.input_audio_transcription.delta"

# CONFIRMED against real calls (2026-08-04): xAI sends this when the caller
# hangs up. It is a NORMAL end of call, not a fault — but it used to fall
# through to the unexpected-event branch, after which the websocket closed
# abnormally and run_call's generic handler texted the owner "the AI call with
# this customer dropped mid-call". Three real calls, three false alarms: every
# successful call ended by telling the owner it had failed.
PARTICIPANT_DISCONNECTED = "participant.disconnected"

# The caller starts HEARING the greeting on the first of these, several
# seconds before response.done reports it finished. Tracing only response.done
# as "first_ai_response" made a 2.3s time-to-audio read as 6.9s of latency and
# sent a real debugging session chasing dead air that never existed.
ASSISTANT_AUDIO_DELTA = "response.output_audio.delta"


def _persist_user_transcript(
    session: Session, business_id: int, thread: str, event: Dict[str, Any]
) -> None:
    """Persist the caller's transcribed utterance as a user-role Message.

    Dedup reuses Message.external_id exactly as service.py's SMS path already
    dedupes a redelivered Twilio MessageSid — here keyed on xAI's own stable
    item_id, same column, same guarantee, no new mechanism. A missing
    item_id/transcript, or an empty/non-string transcript, is a silent no-op:
    a transcription-pipeline hiccup must never crash the call the way an
    unhandled exception would (run_call's top-level handler would otherwise
    text the owner "call dropped" over what is really just a missing field).
    """
    item_id = event.get("item_id")
    transcript = event.get("transcript")
    transcript = transcript.strip() if isinstance(transcript, str) else ""
    if not item_id or not transcript:
        return
    already_stored = session.exec(
        select(Message).where(Message.business_id == business_id, Message.external_id == item_id)
    ).first()
    if already_stored is not None:
        return
    session.add(
        Message(
            business_id=business_id,
            customer_phone=thread,
            role="user",
            content_json=json.dumps([{"type": "text", "text": transcript}]),
            external_id=item_id,
        )
    )
    session.commit()


def _default_connect(call_id: str):
    """The real xAI realtime connection. Kept separate so run_call can be
    driven in tests with a scripted WebSocket and no XAI_API_KEY."""
    api_key = os.environ["XAI_API_KEY"]
    url = f"{REALTIME_URL}?call_id={call_id}"
    return websockets.connect(url, additional_headers={"Authorization": f"Bearer {api_key}"})


async def run_call(
    call_id: str,
    client: Business,
    caller_number: str,
    session_factory,
    connect=None,
    trace: Optional[CallTrace] = None,
    max_duration_seconds: Optional[float] = None,
    max_call_tokens: Optional[int] = None,
    is_test_call: bool = False,
) -> None:
    """Owns one live call end-to-end. Runs as a background task kicked off by
    the /webhook/xai-incoming-call handler — must not block that handler's
    response to xAI.

    `connect(call_id)` returns the realtime WebSocket as an async context
    manager (injectable for tests). `trace` records every event and stage with
    a millisecond offset for boundary debugging; a capturing one is created if
    not supplied. `max_duration_seconds` overrides MAX_CALL_DURATION_SECONDS;
    `max_call_tokens` overrides MAX_CALL_TOKEN_BUDGET (both: tests use small
    values instead of production's real ceilings).

    `is_test_call` (2026-07-29, safe voice test mode) is opt-in only —
    default False, and the real production entry point (app.py's
    /webhook/xai-incoming-call handler) never passes it, so a real inbound
    call can never activate it by accident. When True, the call's thread
    carries VOICE_TEST_THREAD_PREFIX instead of the real prefix, which is
    ALL that changes about the event loop, tool handling, or transcript
    capture below — nothing here is mocked or skipped. Every owner-facing
    notification this function and _handle_function_call can trigger
    (booking, escalation, and every "call ended abnormally" path below)
    checks is_test_thread(thread) and skips the real page, exactly like
    service.py's SMS path already does for its own portal-test thread."""
    connect = connect or _default_connect
    if trace is None:
        from db import DATA_DIR

        trace = CallTrace(call_id, capture_dir=DATA_DIR / "call_captures")
    thread = _thread_id(call_id, is_test_call)
    max_duration = (
        MAX_CALL_DURATION_SECONDS if max_duration_seconds is None else max_duration_seconds
    )
    max_tokens = MAX_CALL_TOKEN_BUDGET if max_call_tokens is None else max_call_tokens

    try:
        await asyncio.wait_for(
            _run_call_session(
                connect, call_id, client, caller_number, session_factory, thread, trace, max_tokens
            ),
            timeout=max_duration,
        )
    except asyncio.TimeoutError:
        # Bounds the WHOLE session (connect through close) from the outside —
        # _run_call_session's own internals (the event loop, tool handling,
        # transcript capture) are untouched. Cancelling this await propagates
        # a CancelledError up through _run_call_session's
        # `async with connect(...) as ws:` block, which is what actually
        # closes the websocket — the same mechanism a clean return or a crash
        # already goes through, not a new one.
        trace.stage("call_timed_out", max_duration_seconds=max_duration)
        if is_test_thread(thread):
            trace.stage("owner_notification_skipped", reason="test_mode")
        else:
            # Honest, not a generic "dropped" claim: this was OUR safety
            # cutoff, not an unexplained failure — say so.
            timeout_reason = (
                "the AI call with this customer reached the maximum allowed "
                "duration and was ended automatically — call them back"
            )
            alerted = await asyncio.to_thread(
                notify_owner_of_escalation,
                client,
                caller_number,
                timeout_reason,
            )
            with session_factory() as session:
                record_owner_notification(
                    session,
                    client.id,
                    KIND_CALL_DROPPED,
                    SOURCE_CALL_DROPPED,
                    build_escalation_message(client, caller_number, timeout_reason),
                    alerted,
                )
    except _CallBudgetExceeded:
        # Detected (and traced, with the exact token count) inside the loop,
        # where the usage numbers actually are — unlike the timeout branch
        # above, whose own trace happens here because wait_for's cancellation
        # carries no extra data worth recording beyond max_duration itself.
        # Raising from inside `async with connect(...) as ws:` closes the
        # websocket via ordinary exception unwinding — the same mechanism a
        # crash already goes through, not a new one.
        if is_test_thread(thread):
            trace.stage("owner_notification_skipped", reason="test_mode")
        else:
            budget_reason = (
                "the AI call with this customer reached its usage budget and "
                "was ended automatically — call them back"
            )
            alerted = await asyncio.to_thread(
                notify_owner_of_escalation,
                client,
                caller_number,
                budget_reason,
            )
            with session_factory() as session:
                record_owner_notification(
                    session,
                    client.id,
                    KIND_CALL_DROPPED,
                    SOURCE_CALL_DROPPED,
                    build_escalation_message(client, caller_number, budget_reason),
                    alerted,
                )
    except Exception as e:
        # A mid-call crash (WS drop, DB error, bad payload) must never vanish
        # silently: record it, and text the owner the caller's number so the
        # human relationship survives the software failure.
        trace.stage("call_failed", error=repr(e))
        if is_test_thread(thread):
            trace.stage("owner_notification_skipped", reason="test_mode")
        else:
            dropped_reason = "the AI call with this customer dropped mid-call — call them back"
            alerted = await asyncio.to_thread(
                notify_owner_of_escalation,
                client,
                caller_number,
                dropped_reason,
            )
            # Its own short-lived session: this path has no open one, and the
            # crashed call's session is not safe to reuse.
            with session_factory() as session:
                record_owner_notification(
                    session,
                    client.id,
                    KIND_CALL_DROPPED,
                    SOURCE_CALL_DROPPED,
                    build_escalation_message(client, caller_number, dropped_reason),
                    alerted,
                )
    finally:
        trace.close()


async def _run_call_session(
    connect, call_id, client, caller_number, session_factory, thread, trace, max_tokens: int
) -> None:
    with session_factory() as session:
        customer_context = build_customer_context(session, client.id, caller_number)
    async with connect(call_id) as ws:
        trace.stage("ws_connected")
        await ws.send(json.dumps(build_session_update(client, customer_context)))
        await ws.send(json.dumps({"type": "response.create"}))

        first_response_seen = False
        first_audio_seen = False
        flagged_types: set = set()
        total_tokens_used = 0
        async for raw in ws:
            event = json.loads(raw)
            trace.event(event)
            etype = event.get("type")

            if etype == PARTICIPANT_DISCONNECTED:
                # The caller hung up. Ending the loop here is what makes this a
                # completed call rather than a crash: falling through to the
                # websocket's own abnormal close raises, and run_call's generic
                # handler would page the owner about a call that went fine.
                trace.stage("caller_hung_up")
                break

            if etype == ASSISTANT_AUDIO_DELTA:
                if not first_audio_seen:
                    trace.stage("first_audio_to_caller")
                    first_audio_seen = True
                continue

            if etype == "response.function_call_arguments.done":
                with session_factory() as session:
                    session_client = session.get(Business, client.id)
                    await _handle_function_call(
                        ws, session, session_client, thread, caller_number, event, trace
                    )

            elif etype == "response.done":
                if not first_response_seen:
                    # Response COMPLETE, not response started — the caller has
                    # been hearing audio since first_audio_to_caller above. Kept
                    # under the old name so existing traces stay comparable.
                    trace.stage("first_ai_response", meaning="first response finished")
                    first_response_seen = True
                transcript = _extract_transcript(event)
                if transcript:
                    with session_factory() as session:
                        session.add(
                            Message(
                                business_id=client.id,
                                customer_phone=thread,
                                role="assistant",
                                content_json=json.dumps([{"type": "text", "text": transcript}]),
                            )
                        )
                        session.commit()
                # Checked AFTER persisting this response's own transcript —
                # the turn that tips the budget over still gets recorded, and
                # any booking/escalation from an EARLIER tool call this same
                # loop already committed synchronously is untouched either way.
                total_tokens_used += _extract_response_tokens(event)
                if total_tokens_used > max_tokens:
                    trace.stage(
                        "call_budget_exceeded",
                        total_tokens=total_tokens_used,
                        max_tokens=max_tokens,
                    )
                    raise _CallBudgetExceeded()

            elif etype == USER_TRANSCRIPT_COMPLETED:
                with session_factory() as session:
                    _persist_user_transcript(session, client.id, thread, event)

            elif etype == USER_TRANSCRIPT_DELTA:
                pass  # streaming partial text; only the .completed event is durable

            else:
                if etype not in flagged_types:
                    trace.stage("unexpected_event", type=etype)
                    flagged_types.add(etype)

        trace.stage("call_completed")
