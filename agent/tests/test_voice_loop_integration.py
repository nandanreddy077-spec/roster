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
