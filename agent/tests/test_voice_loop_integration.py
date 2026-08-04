"""Integration harness + instrumentation for the xAI voice call->booking loop.

These exercise OUR half of the loop deterministically (webhook routing,
signature verification, call orchestration, tool call, job persistence) plus
the call-trace instrumentation — so the only thing left unverified for a real
call is xAI's own provider behavior (audio + real event/payload shapes).
"""
import asyncio
import json
from pathlib import Path

import pytest
from sqlmodel import Session, select

from db_models import Business, Job


# ---- Fakes for the xAI realtime WebSocket ----------------------------------

class FakeWS:
    """Stand-in for the xAI realtime WebSocket: yields a scripted list of
    events to `async for`, and records what run_call sends back."""

    def __init__(self, events):
        self._raw = [json.dumps(e) for e in events]
        self.sent = []

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    async def __aiter__(self):
        for r in self._raw:
            yield r


def connector_for(ws):
    """A drop-in for run_call's `connect` param: an async context manager
    factory that ignores the URL and hands back our FakeWS."""
    class _CM:
        async def __aenter__(self):
            return ws

        async def __aexit__(self, *a):
            return False

    def connect(call_id):
        return _CM()

    return connect


def _seed_business(engine) -> Business:
    with Session(engine) as s:
        b = Business(business_name="Ridgeline Plumbing", trade="Plumbing",
                     escalation_phone="512-555-0148", inbound_number="+15125550100",
                     xai_phone_number="+15125550100", frontdesk_live=True)
        s.add(b)
        s.commit()
        s.refresh(b)
        return b


LOG_JOB_EVENT = {
    "type": "response.function_call_arguments.done",
    "name": "log_job",
    "call_id": "fc_1",
    "arguments": json.dumps({
        "service_type": "burst pipe", "urgency": "emergency", "customer_name": "Jane Doe",
    }),
}
GREETING_DONE = {
    "type": "response.done",
    "response": {"output": [{"content": [{"transcript": "Ridgeline Plumbing, how can I help?"}]}]},
}


# ---- CallTrace (instrumentation) -------------------------------------------

def test_call_trace_stage_records_elapsed_ms():
    from call_trace import CallTrace

    trace = CallTrace("call_abc")
    trace.stage("webhook_received")
    trace.stage("ws_connected")

    stages = [r for r in trace.records if r["kind"] == "stage"]
    assert [s["stage"] for s in stages] == ["webhook_received", "ws_connected"]
    assert all(isinstance(s["t_ms"], (int, float)) and s["t_ms"] >= 0 for s in stages)
    # Later stages are recorded at a monotonically non-decreasing offset.
    assert stages[1]["t_ms"] >= stages[0]["t_ms"]


def test_call_trace_stage_carries_extra_fields():
    from call_trace import CallTrace

    trace = CallTrace("call_abc")
    trace.stage("tool_invoked", tool="log_job")
    trace.stage("job_persisted", job_id=7)

    by_stage = {r["stage"]: r for r in trace.records if r["kind"] == "stage"}
    assert by_stage["tool_invoked"]["tool"] == "log_job"
    assert by_stage["job_persisted"]["job_id"] == 7


def test_call_trace_event_captures_raw_event():
    from call_trace import CallTrace

    trace = CallTrace("call_abc")
    trace.event({"type": "response.done", "response": {"x": 1}})

    events = [r for r in trace.records if r["kind"] == "event"]
    assert events[0]["type"] == "response.done"
    assert events[0]["raw"] == {"type": "response.done", "response": {"x": 1}}


def test_call_trace_webhook_captures_headers_and_body():
    from call_trace import CallTrace

    trace = CallTrace("call_abc")
    trace.webhook({"webhook-id": "wh_1"}, b'{"type":"realtime.call.incoming"}')

    hooks = [r for r in trace.records if r["kind"] == "webhook"]
    assert hooks[0]["headers"] == {"webhook-id": "wh_1"}
    assert "realtime.call.incoming" in hooks[0]["body"]


def test_call_trace_writes_jsonl_to_capture_dir(tmp_path):
    from call_trace import CallTrace

    trace = CallTrace("call_xyz", capture_dir=tmp_path)
    trace.stage("webhook_received")
    trace.event({"type": "response.done"})

    capture_file = tmp_path / "call_xyz.jsonl"
    assert capture_file.exists()
    lines = [json.loads(ln) for ln in capture_file.read_text().splitlines()]
    assert lines[0]["stage"] == "webhook_received"
    assert lines[1]["type"] == "response.done"


# ---- Call trace log rotation: the shared quarantine file must not grow
# without bound -------------------------------------------------------------
#
# Only `_inbound.jsonl` — the pre-auth quarantine file EVERY webhook
# delivery appends to, forever, with no per-call boundary — is rotated. A
# per-call trace file ({call_id}.jsonl) is deliberately NEVER rotated: it's
# already scoped to one call's own short lifetime (further bounded by
# Priority 4's duration cap), and splitting a single call's trace across
# files would break the exact debugging workflow this instrumentation
# exists for — a founder opening ONE file to see the WHOLE call.
#
# Rotation happens strictly BETWEEN writes: capture_unverified opens,
# writes ONE complete line, and closes on every call — no long-lived handle
# to corrupt — so the rotation check runs before that atomic write ever
# begins. A record can never straddle the rotation boundary.
#
# "Concurrent" is scoped to what this codebase's actual deployment model
# allows: capture_unverified is synchronous with no internal `await`, called
# directly (never via run_in_threadpool/to_thread) from the webhook route —
# so within one process, asyncio's cooperative scheduling already guarantees
# calls run one at a time, never truly interleaved (the same single-process
# assumption locks.py documents for conversation_lock). Rapid sequential
# calls are therefore the realistic "concurrent deliveries" scenario here.

BIG_BODY = b"x" * 200   # ~304 bytes once wrapped in the JSONL record


def test_log_below_threshold_does_not_rotate(tmp_path):
    from call_trace import CallTrace

    for i in range(3):
        CallTrace.capture_unverified(tmp_path, {"webhook-id": f"wh_{i}"}, b"small",
                                     max_bytes=10_000, max_backups=2)

    assert (tmp_path / "_inbound.jsonl").exists()
    assert not (tmp_path / "_inbound.jsonl.1").exists()
    assert len((tmp_path / "_inbound.jsonl").read_text().splitlines()) == 3


def test_rotation_at_threshold(tmp_path):
    from call_trace import CallTrace

    for i in range(3):
        CallTrace.capture_unverified(tmp_path, {"webhook-id": f"wh_{i}"}, BIG_BODY,
                                     max_bytes=250, max_backups=2)

    assert (tmp_path / "_inbound.jsonl").exists()
    assert (tmp_path / "_inbound.jsonl.1").exists()


def test_multiple_rotations_shift_the_backup_chain(tmp_path):
    from call_trace import CallTrace

    for i in range(12):
        CallTrace.capture_unverified(tmp_path, {"webhook-id": f"wh_{i}"}, BIG_BODY,
                                     max_bytes=250, max_backups=2)

    files = sorted(p.name for p in tmp_path.glob("_inbound.jsonl*"))
    assert files == ["_inbound.jsonl", "_inbound.jsonl.1", "_inbound.jsonl.2"]


def test_trace_integrity_after_rotation(tmp_path):
    """Every record ever written is recoverable from SOME file, and every
    line in every file is complete, valid JSON — proving rotation never
    splits or corrupts a record. Write count stays within the backup cap so
    nothing is evicted yet (eviction itself is proven separately by
    test_old_backups_beyond_the_cap_are_deleted) — this test is only about
    corruption, not retention."""
    from call_trace import CallTrace

    for i in range(4):
        CallTrace.capture_unverified(tmp_path, {"webhook-id": f"wh_{i}"}, BIG_BODY,
                                     max_bytes=250, max_backups=5)

    all_ids = []
    for path in tmp_path.glob("_inbound.jsonl*"):
        for line in path.read_text().splitlines():
            all_ids.append(json.loads(line)["headers"]["webhook-id"])   # raises on any corrupt line

    assert sorted(all_ids) == sorted(f"wh_{i}" for i in range(4))


def test_old_backups_beyond_the_cap_are_deleted(tmp_path):
    """Rotation alone doesn't prevent unbounded growth — it just reshapes it
    into many files — unless old backups are actually deleted."""
    from call_trace import CallTrace

    for i in range(20):
        CallTrace.capture_unverified(tmp_path, {"webhook-id": f"wh_{i}"}, BIG_BODY,
                                     max_bytes=250, max_backups=2)

    backups = list(tmp_path.glob("_inbound.jsonl.*"))
    assert len(backups) == 2


def test_rapid_successive_deliveries_do_not_corrupt_the_log(tmp_path):
    """The realistic 'concurrent deliveries' scenario for this codebase's
    single-process, no-thread-offload deployment model (see section note).
    Write count stays within the backup cap — this is about corruption
    under rapid writes, not retention (covered separately)."""
    from call_trace import CallTrace

    for i in range(4):
        CallTrace.capture_unverified(tmp_path, {"webhook-id": f"wh_{i}"}, BIG_BODY,
                                     max_bytes=300, max_backups=3)

    all_ids = []
    for path in tmp_path.glob("_inbound.jsonl*"):
        for line in path.read_text().splitlines():
            all_ids.append(json.loads(line)["headers"]["webhook-id"])

    assert sorted(all_ids) == sorted(f"wh_{i}" for i in range(4))


def test_concurrent_per_call_and_quarantine_writes_do_not_interfere(tmp_path):
    """Interleaved per-call trace writes (two different calls) and
    quarantine-file writes must stay fully independent: neither corrupts nor
    truncates the other, and the per-call files are never touched by
    quarantine rotation."""
    from call_trace import CallTrace

    trace_a = CallTrace("call_a", capture_dir=tmp_path)
    trace_b = CallTrace("call_b", capture_dir=tmp_path)
    for i in range(10):
        trace_a.stage(f"stage_a_{i}")
        CallTrace.capture_unverified(tmp_path, {"webhook-id": f"wh_{i}"}, BIG_BODY,
                                     max_bytes=250, max_backups=2)
        trace_b.stage(f"stage_b_{i}")
    trace_a.close()
    trace_b.close()

    a_lines = (tmp_path / "call_a.jsonl").read_text().splitlines()
    b_lines = (tmp_path / "call_b.jsonl").read_text().splitlines()
    assert len(a_lines) == 10 and len(b_lines) == 10
    assert [json.loads(ln)["stage"] for ln in a_lines] == [f"stage_a_{i}" for i in range(10)]
    assert [json.loads(ln)["stage"] for ln in b_lines] == [f"stage_b_{i}" for i in range(10)]
    # Quarantine rotation happened, but never touched the per-call files.
    assert (tmp_path / "_inbound.jsonl.1").exists()


def test_a_per_call_trace_is_never_rotated_or_split(tmp_path):
    """The one file per call must stay whole even with a very verbose call
    that would exceed the same threshold used for the quarantine file — a
    single call's trace is never a rotation target, by design."""
    from call_trace import CallTrace

    trace = CallTrace("call_big", capture_dir=tmp_path)
    for i in range(50):
        trace.event({"type": "response.audio.delta", "chunk": "x" * 50, "i": i})
    trace.close()

    assert (tmp_path / "call_big.jsonl").exists()
    assert not (tmp_path / "call_big.jsonl.1").exists()
    assert len((tmp_path / "call_big.jsonl").read_text().splitlines()) == 50


# ---- run_call orchestration (our half of the loop) -------------------------

def test_run_call_persists_job_from_log_job_event(test_engine):
    from xai_voice_adapter import run_call
    from call_trace import CallTrace

    client = _seed_business(test_engine)
    ws = FakeWS([GREETING_DONE, LOG_JOB_EVENT])

    asyncio.run(run_call("call_1", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ws), trace=CallTrace("call_1")))

    with Session(test_engine) as s:
        jobs = s.exec(select(Job).where(Job.business_id == client.id)).all()
    assert len(jobs) == 1
    assert jobs[0].service_type == "burst pipe"
    assert jobs[0].urgency == "emergency"
    assert jobs[0].customer_name == "Jane Doe"


def test_run_call_persists_preferred_window_from_log_job_event(test_engine):
    """Sprint 1 (conversation-quality audit): the tool schema's new field
    flows through the real dispatch path, not just the unit-level
    bookings.book_job call — end to end through _handle_function_call."""
    from xai_voice_adapter import run_call
    from call_trace import CallTrace

    client = _seed_business(test_engine)
    event_with_window = dict(LOG_JOB_EVENT, arguments=json.dumps({
        "service_type": "burst pipe", "urgency": "emergency",
        "customer_name": "Jane Doe", "preferred_window": "Thursday afternoon",
    }))
    ws = FakeWS([event_with_window])

    asyncio.run(run_call("call_pw1", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ws), trace=CallTrace("call_pw1")))

    with Session(test_engine) as s:
        job = s.exec(select(Job).where(Job.business_id == client.id)).first()
    assert job.preferred_window == "Thursday afternoon"


def test_run_call_defaults_callback_number_to_caller(test_engine):
    from xai_voice_adapter import run_call
    from call_trace import CallTrace

    client = _seed_business(test_engine)
    ws = FakeWS([LOG_JOB_EVENT])  # no callback_number in args

    asyncio.run(run_call("call_2", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ws), trace=CallTrace("call_2")))

    with Session(test_engine) as s:
        job = s.exec(select(Job).where(Job.business_id == client.id)).first()
    assert job.callback_number == "+15125559999"


def test_run_call_records_trace_stages(test_engine):
    from xai_voice_adapter import run_call
    from call_trace import CallTrace

    client = _seed_business(test_engine)
    trace = CallTrace("call_3")
    ws = FakeWS([GREETING_DONE, LOG_JOB_EVENT])

    asyncio.run(run_call("call_3", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ws), trace=trace))

    stages = [r["stage"] for r in trace.records if r["kind"] == "stage"]
    for expected in ("ws_connected", "first_ai_response", "tool_invoked",
                     "job_persisted", "owner_notified", "call_completed"):
        assert expected in stages, f"missing stage {expected}: {stages}"
    tool = next(r for r in trace.records if r.get("stage") == "tool_invoked")
    assert tool["tool"] == "log_job"


def test_run_call_flags_unexpected_event_type_once(test_engine):
    from xai_voice_adapter import run_call
    from call_trace import CallTrace

    client = _seed_business(test_engine)
    trace = CallTrace("call_4")
    ws = FakeWS([
        {"type": "response.audio.delta"},
        {"type": "response.audio.delta"},
        LOG_JOB_EVENT,
    ])

    asyncio.run(run_call("call_4", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ws), trace=trace))

    unexpected = [r for r in trace.records
                  if r.get("stage") == "unexpected_event" and r.get("type") == "response.audio.delta"]
    assert len(unexpected) == 1, "should flag an unhandled event type exactly once"
    # But every raw event is still captured, both deltas included.
    deltas = [r for r in trace.records if r["kind"] == "event" and r["type"] == "response.audio.delta"]
    assert len(deltas) == 2


# ---- Caller transcript capture ----------------------------------------------
#
# Closes the third gap from the 2026-07-29 voice-loop audit: only the
# assistant's side of a call was ever persisted (via response.done). The
# caller's own words were silently dropped into the generic "unexpected
# event" trace bucket.
#
# The event persisted here is `conversation.item.input_audio_transcription.
# completed` — the OpenAI-Realtime-compatible shape xAI's own docs are
# written against (the same assumption _extract_transcript already makes for
# the assistant side, and explicitly flagged there as "confirm against a
# real payload"). `.delta` carries streaming partial text as the audio is
# still being transcribed; only `.completed` is durable — the same
# final-only rule the assistant side already follows (only response.done is
# persisted, never response.audio_transcript.delta).
#
# Dedup reuses Message.external_id exactly as service.py's SMS path already
# does for Twilio MessageSid retries — here keyed on xAI's own stable
# item_id instead, same guarantee, same column, no new mechanism.

def _transcription_completed(item_id: str, transcript) -> dict:
    return {
        "type": "conversation.item.input_audio_transcription.completed",
        "item_id": item_id,
        "transcript": transcript,
    }


def _transcription_delta(item_id: str, delta: str = "burst") -> dict:
    return {
        "type": "conversation.item.input_audio_transcription.delta",
        "item_id": item_id,
        "delta": delta,
    }


def test_a_single_caller_utterance_is_persisted_as_a_user_message(test_engine):
    from xai_voice_adapter import run_call
    from call_trace import CallTrace
    from db_models import Message

    client = _seed_business(test_engine)
    ws = FakeWS([_transcription_completed("item_1", "My AC stopped working")])

    asyncio.run(run_call("call_t1", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ws), trace=CallTrace("call_t1")))

    with Session(test_engine) as s:
        msgs = s.exec(select(Message).where(Message.business_id == client.id)).all()
    assert len(msgs) == 1
    assert msgs[0].role == "user"
    assert json.loads(msgs[0].content_json) == [{"type": "text", "text": "My AC stopped working"}]
    assert msgs[0].external_id == "item_1"


def test_a_multi_turn_conversation_preserves_order(test_engine):
    """User -> assistant -> user, in the order the events actually arrive —
    the same insertion-order-is-conversation-order rule the SMS path and the
    existing assistant-transcript branch already rely on."""
    from xai_voice_adapter import run_call
    from call_trace import CallTrace
    from db_models import Message

    client = _seed_business(test_engine)
    ws = FakeWS([
        _transcription_completed("item_1", "My AC stopped working"),
        GREETING_DONE,
        _transcription_completed("item_2", "It's in the living room"),
    ])

    asyncio.run(run_call("call_t2", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ws), trace=CallTrace("call_t2")))

    with Session(test_engine) as s:
        msgs = s.exec(select(Message).where(Message.business_id == client.id)
                      .order_by(Message.id)).all()
    assert [m.role for m in msgs] == ["user", "assistant", "user"]
    assert json.loads(msgs[0].content_json)[0]["text"] == "My AC stopped working"
    assert json.loads(msgs[2].content_json)[0]["text"] == "It's in the living room"


def test_a_duplicate_transcript_event_does_not_create_a_second_message(test_engine):
    """The same item_id delivered twice (a transport-level redelivery, or a
    correction re-emitting the same completed event) must not double the
    caller's turn in history — exactly the guarantee service.py already gives
    a redelivered Twilio MessageSid."""
    from xai_voice_adapter import run_call
    from call_trace import CallTrace
    from db_models import Message

    client = _seed_business(test_engine)
    ws = FakeWS([
        _transcription_completed("item_1", "My AC stopped working"),
        _transcription_completed("item_1", "My AC stopped working"),
    ])

    asyncio.run(run_call("call_t3", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ws), trace=CallTrace("call_t3")))

    with Session(test_engine) as s:
        msgs = s.exec(select(Message).where(Message.business_id == client.id)).all()
    assert len(msgs) == 1


def test_transcript_followed_by_a_tool_call_persists_both(test_engine, monkeypatch):
    """The new capture must not interfere with the existing log_job path."""
    from xai_voice_adapter import run_call
    from call_trace import CallTrace
    from db_models import Message

    import xai_voice_adapter as adapter
    monkeypatch.setattr(adapter, "notify_owner_of_booking", lambda *a, **k: True)
    client = _seed_business(test_engine)
    ws = FakeWS([
        _transcription_completed("item_1", "It's a burst pipe, emergency"),
        LOG_JOB_EVENT,
    ])

    asyncio.run(run_call("call_t4", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ws), trace=CallTrace("call_t4")))

    with Session(test_engine) as s:
        msgs = s.exec(select(Message).where(Message.business_id == client.id)).all()
        jobs = s.exec(select(Job).where(Job.business_id == client.id)).all()
    assert any(m.role == "user" for m in msgs)
    assert len(jobs) == 1


def test_transcript_followed_by_assistant_response_persists_both_in_order(test_engine):
    from xai_voice_adapter import run_call
    from call_trace import CallTrace
    from db_models import Message

    client = _seed_business(test_engine)
    ws = FakeWS([
        _transcription_completed("item_1", "My AC stopped working"),
        GREETING_DONE,
    ])

    asyncio.run(run_call("call_t5", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ws), trace=CallTrace("call_t5")))

    with Session(test_engine) as s:
        msgs = s.exec(select(Message).where(Message.business_id == client.id)
                      .order_by(Message.id)).all()
    assert [m.role for m in msgs] == ["user", "assistant"]


def test_an_empty_transcript_is_not_persisted(test_engine):
    from xai_voice_adapter import run_call
    from call_trace import CallTrace
    from db_models import Message

    client = _seed_business(test_engine)
    ws = FakeWS([_transcription_completed("item_1", "")])

    asyncio.run(run_call("call_t6", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ws), trace=CallTrace("call_t6")))

    with Session(test_engine) as s:
        msgs = s.exec(select(Message).where(Message.business_id == client.id)).all()
    assert msgs == []


def test_a_malformed_transcript_event_is_ignored_without_crashing_the_call(test_engine):
    """Missing item_id, missing transcript, and a non-string transcript must
    all no-op — a transcription-pipeline hiccup must never take down the
    live call the way an unhandled exception would (run_call's top-level
    handler would otherwise text the owner "call dropped" over a missing
    field, which would be a false alarm)."""
    from xai_voice_adapter import run_call
    from call_trace import CallTrace
    from db_models import Message

    client = _seed_business(test_engine)
    ws = FakeWS([
        {"type": "conversation.item.input_audio_transcription.completed", "item_id": "item_1"},
        {"type": "conversation.item.input_audio_transcription.completed", "transcript": "no id"},
        {"type": "conversation.item.input_audio_transcription.completed",
         "item_id": "item_2", "transcript": None},
        GREETING_DONE,
    ])
    trace = CallTrace("call_t7")

    asyncio.run(run_call("call_t7", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ws), trace=trace))

    with Session(test_engine) as s:
        msgs = s.exec(select(Message).where(Message.business_id == client.id)).all()
    assert [m.role for m in msgs] == ["assistant"]   # only the greeting persisted
    assert "call_failed" not in [r["stage"] for r in trace.records if r["kind"] == "stage"]
    assert "call_completed" in [r["stage"] for r in trace.records if r["kind"] == "stage"]


def test_a_delta_event_is_recognized_and_ignored_not_flagged_unexpected(test_engine):
    """Partial transcript chunks are expected traffic, not a protocol
    surprise — they should not pollute the unexpected_event trace the way a
    genuinely unhandled event type does."""
    from xai_voice_adapter import run_call
    from call_trace import CallTrace
    from db_models import Message

    client = _seed_business(test_engine)
    trace = CallTrace("call_t8")
    ws = FakeWS([_transcription_delta("item_1"), GREETING_DONE])

    asyncio.run(run_call("call_t8", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ws), trace=trace))

    unexpected = [r for r in trace.records if r.get("stage") == "unexpected_event"]
    assert unexpected == []
    with Session(test_engine) as s:
        msgs = s.exec(select(Message).where(Message.business_id == client.id)).all()
    assert [m.role for m in msgs] == ["assistant"]


# ---- Webhook: routing, signature, raw capture, trace hand-off --------------

import base64
import hashlib
import hmac

import app as app_module
import db as db_module
import portal as portal_module
from starlette.testclient import TestClient

WEBHOOK_SECRET = base64.b64encode(b"voice-loop-test-secret-0123456789").decode()


def _incoming_call_body(to_number: str, call_id="call_wh", from_number="+15125559999") -> bytes:
    return json.dumps({
        "type": "realtime.call.incoming",
        "data": {
            "call_id": call_id,
            "sip_headers": [
                {"name": "To", "value": to_number},
                {"name": "From", "value": from_number},
            ],
        },
    }).encode()


def _sign(secret_b64: str, webhook_id: str, ts: str, body: bytes) -> str:
    secret_bytes = base64.b64decode(secret_b64)
    signed = f"{webhook_id}.{ts}.".encode() + body
    return "v1," + base64.b64encode(hmac.new(secret_bytes, signed, hashlib.sha256).digest()).decode()


def _voice_client(test_engine, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    monkeypatch.setattr(app_module, "VOICE_CAPTURE_DIR", tmp_path / "captures")
    with Session(test_engine) as s:
        b = Business(business_name="Ridgeline Plumbing", inbound_number="+15125550100",
                     xai_phone_number="+15125550100", xai_signing_secret=WEBHOOK_SECRET,
                     frontdesk_live=True)
        s.add(b); s.commit()
    return TestClient(app_module.app)


def _patch_run(monkeypatch):
    """Replace run_xai_call with a sync recorder returning a real coroutine, so
    the webhook's asyncio.create_task gets something awaitable without opening a
    real xAI connection."""
    recorded = {}

    def fake_run(call_id, client, caller_number, session_factory, connect=None, trace=None):
        recorded.update(call_id=call_id, caller_number=caller_number, trace=trace, scheduled=True)

        async def _noop():
            return None
        return _noop()

    monkeypatch.setattr(app_module, "run_xai_call", fake_run)
    return recorded


def test_webhook_valid_signature_schedules_call(test_engine, monkeypatch, tmp_path):
    client = _voice_client(test_engine, monkeypatch, tmp_path)
    recorded = _patch_run(monkeypatch)
    body = _incoming_call_body("+15125550100")
    headers = _fresh_signed(body, WEBHOOK_SECRET)

    resp = client.post("/webhook/xai-incoming-call", content=body, headers=headers)

    assert resp.status_code == 200
    assert recorded.get("scheduled") is True
    assert recorded["call_id"] == "call_wh"
    assert recorded["caller_number"] == "+15125559999"


def test_webhook_bad_signature_returns_401(test_engine, monkeypatch, tmp_path):
    client = _voice_client(test_engine, monkeypatch, tmp_path)
    recorded = _patch_run(monkeypatch)
    body = _incoming_call_body("+15125550100")
    import time as _time
    headers = {"webhook-id": "wh_1", "webhook-timestamp": str(int(_time.time())),
               "webhook-signature": "v1," + base64.b64encode(b"wrong").decode()}

    resp = client.post("/webhook/xai-incoming-call", content=body, headers=headers)

    assert resp.status_code == 401
    assert recorded.get("scheduled") is None  # never dispatched


def test_webhook_unknown_number_returns_204(test_engine, monkeypatch, tmp_path):
    client = _voice_client(test_engine, monkeypatch, tmp_path)
    recorded = _patch_run(monkeypatch)
    body = _incoming_call_body("+19998887777")  # not a registered number
    headers = _fresh_signed(body, WEBHOOK_SECRET)

    resp = client.post("/webhook/xai-incoming-call", content=body, headers=headers)

    assert resp.status_code == 204
    assert recorded.get("scheduled") is None


def test_webhook_captures_raw_body_into_trace_from_receipt(test_engine, monkeypatch, tmp_path):
    client = _voice_client(test_engine, monkeypatch, tmp_path)
    recorded = _patch_run(monkeypatch)
    body = _incoming_call_body("+15125550100")
    headers = _fresh_signed(body, WEBHOOK_SECRET)

    client.post("/webhook/xai-incoming-call", content=body, headers=headers)

    trace = recorded["trace"]
    assert trace is not None, "webhook must create the trace so t0 = call receipt"
    kinds = {r["kind"] for r in trace.records}
    assert "webhook" in kinds
    hook = next(r for r in trace.records if r["kind"] == "webhook")
    assert "realtime.call.incoming" in hook["body"]
    stages = [r["stage"] for r in trace.records if r["kind"] == "stage"]
    assert "webhook_received" in stages


def test_webhook_rejects_path_traversal_call_id_without_writing_outside_capture_dir(
    test_engine, monkeypatch, tmp_path
):
    """A malicious call_id must not steer any pre-auth filesystem write.

    Regression for CWE-22/CWE-73: the raw-webhook capture used to open
    `{capture_dir}/{call_id}.jsonl` before signature verification, so an
    absolute or `../`-laden call_id was an unauthenticated arbitrary
    file-create/append primitive. The value is now rejected as malformed and
    only the fixed `_inbound.jsonl` quarantine file may ever be written.
    """
    client = _voice_client(test_engine, monkeypatch, tmp_path)
    recorded = _patch_run(monkeypatch)
    capture_dir = tmp_path / "captures"
    # An absolute call_id pointing inside tmp_path proves the pathlib
    # absolute-override vector is closed, and keeps the test hermetic.
    traversal_target = tmp_path / "pwned"
    body = _incoming_call_body("+15125550100", call_id=str(traversal_target))
    headers = _fresh_signed(body, WEBHOOK_SECRET)

    resp = client.post("/webhook/xai-incoming-call", content=body, headers=headers)

    # Malformed call_id -> dropped as unparseable, no live call scheduled.
    assert resp.status_code == 204
    assert recorded.get("scheduled") is None
    # Nothing was written to the traversal target...
    assert not Path(f"{traversal_target}.jsonl").exists()
    # ...and the only file allowed inside the capture dir is the fixed
    # quarantine file (no attacker-named per-call file was created pre-auth).
    written = {p.name for p in capture_dir.glob("*.jsonl")} if capture_dir.exists() else set()
    assert written <= {"_inbound.jsonl"}


def test_call_trace_sanitizes_call_id_and_never_escapes_capture_dir(tmp_path):
    """Defense-in-depth: even handed a traversal call_id directly, CallTrace
    keeps the write inside capture_dir under a sanitized filename."""
    from call_trace import CallTrace

    outside = tmp_path / "outside.jsonl"
    capture = tmp_path / "cap"
    trace = CallTrace("../outside", capture_dir=capture)

    trace.stage("webhook_received")

    assert not outside.exists(), "traversal call_id must not write outside capture_dir"
    written = list(capture.glob("*.jsonl"))
    assert written, "the record should still be captured, just under a safe name"
    assert all(p.parent == capture for p in written)


# ---- Customer identity: voice reaches parity with SMS -----------------------
#
# service.py's SMS path calls repositories.get_or_create_customer before
# book_job and passes the resulting customer_id through — every SMS-booked
# Job is linked to a real Customer row. The voice path never did this (a
# confirmed, not hypothetical, gap from the 2026-07-29 audit): every
# voice-booked Job had customer_id=None forever, so any feature that joins on
# Customer (Retention Manager, Reviews, Customer Success outcomes) silently
# missed every customer who has only ever called, never texted.
#
# Keyed on caller_number (the real phone), never `thread`
# (`xai-voice:{call_id}`) — a customer's identity must be the same across
# every call they ever make, and the thread id is unique per call by design.

def test_log_job_creates_a_customer_and_links_the_job(test_engine):
    from xai_voice_adapter import run_call
    from call_trace import CallTrace

    client = _seed_business(test_engine)
    ws = FakeWS([LOG_JOB_EVENT])

    asyncio.run(run_call("call_cust1", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ws), trace=CallTrace("call_cust1")))

    with Session(test_engine) as s:
        from db_models import Customer
        customer = s.exec(select(Customer).where(
            Customer.business_id == client.id, Customer.phone == "+15125559999")).first()
        job = s.exec(select(Job).where(Job.business_id == client.id)).first()

    assert customer is not None
    assert customer.name == "Jane Doe"          # log_job's customer_name, same as SMS
    assert job.customer_id == customer.id


def test_a_repeat_caller_across_two_separate_calls_reuses_the_same_customer(test_engine):
    """Two different call_ids (each its own thread) from the same real phone
    number must resolve to one Customer, not two — identity is the caller's
    phone number, never the per-call thread id."""
    from xai_voice_adapter import run_call
    from call_trace import CallTrace

    client = _seed_business(test_engine)

    asyncio.run(run_call("call_cust2a", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(FakeWS([LOG_JOB_EVENT])), trace=CallTrace("call_cust2a")))
    asyncio.run(run_call("call_cust2b", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(FakeWS([LOG_JOB_EVENT])), trace=CallTrace("call_cust2b")))

    with Session(test_engine) as s:
        from db_models import Customer
        customers = s.exec(select(Customer).where(
            Customer.business_id == client.id, Customer.phone == "+15125559999")).all()
        jobs = s.exec(select(Job).where(Job.business_id == client.id)).all()

    assert len(customers) == 1
    assert len(jobs) == 2                       # two calls, two escalation-free jobs
    assert jobs[0].customer_id == jobs[1].customer_id == customers[0].id


def test_a_caller_reuses_the_customer_record_their_sms_conversation_already_created(
    test_engine, monkeypatch
):
    """Cross-channel identity: get_or_create_customer keys on (business_id,
    phone) alone, so a customer who has already texted this business and
    then calls must resolve to the SAME Customer row, not a second one."""
    from xai_voice_adapter import run_call
    from call_trace import CallTrace
    from repositories import get_or_create_customer

    client = _seed_business(test_engine)
    with Session(test_engine) as s:
        existing = get_or_create_customer(s, client.id, "+15125559999", name="Jane Doe")
        existing_id = existing.id

    ws = FakeWS([LOG_JOB_EVENT])
    asyncio.run(run_call("call_cust3", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ws), trace=CallTrace("call_cust3")))

    with Session(test_engine) as s:
        from db_models import Customer
        customers = s.exec(select(Customer).where(
            Customer.business_id == client.id, Customer.phone == "+15125559999")).all()
        job = s.exec(select(Job).where(Job.business_id == client.id)).first()

    assert len(customers) == 1
    assert job.customer_id == existing_id


def test_alert_owner_also_links_a_customer_to_the_escalation_job(test_engine, monkeypatch):
    """A Job is a Job regardless of which tool created it — an escalation
    call is from a real, identifiable caller too."""
    import xai_voice_adapter as adapter
    monkeypatch.setattr(adapter, "notify_owner_of_escalation", lambda *a, **k: True)
    client = _seed_business(test_engine)
    ws = FakeWS([ALERT_OWNER_EVENT])

    _run(client, ws, test_engine)

    with Session(test_engine) as s:
        from db_models import Customer
        customer = s.exec(select(Customer).where(
            Customer.business_id == client.id, Customer.phone == "+15125559999")).first()
        job = s.exec(select(Job).where(Job.business_id == client.id)).first()

    assert customer is not None
    assert job.customer_id == customer.id


# ---- Escalation: honest emergency path --------------------------------------

ALERT_OWNER_EVENT = {
    "type": "response.function_call_arguments.done",
    "name": "alert_owner",
    "call_id": "fc_esc",
    "arguments": json.dumps({"destination": "512-555-0148", "reason": "gas smell in kitchen"}),
}


def _run(client, ws, test_engine, call_id="call_esc", trace=None):
    from xai_voice_adapter import run_call
    from call_trace import CallTrace
    asyncio.run(run_call(call_id, client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ws), trace=trace or CallTrace(call_id)))


def test_alert_owner_persists_emergency_lead(test_engine, monkeypatch):
    import xai_voice_adapter as adapter
    monkeypatch.setattr(adapter, "notify_owner_of_escalation", lambda *a, **k: True)
    client = _seed_business(test_engine)
    ws = FakeWS([ALERT_OWNER_EVENT])

    _run(client, ws, test_engine)

    with Session(test_engine) as s:
        jobs = s.exec(select(Job).where(Job.business_id == client.id)).all()
    assert len(jobs) == 1
    assert jobs[0].urgency == "emergency"
    assert "gas smell in kitchen" in (jobs[0].notes or "") + jobs[0].service_type
    assert jobs[0].callback_number == "+15125559999"


def test_alert_owner_texts_owner_and_reports_honest_success(test_engine, monkeypatch):
    import xai_voice_adapter as adapter
    calls = []
    monkeypatch.setattr(adapter, "notify_owner_of_escalation",
                        lambda business, caller, reason: calls.append((business.id, caller, reason)) or True)
    client = _seed_business(test_engine)
    ws = FakeWS([ALERT_OWNER_EVENT])

    _run(client, ws, test_engine)

    assert calls == [(client.id, "+15125559999", "gas smell in kitchen")]
    outputs = [m for m in ws.sent if m.get("type") == "conversation.item.create"]
    payload = json.loads(outputs[0]["item"]["output"])
    assert payload["status"] == "owner_alerted"


def test_alert_owner_reports_failure_with_fallback_number(test_engine, monkeypatch):
    import xai_voice_adapter as adapter
    monkeypatch.setattr(adapter, "notify_owner_of_escalation", lambda *a, **k: False)
    client = _seed_business(test_engine)
    ws = FakeWS([ALERT_OWNER_EVENT])

    _run(client, ws, test_engine)

    outputs = [m for m in ws.sent if m.get("type") == "conversation.item.create"]
    payload = json.loads(outputs[0]["item"]["output"])
    assert payload["status"] == "alert_failed"
    assert payload["owner_number"] == "512-555-0148"
    # The lead must still be persisted even when the text failed.
    with Session(test_engine) as s:
        assert s.exec(select(Job).where(Job.business_id == client.id)).first() is not None


def test_a_second_alert_owner_in_the_same_call_does_not_repage_the_owner(test_engine, monkeypatch):
    """Closes the gap in the 2026-07-29 audit: alert_owner had no duplicate
    protection at all, unlike log_job. Two escalations in one call must
    produce exactly one Job and one owner text, not two."""
    import xai_voice_adapter as adapter
    calls = []
    monkeypatch.setattr(adapter, "notify_owner_of_escalation",
                        lambda business, caller, reason: calls.append(reason) or True)
    client = _seed_business(test_engine)
    second_event = dict(ALERT_OWNER_EVENT, call_id="fc_esc2",
                        arguments=json.dumps({"reason": "gas smell in kitchen"}))
    ws = FakeWS([ALERT_OWNER_EVENT, second_event])

    _run(client, ws, test_engine)

    assert len(calls) == 1, "the owner must be paged exactly once, not twice"
    with Session(test_engine) as s:
        jobs = s.exec(select(Job).where(Job.business_id == client.id)).all()
    assert len(jobs) == 1
    outputs = [json.loads(m["item"]["output"]) for m in ws.sent
              if m.get("type") == "conversation.item.create"]
    # Both tool calls get an honest "owner_alerted" response — the second one
    # truthfully reflects that the owner already knows, not a fresh page.
    assert [o["status"] for o in outputs] == ["owner_alerted", "owner_alerted"]


def test_a_failed_alert_owner_is_retried_on_a_second_call_in_the_same_conversation(
    test_engine, monkeypatch
):
    """The first page never went through — a second alert_owner call must
    actually retry the SMS, not be silently treated as a duplicate."""
    import xai_voice_adapter as adapter
    calls = []
    monkeypatch.setattr(adapter, "notify_owner_of_escalation",
                        lambda business, caller, reason: calls.append(reason) or False)
    client = _seed_business(test_engine)
    second_event = dict(ALERT_OWNER_EVENT, call_id="fc_esc2")
    ws = FakeWS([ALERT_OWNER_EVENT, second_event])

    _run(client, ws, test_engine)

    assert len(calls) == 2, "a failed page must be retried, never swallowed"
    with Session(test_engine) as s:
        jobs = s.exec(select(Job).where(Job.business_id == client.id)).all()
    assert len(jobs) == 1
    outputs = [json.loads(m["item"]["output"]) for m in ws.sent
              if m.get("type") == "conversation.item.create"]
    assert [o["status"] for o in outputs] == ["alert_failed", "alert_failed"]


def test_voice_prompt_is_honest_about_escalation():
    from engine import build_voice_system_prompt
    from models import ClientConfig

    cfg = ClientConfig(client_id="1", business_name="Ridgeline", trade="Plumbing",
                       services=["drains"], hours="Mon-Sat", pricing_faq="x",
                       escalation_phone="512-555-0148", answer_mode="primary",
                       tone="friendly")
    prompt = build_voice_system_prompt(cfg)
    lower = prompt.lower()
    assert "transfer" not in lower, "must never promise a transfer that doesn't exist"
    assert "911" in prompt
    assert "512-555-0148" in prompt
    assert "alert_owner" in prompt


def test_notify_owner_of_escalation_sends_urgent_text(test_engine):
    from notifications import notify_owner_of_escalation

    class Recorder:
        def __init__(self): self.sent = []
        def send(self, from_number, to_number, body): self.sent.append((from_number, to_number, body))

    rec = Recorder()
    client = _seed_business(test_engine)
    ok = notify_owner_of_escalation(client, "+15125559999", "gas smell", channel=rec)

    assert ok is True
    assert rec.sent[0][1] == "512-555-0148"
    body = rec.sent[0][2]
    assert "URGENT" in body and "+15125559999" in body and "gas smell" in body


def test_notify_owner_of_escalation_never_raises(test_engine):
    from notifications import notify_owner_of_escalation

    class Exploder:
        def send(self, *a, **k): raise RuntimeError("twilio down")

    client = _seed_business(test_engine)
    assert notify_owner_of_escalation(client, "+15125559999", "flood", channel=Exploder()) is False


# ---- Crash safety: a dying call must never vanish silently ------------------

class ExplodingWS(FakeWS):
    """Yields one greeting, then the connection dies mid-call."""
    async def __aiter__(self):
        yield json.dumps(GREETING_DONE)
        raise RuntimeError("ws dropped")


def test_run_call_survives_ws_drop_and_alerts_owner(test_engine, monkeypatch):
    from xai_voice_adapter import run_call
    from call_trace import CallTrace
    import xai_voice_adapter as adapter

    alerts = []
    monkeypatch.setattr(adapter, "notify_owner_of_escalation",
                        lambda business, caller, reason: alerts.append((caller, reason)) or True)
    client = _seed_business(test_engine)
    trace = CallTrace("call_drop")

    # Must not raise out of the coroutine — a crashed call is handled, not lost.
    asyncio.run(run_call("call_drop", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ExplodingWS([])), trace=trace))

    stages = [r["stage"] for r in trace.records if r["kind"] == "stage"]
    assert "call_failed" in stages
    assert "call_completed" not in stages
    assert len(alerts) == 1
    assert alerts[0][0] == "+15125559999"
    assert "dropped" in alerts[0][1].lower() or "call them back" in alerts[0][1].lower()


def test_supervised_call_task_logs_crash_and_is_released(capsys):
    import app as app_module

    async def scenario():
        async def boom():
            raise RuntimeError("task exploded")
        task = asyncio.get_event_loop().create_task(boom())
        app_module.supervise_call_task(task, "call_sup")
        await asyncio.sleep(0)   # let the task run and the callback fire
        await asyncio.sleep(0)
        return task

    asyncio.run(scenario())

    assert len(app_module._active_call_tasks) == 0, "finished task must be released"
    err = capsys.readouterr().err
    assert "call_sup" in err and "exploded" in err


# ---- Maximum call duration: no session can run forever ----------------------
#
# The guarantee: run_call bounds the ENTIRE call session (from connect through
# close) in one asyncio.wait_for, wrapping _run_call_session's single
# top-level await from the outside — _run_call_session's own internals are
# untouched, so the event-dispatch loop, tool handling, and transcript
# capture built in the earlier priorities are not restructured at all.
#
# Chosen there (in run_call, not inside the event loop) because it needs to
# bound EVERYTHING a call can block on — the initial connect handshake as
# well as an idle `async for raw in ws`, not just one wait point inside the
# loop. A per-event timeout would miss a call stuck before its first event.
#
# On timeout, asyncio.wait_for cancels the inner coroutine; the resulting
# CancelledError propagates up through _run_call_session's
# `async with connect(call_id) as ws:` block, which is what actually closes
# the websocket — the SAME mechanism that already closes it on a clean
# return or a crash, not a new one. run_call's own try/except/finally then
# behaves exactly like the existing crash path: an owner notification (a
# DIFFERENT message from a crash's, honest about why — "reached the maximum
# allowed duration", never claimed to be a generic drop) and trace.close()
# in `finally`, unconditionally.

class HangingWS(FakeWS):
    """Yields the given events, then blocks forever — simulates an idle or
    stuck call so the max-duration timeout actually has something to cut
    off. `closed` records whether __aexit__ ran, proving cleanup happened."""
    def __init__(self, events=None):
        super().__init__(events or [])
        self.closed = False

    async def __aiter__(self):
        for r in self._raw:
            yield r
        await asyncio.sleep(999999)


def tracking_connector_for(ws):
    """Same shape as connector_for, but records whether __aexit__ actually
    ran — connector_for's own _CM discards that, and this test needs to
    prove the websocket is closed on timeout, not just assume it."""
    class _CM:
        async def __aenter__(self):
            return ws

        async def __aexit__(self, *a):
            ws.closed = True
            return False

    def connect(call_id):
        return _CM()

    return connect


TINY_TIMEOUT = 0.05


def test_a_normal_call_under_the_limit_is_unaffected(test_engine):
    """The new wrapping must not change ordinary behavior at all."""
    from xai_voice_adapter import run_call
    from call_trace import CallTrace

    client = _seed_business(test_engine)
    trace = CallTrace("call_ok")
    ws = FakeWS([GREETING_DONE, LOG_JOB_EVENT])

    asyncio.run(run_call("call_ok", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ws), trace=trace, max_duration_seconds=10))

    stages = [r["stage"] for r in trace.records if r["kind"] == "stage"]
    assert "call_completed" in stages
    assert "call_timed_out" not in stages


def test_timeout_while_idle_is_recorded_and_ends_the_call(test_engine, monkeypatch):
    from xai_voice_adapter import run_call
    from call_trace import CallTrace
    import xai_voice_adapter as adapter

    monkeypatch.setattr(adapter, "notify_owner_of_escalation", lambda *a, **k: True)
    client = _seed_business(test_engine)
    trace = CallTrace("call_idle")
    ws = HangingWS([])  # nothing ever arrives — an idle/stuck connection

    asyncio.run(run_call("call_idle", client, "+15125559999", lambda: Session(test_engine),
                         connect=tracking_connector_for(ws), trace=trace,
                         max_duration_seconds=TINY_TIMEOUT))

    stages = [r["stage"] for r in trace.records if r["kind"] == "stage"]
    assert "call_timed_out" in stages
    assert "call_completed" not in stages
    assert ws.closed is True, "the websocket must be closed on timeout, not left dangling"


def test_timeout_during_conversation_ends_the_call_cleanly(test_engine, monkeypatch):
    """A call that had real back-and-forth, then goes silent past the limit."""
    from xai_voice_adapter import run_call
    from call_trace import CallTrace
    import xai_voice_adapter as adapter

    monkeypatch.setattr(adapter, "notify_owner_of_escalation", lambda *a, **k: True)
    client = _seed_business(test_engine)
    trace = CallTrace("call_mid")
    ws = HangingWS([GREETING_DONE, _transcription_completed("item_1", "hello?")])

    asyncio.run(run_call("call_mid", client, "+15125559999", lambda: Session(test_engine),
                         connect=tracking_connector_for(ws), trace=trace,
                         max_duration_seconds=TINY_TIMEOUT))

    stages = [r["stage"] for r in trace.records if r["kind"] == "stage"]
    assert "first_ai_response" in stages    # the earlier turns were handled normally
    assert "call_timed_out" in stages
    assert ws.closed is True


def test_timeout_after_a_booking_preserves_the_booked_job(test_engine, monkeypatch):
    """Partial work already committed before the timeout must survive it —
    book_job already commits synchronously as each event is processed, so
    this proves the timeout path doesn't touch or roll back that work."""
    from xai_voice_adapter import run_call
    from call_trace import CallTrace
    from db_models import Message

    import xai_voice_adapter as adapter
    monkeypatch.setattr(adapter, "notify_owner_of_booking", lambda *a, **k: True)
    monkeypatch.setattr(adapter, "notify_owner_of_escalation", lambda *a, **k: True)
    client = _seed_business(test_engine)
    trace = CallTrace("call_after_book")
    ws = HangingWS([LOG_JOB_EVENT])

    asyncio.run(run_call("call_after_book", client, "+15125559999", lambda: Session(test_engine),
                         connect=tracking_connector_for(ws), trace=trace,
                         max_duration_seconds=TINY_TIMEOUT))

    with Session(test_engine) as s:
        jobs = s.exec(select(Job).where(Job.business_id == client.id)).all()
    assert len(jobs) == 1
    assert jobs[0].service_type == "burst pipe"
    stages = [r["stage"] for r in trace.records if r["kind"] == "stage"]
    assert "call_timed_out" in stages


def test_timeout_after_an_escalation_still_notifies_once_more_honestly(test_engine, monkeypatch):
    """Owner notifications must behave correctly across both events: the
    escalation pages the owner once (Priority 1's guarantee, untouched), and
    the timeout — a SEPARATE incident, not a re-alert of the same one — adds
    its own distinct, honest notification on top. Neither suppresses or
    duplicates the other."""
    from xai_voice_adapter import run_call
    from call_trace import CallTrace
    from db_models import OwnerNotification
    import xai_voice_adapter as adapter

    monkeypatch.setattr(adapter, "notify_owner_of_escalation", lambda *a, **k: True)
    client = _seed_business(test_engine)
    trace = CallTrace("call_after_esc")
    ws = HangingWS([ALERT_OWNER_EVENT])

    asyncio.run(run_call("call_after_esc", client, "+15125559999", lambda: Session(test_engine),
                         connect=tracking_connector_for(ws), trace=trace,
                         max_duration_seconds=TINY_TIMEOUT))

    with Session(test_engine) as s:
        jobs = s.exec(select(Job).where(Job.business_id == client.id)).all()
        notifications = s.exec(
            select(OwnerNotification).where(OwnerNotification.business_id == client.id)
        ).all()
    assert len(jobs) == 1
    assert jobs[0].owner_alerted_at is not None      # the escalation's own page succeeded
    assert len(notifications) == 2                    # escalation + timeout, not deduped together
    kinds = sorted(n.kind for n in notifications)
    assert kinds == sorted(["escalation", "call_dropped"])
    stages = [r["stage"] for r in trace.records if r["kind"] == "stage"]
    assert "call_timed_out" in stages


def test_cleanup_always_happens_even_on_timeout(test_engine, monkeypatch, tmp_path):
    """trace.close() runs in `finally` regardless of how the try block
    exits — proven here by asserting the capture file was actually closed
    (a second write after run_call returns must still succeed, which would
    fail if the handle were left in some broken half-open state)."""
    from xai_voice_adapter import run_call
    from call_trace import CallTrace
    import xai_voice_adapter as adapter

    monkeypatch.setattr(adapter, "notify_owner_of_escalation", lambda *a, **k: True)
    client = _seed_business(test_engine)
    trace = CallTrace("call_cleanup", capture_dir=tmp_path)
    ws = HangingWS([])

    asyncio.run(run_call("call_cleanup", client, "+15125559999", lambda: Session(test_engine),
                         connect=tracking_connector_for(ws), trace=trace,
                         max_duration_seconds=TINY_TIMEOUT))

    assert trace._fh is None, "the capture file handle must be closed, not left open"
    capture_file = tmp_path / "call_cleanup.jsonl"
    assert capture_file.exists()


def test_no_task_leaks_after_a_timeout(test_engine, monkeypatch):
    """The asyncio.Task wrapping a timed-out call must be released, exactly
    like a crashed or cleanly-completed one — proven through app.py's real
    supervise_call_task, not a re-implementation of it."""
    import app as app_module
    from xai_voice_adapter import run_call
    from call_trace import CallTrace
    import xai_voice_adapter as adapter

    monkeypatch.setattr(adapter, "notify_owner_of_escalation", lambda *a, **k: True)
    client = _seed_business(test_engine)
    ws = HangingWS([])

    async def scenario():
        task = asyncio.get_event_loop().create_task(
            run_call("call_leak", client, "+15125559999", lambda: Session(test_engine),
                    connect=tracking_connector_for(ws), trace=CallTrace("call_leak"),
                    max_duration_seconds=TINY_TIMEOUT)
        )
        app_module.supervise_call_task(task, "call_leak")
        await task
        await asyncio.sleep(0)   # let the done-callback fire

    asyncio.run(scenario())

    assert len(app_module._active_call_tasks) == 0, "a timed-out call's task must be released"


# ---- Per-tool recovery: one bad tool call must not end a healthy call ------
#
# 2026-07-29 audit, "narrow per-tool error recovery": before this, ANY
# exception raised while handling a tool call (a booking DB hiccup, a bad
# arguments payload, an unexpected bug) propagated all the way up through
# _run_call_session and was caught only by run_call's top-level handler —
# ending the ENTIRE call and texting the owner "call dropped", even though
# the caller might have simply needed to repeat themselves.
#
# The line drawn: recoverable is anything inside tool EXECUTION (booking,
# escalation, argument parsing) — the model is told {"status": "error", ...}
# over the same function_call_output round-trip a success uses, and the loop
# continues. Unrecoverable is a transport failure — if ws.send itself raises,
# there is no way to tell the model or the caller anything, so that still
# ends the call via run_call's existing (unchanged) crash handler.

def test_a_booking_failure_is_recovered_not_fatal(test_engine, monkeypatch):
    import xai_voice_adapter as adapter
    from xai_voice_adapter import run_call
    from call_trace import CallTrace

    monkeypatch.setattr(adapter, "book_job",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    client = _seed_business(test_engine)
    trace = CallTrace("call_bookfail")
    ws = FakeWS([LOG_JOB_EVENT, GREETING_DONE])

    asyncio.run(run_call("call_bookfail", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ws), trace=trace))

    stages = [r["stage"] for r in trace.records if r["kind"] == "stage"]
    assert "tool_call_failed" in stages
    assert "call_completed" in stages
    assert "call_failed" not in stages
    with Session(test_engine) as s:
        jobs = s.exec(select(Job).where(Job.business_id == client.id)).all()
    assert jobs == []
    outputs = [json.loads(m["item"]["output"]) for m in ws.sent if m.get("type") == "conversation.item.create"]
    assert outputs[0]["status"] == "error"


def test_a_notification_failure_does_not_fail_the_booking(test_engine, monkeypatch):
    """Notification failure is handled differently from a booking failure —
    notify_owner_of_booking already never raises (returns False), and that
    must keep reporting "logged" to the model, not "error"."""
    import xai_voice_adapter as adapter
    from xai_voice_adapter import run_call
    from call_trace import CallTrace

    monkeypatch.setattr(adapter, "notify_owner_of_booking", lambda *a, **k: False)
    client = _seed_business(test_engine)
    trace = CallTrace("call_notif_fail")
    ws = FakeWS([LOG_JOB_EVENT])

    asyncio.run(run_call("call_notif_fail", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ws), trace=trace))

    with Session(test_engine) as s:
        jobs = s.exec(select(Job).where(Job.business_id == client.id)).all()
    assert len(jobs) == 1        # the booking succeeded regardless of the failed owner text
    outputs = [json.loads(m["item"]["output"]) for m in ws.sent if m.get("type") == "conversation.item.create"]
    assert outputs[0]["status"] == "logged"
    stages = [r["stage"] for r in trace.records if r["kind"] == "stage"]
    assert "tool_call_failed" not in stages


def test_malformed_tool_arguments_are_recovered_not_fatal(test_engine):
    from xai_voice_adapter import run_call
    from call_trace import CallTrace

    client = _seed_business(test_engine)
    trace = CallTrace("call_malformed")
    malformed = {"type": "response.function_call_arguments.done", "name": "log_job",
                "call_id": "fc_bad", "arguments": "{not valid json"}
    ws = FakeWS([malformed, GREETING_DONE])

    asyncio.run(run_call("call_malformed", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ws), trace=trace))

    failures = [r for r in trace.records if r.get("stage") == "tool_call_failed"]
    assert failures and failures[0]["reason"] == "malformed_arguments"
    stages = [r["stage"] for r in trace.records if r["kind"] == "stage"]
    assert "call_completed" in stages
    assert "call_failed" not in stages
    with Session(test_engine) as s:
        jobs = s.exec(select(Job).where(Job.business_id == client.id)).all()
    assert jobs == []
    outputs = [json.loads(m["item"]["output"]) for m in ws.sent if m.get("type") == "conversation.item.create"]
    assert outputs[0]["status"] == "error"


def test_an_unexpected_exception_during_escalation_is_recovered(test_engine, monkeypatch):
    import xai_voice_adapter as adapter
    from xai_voice_adapter import run_call
    from call_trace import CallTrace

    monkeypatch.setattr(adapter, "record_escalation",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    client = _seed_business(test_engine)
    trace = CallTrace("call_exc")
    ws = FakeWS([ALERT_OWNER_EVENT, GREETING_DONE])

    asyncio.run(run_call("call_exc", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ws), trace=trace))

    failures = [r for r in trace.records if r.get("stage") == "tool_call_failed"]
    assert failures and failures[0]["reason"] == "execution_error"
    stages = [r["stage"] for r in trace.records if r["kind"] == "stage"]
    assert "call_completed" in stages
    assert "call_failed" not in stages
    with Session(test_engine) as s:
        jobs = s.exec(select(Job).where(Job.business_id == client.id)).all()
    assert jobs == []
    outputs = [json.loads(m["item"]["output"]) for m in ws.sent if m.get("type") == "conversation.item.create"]
    assert outputs[0]["status"] == "error"


def test_a_failed_booking_can_be_retried_successfully(test_engine, monkeypatch):
    """The model retries the same tool after being told "error" — proves the
    call is genuinely still alive and functional, not just not-crashed."""
    import xai_voice_adapter as adapter
    from bookings import book_job as real_book_job
    from xai_voice_adapter import run_call
    from call_trace import CallTrace

    calls = {"n": 0}

    def flaky_book_job(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db hiccup")
        return real_book_job(*a, **kw)

    monkeypatch.setattr(adapter, "book_job", flaky_book_job)
    monkeypatch.setattr(adapter, "notify_owner_of_booking", lambda *a, **k: True)
    client = _seed_business(test_engine)
    trace = CallTrace("call_retry")
    first = dict(LOG_JOB_EVENT, call_id="fc_a")
    second = dict(LOG_JOB_EVENT, call_id="fc_b")
    ws = FakeWS([first, second])

    asyncio.run(run_call("call_retry", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ws), trace=trace))

    with Session(test_engine) as s:
        jobs = s.exec(select(Job).where(Job.business_id == client.id)).all()
    assert len(jobs) == 1        # the failed attempt left nothing; the retry booked cleanly
    outputs = [json.loads(m["item"]["output"]) for m in ws.sent if m.get("type") == "conversation.item.create"]
    assert [o["status"] for o in outputs] == ["error", "logged"]
    stages = [r["stage"] for r in trace.records if r["kind"] == "stage"]
    assert "call_completed" in stages


class SendFailingAfterHandshakeWS(FakeWS):
    """The initial session handshake succeeds, but every send after it fails
    — proves specifically that a TRANSPORT failure while responding to a
    tool call (not the earlier connect handshake) is genuinely unrecoverable,
    unlike a booking/escalation/argument failure."""
    def __init__(self, events):
        super().__init__(events)
        self._send_count = 0

    async def send(self, raw):
        self._send_count += 1
        if self._send_count > 2:   # 1=session.update, 2=response.create (handshake)
            raise RuntimeError("connection reset")
        self.sent.append(json.loads(raw))


def test_a_transport_send_failure_is_unrecoverable_and_ends_the_call(test_engine, monkeypatch):
    import xai_voice_adapter as adapter
    from xai_voice_adapter import run_call
    from call_trace import CallTrace

    monkeypatch.setattr(adapter, "notify_owner_of_escalation", lambda *a, **k: True)
    client = _seed_business(test_engine)
    trace = CallTrace("call_send_fail")
    ws = SendFailingAfterHandshakeWS([LOG_JOB_EVENT])

    asyncio.run(run_call("call_send_fail", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ws), trace=trace))

    stages = [r["stage"] for r in trace.records if r["kind"] == "stage"]
    assert "call_failed" in stages
    assert "call_completed" not in stages


# ---- Voice cost cap: no call can exceed a token budget ----------------------
#
# Closes the sixth priority from the 2026-07-29 voice-loop audit: SMS turns
# are metered by trial_cap.py; a live voice call had no cost cutoff of any
# kind. Cost is estimated in TOKENS, read from each response.done event's
# `response.usage` field — the same OpenAI-Realtime-compatible shape already
# assumed for _extract_transcript and the caller-transcript event (and
# equally unconfirmed against a real payload until one is captured). Tokens,
# not a fabricated cents-per-token rate: trial_cap.py's own flat per-turn
# cents estimate is deliberately NOT real per-token billing either — a made
# up conversion rate would be no more honest than the token count itself.
#
# Enforced INSIDE the event loop (unlike Priority 4's duration cap, which
# wraps run_call's outer await): usage numbers only exist attached to a
# response.done event, so the check has to happen where that event is
# handled, not from outside. Exceeding budget raises a small internal
# signal (_CallBudgetExceeded) from inside `async with connect(...) as ws:`,
# which closes the websocket via ordinary exception unwinding — the same
# mechanism a crash already goes through, not a new one. run_call catches it
# in its own except branch, parallel to (and independent of) the timeout
# branch, and notifies the owner the same honest way.

def _response_done_with_usage(total_tokens: int, transcript: str = "") -> dict:
    """The REAL response.done shape, captured from a live xAI session
    2026-08-04: usage at the top level, `response.usage` an empty dict.

    This helper previously emitted usage nested under `response`, matching the
    same assumption the parser made — so every budget test below passed while
    production counted zero tokens on every turn and the cap could never fire.
    A fixture that encodes the code's own guess cannot falsify it; see
    test_xai_live_payloads.py."""
    output = [{"content": [{"transcript": transcript}]}] if transcript else []
    return {
        "type": "response.done",
        "response": {"output": output, "usage": {}},
        "usage": {"total_tokens": total_tokens},
    }


TINY_TOKEN_BUDGET = 100


def test_a_normal_inexpensive_call_is_unaffected(test_engine):
    client = _seed_business(test_engine)
    from xai_voice_adapter import run_call
    from call_trace import CallTrace
    trace = CallTrace("call_cheap")
    ws = FakeWS([_response_done_with_usage(10, "hi"), LOG_JOB_EVENT])

    asyncio.run(run_call("call_cheap", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ws), trace=trace,
                         max_call_tokens=TINY_TOKEN_BUDGET))

    stages = [r["stage"] for r in trace.records if r["kind"] == "stage"]
    assert "call_completed" in stages
    assert "call_budget_exceeded" not in stages


def test_budget_exceeded_mid_conversation_ends_the_call(test_engine, monkeypatch):
    import xai_voice_adapter as adapter
    from xai_voice_adapter import run_call
    from call_trace import CallTrace

    monkeypatch.setattr(adapter, "notify_owner_of_escalation", lambda *a, **k: True)
    client = _seed_business(test_engine)
    trace = CallTrace("call_budget")
    ws = FakeWS([
        _response_done_with_usage(60, "first"),
        _response_done_with_usage(60, "second"),   # 120 > TINY_TOKEN_BUDGET
        GREETING_DONE,                              # never reached
    ])

    asyncio.run(run_call("call_budget", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ws), trace=trace,
                         max_call_tokens=TINY_TOKEN_BUDGET))

    stages = [r["stage"] for r in trace.records if r["kind"] == "stage"]
    assert "call_budget_exceeded" in stages
    assert "call_completed" not in stages
    exceeded = next(r for r in trace.records if r.get("stage") == "call_budget_exceeded")
    assert exceeded["total_tokens"] == 120


def test_booking_completed_before_budget_exceeded_is_preserved(test_engine, monkeypatch):
    import xai_voice_adapter as adapter
    from xai_voice_adapter import run_call
    from call_trace import CallTrace

    monkeypatch.setattr(adapter, "notify_owner_of_booking", lambda *a, **k: True)
    monkeypatch.setattr(adapter, "notify_owner_of_escalation", lambda *a, **k: True)
    client = _seed_business(test_engine)
    trace = CallTrace("call_budget_book")
    ws = FakeWS([
        LOG_JOB_EVENT,
        _response_done_with_usage(60),
        _response_done_with_usage(60),   # tips over budget after the booking
    ])

    asyncio.run(run_call("call_budget_book", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ws), trace=trace,
                         max_call_tokens=TINY_TOKEN_BUDGET))

    with Session(test_engine) as s:
        jobs = s.exec(select(Job).where(Job.business_id == client.id)).all()
    assert len(jobs) == 1
    assert jobs[0].service_type == "burst pipe"
    stages = [r["stage"] for r in trace.records if r["kind"] == "stage"]
    assert "call_budget_exceeded" in stages


def test_escalation_completed_before_budget_exceeded_is_preserved(test_engine, monkeypatch):
    """Mirrors Priority 4's timeout-after-escalation guarantee: the
    escalation's own page and the budget cutoff's own notification are two
    distinct incidents, neither suppresses nor duplicates the other."""
    import xai_voice_adapter as adapter
    from db_models import OwnerNotification
    from xai_voice_adapter import run_call
    from call_trace import CallTrace

    monkeypatch.setattr(adapter, "notify_owner_of_escalation", lambda *a, **k: True)
    client = _seed_business(test_engine)
    trace = CallTrace("call_budget_esc")
    ws = FakeWS([
        ALERT_OWNER_EVENT,
        _response_done_with_usage(60),
        _response_done_with_usage(60),
    ])

    asyncio.run(run_call("call_budget_esc", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ws), trace=trace,
                         max_call_tokens=TINY_TOKEN_BUDGET))

    with Session(test_engine) as s:
        jobs = s.exec(select(Job).where(Job.business_id == client.id)).all()
        notifications = s.exec(
            select(OwnerNotification).where(OwnerNotification.business_id == client.id)
        ).all()
    assert len(jobs) == 1
    assert jobs[0].owner_alerted_at is not None
    assert len(notifications) == 2
    assert sorted(n.kind for n in notifications) == sorted(["escalation", "call_dropped"])


def test_cleanup_after_budget_termination(test_engine, monkeypatch, tmp_path):
    import xai_voice_adapter as adapter
    from xai_voice_adapter import run_call
    from call_trace import CallTrace

    monkeypatch.setattr(adapter, "notify_owner_of_escalation", lambda *a, **k: True)
    client = _seed_business(test_engine)
    trace = CallTrace("call_budget_cleanup", capture_dir=tmp_path)
    ws = FakeWS([_response_done_with_usage(60), _response_done_with_usage(60)])

    asyncio.run(run_call("call_budget_cleanup", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ws), trace=trace,
                         max_call_tokens=TINY_TOKEN_BUDGET))

    assert trace._fh is None, "the capture file handle must be closed, not left open"
    assert (tmp_path / "call_budget_cleanup.jsonl").exists()


def test_budget_and_duration_caps_do_not_interfere(test_engine, monkeypatch):
    """A cheap call that goes idle must still end via the DURATION cap, not
    be affected by (or accidentally trip) the separate token-budget check —
    Priority 4's mechanism is untouched by this change."""
    import xai_voice_adapter as adapter
    from xai_voice_adapter import run_call
    from call_trace import CallTrace

    monkeypatch.setattr(adapter, "notify_owner_of_escalation", lambda *a, **k: True)
    client = _seed_business(test_engine)
    trace = CallTrace("call_duration_still_works")
    ws = HangingWS([_response_done_with_usage(1, "hi")])   # far under any budget

    asyncio.run(run_call("call_duration_still_works", client, "+15125559999",
                         lambda: Session(test_engine),
                         connect=tracking_connector_for(ws), trace=trace,
                         max_duration_seconds=TINY_TIMEOUT,
                         max_call_tokens=1_000_000))

    stages = [r["stage"] for r in trace.records if r["kind"] == "stage"]
    assert "call_timed_out" in stages
    assert "call_budget_exceeded" not in stages


def test_malformed_usage_information_is_treated_as_zero(test_engine):
    """A missing/malformed usage field must never crash the call — the
    budget cap exists to protect the call, not to become a new way to break
    it over a reporting hiccup."""
    from xai_voice_adapter import run_call
    from call_trace import CallTrace

    client = _seed_business(test_engine)
    trace = CallTrace("call_bad_usage")
    ws = FakeWS([
        {"type": "response.done", "response": {"output": [], "usage": "not a dict"}},
        {"type": "response.done", "response": {"output": [], "usage": {"total_tokens": "lots"}}},
        {"type": "response.done", "response": {}},   # no usage key at all
        LOG_JOB_EVENT,
    ])

    asyncio.run(run_call("call_bad_usage", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ws), trace=trace,
                         max_call_tokens=TINY_TOKEN_BUDGET))

    stages = [r["stage"] for r in trace.records if r["kind"] == "stage"]
    assert "call_completed" in stages
    assert "call_budget_exceeded" not in stages
    with Session(test_engine) as s:
        jobs = s.exec(select(Job).where(Job.business_id == client.id)).all()
    assert len(jobs) == 1


# ---- Safe voice test mode ---------------------------------------------------
#
# Closes the eighth priority from the 2026-07-29 voice-loop audit: there was
# no way to exercise a live number without it producing a real-looking
# Job/OwnerNotification/dashboard row. run_call gains a single opt-in
# `is_test_call` parameter (default False — the real production entry point,
# app.py's xai-incoming-call webhook, never passes it, so this can never
# activate on its own).
#
# The mechanism is NOT a parallel test pipeline: is_test_call only changes
# which thread PREFIX _thread_id builds (VOICE_TEST_THREAD_PREFIX instead of
# VOICE_THREAD_PREFIX) and gates the owner-notification call sites — every
# other line of _run_call_session, _handle_function_call, and _persist_job
# runs identically regardless. Isolation then falls out of the SAME shared
# mechanism the SMS path's `portal-test` thread already relies on
# (notifications.is_test_thread): metrics.py's `_jobs`/`_voice_conversations`
# already filter on it, so a test call's Job/Message rows are automatically
# excluded from customer-facing metrics and the dashboard with no changes to
# those modules at all — the two thread prefixes are non-overlapping
# strings ("xai-voice-test:" does not start with "xai-voice:"), so existing
# `.startswith(VOICE_THREAD_PREFIX)` filters already exclude test threads.

TEST_CALLER_NUMBER = "+15125550001"   # a fixed, clearly-non-customer number


def test_a_normal_production_call_is_unaffected(test_engine, monkeypatch):
    """The default (is_test_call omitted) must behave exactly as before —
    real thread prefix, real owner notification."""
    from xai_voice_adapter import run_call
    from call_trace import CallTrace
    import xai_voice_adapter as adapter

    calls = []
    monkeypatch.setattr(adapter, "notify_owner_of_booking",
                        lambda *a, **k: calls.append(1) or True)
    client = _seed_business(test_engine)
    ws = FakeWS([LOG_JOB_EVENT])

    asyncio.run(run_call("call_prod", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ws), trace=CallTrace("call_prod")))

    assert len(calls) == 1
    with Session(test_engine) as s:
        job = s.exec(select(Job).where(Job.business_id == client.id)).first()
    assert job.customer_phone == "xai-voice:call_prod"


def test_test_mode_uses_the_distinct_thread_prefix(test_engine, monkeypatch):
    from xai_voice_adapter import run_call
    from call_trace import CallTrace
    import xai_voice_adapter as adapter

    monkeypatch.setattr(adapter, "notify_owner_of_booking", lambda *a, **k: True)
    client = _seed_business(test_engine)
    ws = FakeWS([LOG_JOB_EVENT])

    asyncio.run(run_call("call_test1", client, TEST_CALLER_NUMBER, lambda: Session(test_engine),
                         connect=connector_for(ws), trace=CallTrace("call_test1"),
                         is_test_call=True))

    with Session(test_engine) as s:
        job = s.exec(select(Job).where(Job.business_id == client.id)).first()
    assert job.customer_phone == "xai-voice-test:call_test1"


def test_test_mode_never_calls_notify_owner_of_booking(test_engine, monkeypatch):
    from xai_voice_adapter import run_call
    from call_trace import CallTrace
    import xai_voice_adapter as adapter

    calls = []
    monkeypatch.setattr(adapter, "notify_owner_of_booking", lambda *a, **k: calls.append(1) or True)
    client = _seed_business(test_engine)
    ws = FakeWS([LOG_JOB_EVENT])

    asyncio.run(run_call("call_test2", client, TEST_CALLER_NUMBER, lambda: Session(test_engine),
                         connect=connector_for(ws), trace=CallTrace("call_test2"),
                         is_test_call=True))

    assert calls == []


def test_test_mode_never_calls_notify_owner_of_escalation(test_engine, monkeypatch):
    from xai_voice_adapter import run_call
    from call_trace import CallTrace
    import xai_voice_adapter as adapter

    calls = []
    monkeypatch.setattr(adapter, "notify_owner_of_escalation", lambda *a, **k: calls.append(1) or True)
    client = _seed_business(test_engine)
    ws = FakeWS([ALERT_OWNER_EVENT])

    asyncio.run(run_call("call_test3", client, TEST_CALLER_NUMBER, lambda: Session(test_engine),
                         connect=connector_for(ws), trace=CallTrace("call_test3"),
                         is_test_call=True))

    assert calls == []
    with Session(test_engine) as s:
        job = s.exec(select(Job).where(Job.business_id == client.id)).first()
    assert job.owner_alerted_at is None   # never marked as paged — it never was


def test_test_mode_leaves_no_owner_notification_row(test_engine, monkeypatch):
    """No customer-facing side effects: not even a durable notification-log
    row is created for test-mode activity."""
    from xai_voice_adapter import run_call
    from call_trace import CallTrace
    import xai_voice_adapter as adapter
    from db_models import OwnerNotification

    monkeypatch.setattr(adapter, "notify_owner_of_booking", lambda *a, **k: True)
    client = _seed_business(test_engine)
    ws = FakeWS([LOG_JOB_EVENT])

    asyncio.run(run_call("call_test4", client, TEST_CALLER_NUMBER, lambda: Session(test_engine),
                         connect=connector_for(ws), trace=CallTrace("call_test4"),
                         is_test_call=True))

    with Session(test_engine) as s:
        notifications = s.exec(
            select(OwnerNotification).where(OwnerNotification.business_id == client.id)
        ).all()
    assert notifications == []


def test_test_mode_bookings_are_excluded_from_customer_facing_metrics(test_engine, monkeypatch):
    """Isolation via the shared is_test_thread mechanism, not a special case
    invented for voice — metrics.py needed no changes at all."""
    from xai_voice_adapter import run_call
    from call_trace import CallTrace
    import xai_voice_adapter as adapter
    import metrics

    monkeypatch.setattr(adapter, "notify_owner_of_booking", lambda *a, **k: True)
    client = _seed_business(test_engine)
    ws = FakeWS([LOG_JOB_EVENT])

    asyncio.run(run_call("call_test5", client, TEST_CALLER_NUMBER, lambda: Session(test_engine),
                         connect=connector_for(ws), trace=CallTrace("call_test5"),
                         is_test_call=True))

    with Session(test_engine) as s:
        assert metrics.booked_jobs(s, client.id) == 0


def test_test_mode_still_generates_a_trace(test_engine, monkeypatch):
    """Traces are never suppressed — only who gets notified changes."""
    from xai_voice_adapter import run_call
    from call_trace import CallTrace
    import xai_voice_adapter as adapter

    monkeypatch.setattr(adapter, "notify_owner_of_booking", lambda *a, **k: True)
    client = _seed_business(test_engine)
    trace = CallTrace("call_test6")
    ws = FakeWS([GREETING_DONE, LOG_JOB_EVENT])

    asyncio.run(run_call("call_test6", client, TEST_CALLER_NUMBER, lambda: Session(test_engine),
                         connect=connector_for(ws), trace=trace, is_test_call=True))

    stages = [r["stage"] for r in trace.records if r["kind"] == "stage"]
    for expected in ("ws_connected", "first_ai_response", "tool_invoked",
                     "job_persisted", "owner_notification_skipped", "call_completed"):
        assert expected in stages, f"missing stage {expected}: {stages}"


def test_test_mode_still_captures_transcripts(test_engine, monkeypatch):
    from xai_voice_adapter import run_call
    from call_trace import CallTrace
    from db_models import Message

    client = _seed_business(test_engine)
    ws = FakeWS([_transcription_completed("item_1", "testing the line"), GREETING_DONE])

    asyncio.run(run_call("call_test7", client, TEST_CALLER_NUMBER, lambda: Session(test_engine),
                         connect=connector_for(ws), trace=CallTrace("call_test7"),
                         is_test_call=True))

    with Session(test_engine) as s:
        msgs = s.exec(select(Message).where(Message.business_id == client.id)
                      .order_by(Message.id)).all()
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert all(m.customer_phone == "xai-voice-test:call_test7" for m in msgs)


def test_test_mode_actually_books_a_real_job_isolated_not_simulated(test_engine, monkeypatch):
    """The full pipeline is exercised, not mocked: book_job really runs and
    really persists a Job — it's isolated (test thread prefix), not faked."""
    from xai_voice_adapter import run_call
    from call_trace import CallTrace
    import xai_voice_adapter as adapter

    monkeypatch.setattr(adapter, "notify_owner_of_booking", lambda *a, **k: True)
    client = _seed_business(test_engine)
    ws = FakeWS([LOG_JOB_EVENT])

    asyncio.run(run_call("call_test8", client, TEST_CALLER_NUMBER, lambda: Session(test_engine),
                         connect=connector_for(ws), trace=CallTrace("call_test8"),
                         is_test_call=True))

    with Session(test_engine) as s:
        job = s.exec(select(Job).where(Job.business_id == client.id)).first()
    assert job is not None
    assert job.service_type == "burst pipe"   # real booking logic ran, not a stub


def test_test_mode_timeout_does_not_notify_a_real_owner(test_engine, monkeypatch):
    """Timeout behaviour must be exercisable in test mode too, without
    paging anyone real when it fires."""
    from xai_voice_adapter import run_call
    from call_trace import CallTrace
    import xai_voice_adapter as adapter

    calls = []
    monkeypatch.setattr(adapter, "notify_owner_of_escalation", lambda *a, **k: calls.append(1) or True)
    client = _seed_business(test_engine)
    trace = CallTrace("call_test9")
    ws = HangingWS([])

    asyncio.run(run_call("call_test9", client, TEST_CALLER_NUMBER, lambda: Session(test_engine),
                         connect=tracking_connector_for(ws), trace=trace,
                         max_duration_seconds=TINY_TIMEOUT, is_test_call=True))

    stages = [r["stage"] for r in trace.records if r["kind"] == "stage"]
    assert "call_timed_out" in stages
    assert "owner_notification_skipped" in stages
    assert calls == []


def test_test_mode_cleanup_still_happens(test_engine, monkeypatch, tmp_path):
    from xai_voice_adapter import run_call
    from call_trace import CallTrace
    import xai_voice_adapter as adapter

    monkeypatch.setattr(adapter, "notify_owner_of_booking", lambda *a, **k: True)
    client = _seed_business(test_engine)
    trace = CallTrace("call_test10", capture_dir=tmp_path)
    ws = FakeWS([LOG_JOB_EVENT])

    asyncio.run(run_call("call_test10", client, TEST_CALLER_NUMBER, lambda: Session(test_engine),
                         connect=connector_for(ws), trace=trace, is_test_call=True))

    assert trace._fh is None
    assert (tmp_path / "call_test10.jsonl").exists()


# ---- Webhook hardening: fail closed, reject replays --------------------------

def _fresh_signed(body: bytes, secret: str):
    import time as _time
    ts = str(int(_time.time()))
    sig = _sign(secret, "wh_1", ts, body)
    return {"webhook-id": "wh_1", "webhook-timestamp": ts, "webhook-signature": sig}


def test_webhook_missing_secret_fails_closed(test_engine, monkeypatch, tmp_path):
    """A voice-enabled number with no stored signing secret must NEVER accept
    an unverifiable webhook — reject, don't run unauthenticated."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    monkeypatch.setattr(app_module, "VOICE_CAPTURE_DIR", tmp_path / "captures")
    with Session(test_engine) as s:
        s.add(Business(business_name="X", inbound_number="+15125550100",
                       xai_phone_number="+15125550100", xai_signing_secret=None,
                       frontdesk_live=True))
        s.commit()
    recorded = _patch_run(monkeypatch)
    client = TestClient(app_module.app)
    body = _incoming_call_body("+15125550100")

    resp = client.post("/webhook/xai-incoming-call", content=body,
                       headers=_fresh_signed(body, WEBHOOK_SECRET))

    assert resp.status_code == 401
    assert recorded.get("scheduled") is None


def test_webhook_stale_timestamp_rejected(test_engine, monkeypatch, tmp_path):
    """A correctly-signed webhook with an old timestamp is a replay — reject."""
    import time as _time
    client = _voice_client(test_engine, monkeypatch, tmp_path)
    recorded = _patch_run(monkeypatch)
    body = _incoming_call_body("+15125550100")
    stale_ts = str(int(_time.time()) - 3600)
    headers = {"webhook-id": "wh_1", "webhook-timestamp": stale_ts,
               "webhook-signature": _sign(WEBHOOK_SECRET, "wh_1", stale_ts, body)}

    resp = client.post("/webhook/xai-incoming-call", content=body, headers=headers)

    assert resp.status_code == 401
    assert recorded.get("scheduled") is None


# ---- a normal hangup is not a failure --------------------------------------
# Three real calls on 2026-08-04 all ended `call_failed`, because
# participant.disconnected fell through to the unexpected-event branch and the
# websocket then closed abnormally. Every successful call ended by telling the
# owner it had dropped. These pin the end-of-call semantics against that.

DISCONNECT_EVENT = {"type": "participant.disconnected"}
AUDIO_DELTA = {"type": "response.output_audio.delta", "delta": "AAAA"}


class HangUpWS(FakeWS):
    """What a REAL hangup looks like on the wire: xAI sends
    participant.disconnected and then the socket dies. A plain FakeWS that
    simply stops iterating does NOT reproduce this — under it the loop exits
    cleanly and every assertion below passes with or without the fix, which is
    how the first version of these tests gave a false pass. Verified by
    reverting the fix and watching them fail."""

    async def __aiter__(self):
        for raw in self._raw:
            yield raw
        raise RuntimeError("socket closed after participant disconnected")


def _stages(trace):
    return [r.get("stage") for r in trace.records if r["kind"] == "stage"]


def test_a_caller_hanging_up_completes_the_call(test_engine):
    from xai_voice_adapter import run_call
    from call_trace import CallTrace

    client = _seed_business(test_engine)
    trace = CallTrace("hangup_1")
    asyncio.run(run_call("hangup_1", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(HangUpWS([GREETING_DONE, DISCONNECT_EVENT])),
                         trace=trace))

    stages = _stages(trace)
    assert "caller_hung_up" in stages
    assert "call_completed" in stages
    assert "call_failed" not in stages, "a normal hangup was recorded as a failure"


def test_a_caller_hanging_up_does_not_page_the_owner(test_engine, monkeypatch):
    """The bug in customer terms: after every successful call the owner got
    'the AI call with this customer dropped mid-call — call them back'."""
    import xai_voice_adapter
    from xai_voice_adapter import run_call

    paged = []
    monkeypatch.setattr(xai_voice_adapter, "notify_owner_of_escalation",
                        lambda *a, **k: (paged.append(a), True)[1])

    client = _seed_business(test_engine)
    asyncio.run(run_call("hangup_2", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(HangUpWS([GREETING_DONE, DISCONNECT_EVENT]))))

    assert paged == [], "owner was paged about a call the customer simply ended"


def test_a_hangup_after_a_booking_keeps_the_job_and_stays_clean(test_engine, monkeypatch):
    import xai_voice_adapter
    from xai_voice_adapter import run_call
    from call_trace import CallTrace

    monkeypatch.setattr(xai_voice_adapter, "notify_owner_of_booking", lambda *a, **k: True)
    client = _seed_business(test_engine)
    trace = CallTrace("hangup_3")
    asyncio.run(run_call("hangup_3", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(HangUpWS([LOG_JOB_EVENT, DISCONNECT_EVENT])),
                         trace=trace))

    with Session(test_engine) as s:
        assert s.exec(select(Job).where(Job.business_id == client.id)).first() is not None
    stages = _stages(trace)
    assert "call_completed" in stages
    assert "call_failed" not in stages


def test_a_real_drop_is_still_reported_as_a_failure(test_engine, monkeypatch):
    """The fix must not swallow genuine failures: a socket that dies WITHOUT a
    disconnect event is still a dropped call the owner needs to hear about."""
    import xai_voice_adapter
    from xai_voice_adapter import run_call
    from call_trace import CallTrace

    paged = []
    monkeypatch.setattr(xai_voice_adapter, "notify_owner_of_escalation",
                        lambda *a, **k: (paged.append(a), True)[1])

    client = _seed_business(test_engine)
    trace = CallTrace("real_drop")
    asyncio.run(run_call("real_drop", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ExplodingWS([])), trace=trace))

    assert "call_failed" in _stages(trace)
    assert paged, "a genuine mid-call drop no longer alerts the owner"


# ---- instrumentation honesty ----------------------------------------------

def test_time_to_first_audio_is_traced_separately_from_response_complete(test_engine):
    """`first_ai_response` fires on response.done — the greeting FINISHING.
    Reading it as time-to-first-audio turned a real 2.3s answer into an
    apparent 6.9s of dead air and sent a debugging session chasing a bug that
    did not exist. What the caller actually experiences now has its own stage."""
    from xai_voice_adapter import run_call
    from call_trace import CallTrace

    client = _seed_business(test_engine)
    trace = CallTrace("timing")
    asyncio.run(run_call("timing", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(FakeWS([AUDIO_DELTA, GREETING_DONE, DISCONNECT_EVENT])),
                         trace=trace))

    stages = _stages(trace)
    assert stages.index("first_audio_to_caller") < stages.index("first_ai_response")


def test_streaming_audio_is_never_flagged_as_an_unexpected_event(test_engine):
    from xai_voice_adapter import run_call
    from call_trace import CallTrace

    client = _seed_business(test_engine)
    trace = CallTrace("audio_noise")
    asyncio.run(run_call("audio_noise", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(FakeWS([AUDIO_DELTA, DISCONNECT_EVENT])),
                         trace=trace))

    assert "unexpected_event" not in _stages(trace)


def test_raw_audio_never_reaches_the_capture_or_the_logs(tmp_path):
    """259 audio events in one 34s call wrote the caller's voice, base64, to
    the volume and to stderr. Keep the count, drop the payload."""
    from call_trace import CallTrace

    trace = CallTrace("audio_privacy", capture_dir=tmp_path)
    for _ in range(50):
        trace.event({"type": "input_audio_buffer.append", "audio": "U2VjcmV0Q2FsbGVyVm9pY2U="})
    trace.event({"type": "response.done", "response": {}})
    trace.close()

    body = (tmp_path / "audio_privacy.jsonl").read_text()
    assert "U2VjcmV0Q2FsbGVyVm9pY2U" not in body, "caller audio was written to disk"
    assert "input_audio_buffer.append" in body, "the fact audio flowed was lost entirely"
    assert '"audio_stream_summary"' in body or "audio_stream_summary" in body
    assert body.count("U2VjcmV0") == 0


# ---- session config: turn-taking and trade vocabulary ----------------------

def test_the_session_tunes_turn_detection_for_a_phone_line(test_engine):
    """xAI's default VAD threshold is 0.85 — tuned for studio audio. A caller in
    a truck, outside, or simply soft-spoken never registers, and the AI talks
    over them. Turn-taking is what makes a voice feel human, more than the
    voice itself."""
    from xai_voice_adapter import build_session_update

    session = build_session_update(_seed_business(test_engine))["session"]
    vad = session["turn_detection"]

    assert vad["type"] == "server_vad"
    assert vad["threshold"] < 0.85, "left at the studio-audio default"
    assert 200 <= vad["silence_duration_ms"] <= 900, (
        "too short cuts callers off mid-sentence; too long is the classic "
        "robot pause")


def test_the_session_sends_trade_vocabulary_to_the_transcriber(test_engine):
    """Mishearing 'P-trap' as 'pea trap' is the loudest tell that a machine is
    on the line, and it makes the caller repeat themselves."""
    from xai_voice_adapter import build_session_update, TRADE_KEYTERMS

    session = build_session_update(_seed_business(test_engine))["session"]
    keyterms = session["audio"]["input"]["transcription"]["keyterms"]

    assert "P-trap" in keyterms
    assert "condenser" in keyterms
    assert len(keyterms) <= 100, "xAI caps keyterms at 100"
    assert all(len(t) <= 50 for t in keyterms), "xAI caps each term at 50 chars"
    assert keyterms == TRADE_KEYTERMS


def test_the_voice_is_configurable_without_a_deploy(test_engine, monkeypatch):
    """26 voices exist and picking one is a judgement made by ear, not in code."""
    import importlib
    import xai_voice_adapter

    monkeypatch.setenv("XAI_VOICE", "celeste")
    importlib.reload(xai_voice_adapter)
    try:
        session = xai_voice_adapter.build_session_update(_seed_business(test_engine))["session"]
        assert session["voice"] == "celeste"
    finally:
        monkeypatch.undo()
        importlib.reload(xai_voice_adapter)
