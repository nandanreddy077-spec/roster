"""Booking idempotency: the money event (a booked job) must survive every
retry path without duplicating — Twilio SMS redelivery, xAI/Svix webhook
retries, and the model re-calling log_job with fresh details mid-conversation.

Also covers record_escalation: the same idempotency guarantee for the
emergency alert_owner path, closing the gap found in the 2026-07-29
Frontdesk voice-loop production audit (docs/superpowers/specs/
2026-07-29-frontdesk-voice-loop-production-audit.md) — a duplicate escalation
must never double-page the owner, but a retry after a FAILED page must still
go through.
"""
import asyncio
import json
from datetime import datetime

import pytest
from sqlmodel import Session, select

import app as app_module
import db as db_module
import portal as portal_module
import service
from conftest import StubAgent
from db_models import Business, Job, Message
from starlette.testclient import TestClient


def _seed(engine, **kw) -> Business:
    with Session(engine) as s:
        b = Business(business_name="Ridgeline Plumbing", trade="Plumbing",
                     escalation_phone="512-555-0148", inbound_number="+15125550100",
                     frontdesk_live=True, **kw)
        s.add(b); s.commit(); s.refresh(b)
        return b


# ---- book_job upsert --------------------------------------------------------

def test_book_job_same_thread_and_service_updates_instead_of_duplicating(test_engine):
    from bookings import book_job

    b = _seed(test_engine)
    with Session(test_engine) as s:
        job1, created1 = book_job(s, s.get(Business, b.id), "+15550001111", "+15550001111",
                                  {"service_type": "burst pipe", "urgency": "emergency"})
        job2, created2 = book_job(s, s.get(Business, b.id), "+15550001111", "+15550001111",
                                  {"service_type": "burst pipe", "urgency": "emergency",
                                   "address": "12 Oak St", "customer_name": "Jane"})
        jobs = s.exec(select(Job).where(Job.business_id == b.id)).all()

    assert created1 is True and created2 is False
    assert job1.id == job2.id
    assert len(jobs) == 1
    assert jobs[0].address == "12 Oak St"          # new details merged in
    assert jobs[0].customer_name == "Jane"


def test_book_job_persists_preferred_window(test_engine):
    """Sprint 1 (conversation-quality audit): a preference only, never a
    confirmed appointment — just captured and passed through like every
    other optional field."""
    from bookings import book_job

    b = _seed(test_engine)
    with Session(test_engine) as s:
        job, created = book_job(s, s.get(Business, b.id), "+15550001111", "+15550001111",
                                {"service_type": "burst pipe", "urgency": "emergency",
                                 "preferred_window": "Thursday afternoon"})

    assert created is True
    assert job.preferred_window == "Thursday afternoon"


def test_book_job_merges_preferred_window_on_a_repeat_call(test_engine):
    from bookings import book_job

    b = _seed(test_engine)
    with Session(test_engine) as s:
        job1, _ = book_job(s, s.get(Business, b.id), "+15550001111", "+15550001111",
                           {"service_type": "burst pipe", "urgency": "emergency"})
        job2, created2 = book_job(s, s.get(Business, b.id), "+15550001111", "+15550001111",
                                  {"service_type": "burst pipe", "urgency": "emergency",
                                   "preferred_window": "tomorrow morning"})

    assert created2 is False
    assert job1.id == job2.id
    assert job2.preferred_window == "tomorrow morning"


def test_book_job_without_preferred_window_leaves_it_unset(test_engine):
    """Regression: the existing booking flow (no preferred_window supplied)
    must behave exactly as before."""
    from bookings import book_job

    b = _seed(test_engine)
    with Session(test_engine) as s:
        job, created = book_job(s, s.get(Business, b.id), "+15550001111", "+15550001111",
                                {"service_type": "burst pipe", "urgency": "emergency"})

    assert created is True
    assert job.preferred_window is None


def test_book_job_different_service_creates_second_job(test_engine):
    from bookings import book_job

    b = _seed(test_engine)
    with Session(test_engine) as s:
        book_job(s, s.get(Business, b.id), "+15550001111", "+15550001111",
                 {"service_type": "burst pipe", "urgency": "emergency"})
        book_job(s, s.get(Business, b.id), "+15550001111", "+15550001111",
                 {"service_type": "water heater", "urgency": "routine"})
        jobs = s.exec(select(Job).where(Job.business_id == b.id)).all()
    assert len(jobs) == 2


def test_book_job_completed_job_does_not_absorb_new_booking(test_engine):
    from bookings import book_job
    from datetime import datetime

    b = _seed(test_engine)
    with Session(test_engine) as s:
        job1, _ = book_job(s, s.get(Business, b.id), "+15550001111", "+15550001111",
                           {"service_type": "burst pipe", "urgency": "emergency"})
        job1_id = job1.id
        job1.completed_at = datetime.utcnow()
        s.add(job1); s.commit()
        job2, created2 = book_job(s, s.get(Business, b.id), "+15550001111", "+15550001111",
                                  {"service_type": "burst pipe", "urgency": "routine"})
        job2_id = job2.id
    assert created2 is True and job2_id != job1_id


# ---- record_escalation: idempotent per call thread --------------------------

def test_record_escalation_creates_a_job_and_says_to_notify(test_engine):
    from bookings import record_escalation

    b = _seed(test_engine)
    with Session(test_engine) as s:
        job, should_notify = record_escalation(
            s, s.get(Business, b.id), "xai-voice:call_1", "+15125559999", "gas smell")

    assert should_notify is True
    assert job.service_type == "Escalated call"
    assert job.urgency == "emergency"
    assert job.notes == "gas smell"


def test_record_escalation_recall_same_thread_reuses_job_and_skips_notify(test_engine):
    """The owner was already successfully alerted — a second alert_owner call
    in the same conversation must not create a second Job or say to re-page."""
    from bookings import record_escalation

    b = _seed(test_engine)
    with Session(test_engine) as s:
        client = s.get(Business, b.id)
        job1, _ = record_escalation(s, client, "xai-voice:call_1", "+15125559999", "gas smell")
        job1.owner_alerted_at = datetime.utcnow()
        s.add(job1); s.commit()

        job2, should_notify = record_escalation(
            s, client, "xai-voice:call_1", "+15125559999", "gas smell again")
        jobs = s.exec(select(Job).where(Job.business_id == b.id)).all()

    assert should_notify is False
    assert job2.id == job1.id
    assert len(jobs) == 1


def test_record_escalation_recall_after_failed_alert_says_to_retry(test_engine):
    """The FIRST page never went through — a retry must not be silently
    swallowed, or a real emergency alert could vanish entirely."""
    from bookings import record_escalation

    b = _seed(test_engine)
    with Session(test_engine) as s:
        client = s.get(Business, b.id)
        job1, should_notify1 = record_escalation(
            s, client, "xai-voice:call_1", "+15125559999", "gas smell")
        # owner_alerted_at deliberately left unset — the send failed.
        job2, should_notify2 = record_escalation(
            s, client, "xai-voice:call_1", "+15125559999", "gas smell")
        jobs = s.exec(select(Job).where(Job.business_id == b.id)).all()

    assert should_notify1 is True
    assert should_notify2 is True
    assert job2.id == job1.id
    assert len(jobs) == 1


def test_record_escalation_different_thread_creates_separate_job(test_engine):
    from bookings import record_escalation

    b = _seed(test_engine)
    with Session(test_engine) as s:
        client = s.get(Business, b.id)
        record_escalation(s, client, "xai-voice:call_1", "+15125559999", "gas smell")
        record_escalation(s, client, "xai-voice:call_2", "+15125550001", "flooding")
        jobs = s.exec(select(Job).where(Job.business_id == b.id)).all()

    assert len(jobs) == 2


# ---- handle_customer_message: retry-safe via external_id --------------------

def test_same_external_id_does_not_double_insert_inbound_message(test_engine, monkeypatch):
    monkeypatch.setattr(service, "agent",
                        StubAgent({"reply": "hi", "jobs": [], "new_messages": [],
                                   "pending_tool_call": None}))
    b = _seed(test_engine)
    with Session(test_engine) as s:
        client = s.get(Business, b.id)
        service.handle_customer_message(s, client, "+15550001111", "help", external_id="SM123")
        service.handle_customer_message(s, client, "+15550001111", "help", external_id="SM123")
        user_msgs = s.exec(select(Message).where(
            Message.business_id == b.id, Message.role == "user")).all()
    assert len(user_msgs) == 1


# ---- /webhook/sms: dedup + cached reply replay ------------------------------

def _sms_app(test_engine, monkeypatch, reply="Got it!"):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    calls = []

    class CountingAgent:
        def respond(self, *a, **k):
            calls.append(1)
            return {"reply": reply, "jobs": [], "new_messages": [], "pending_tool_call": None}

    monkeypatch.setattr(service, "agent", CountingAgent())
    return TestClient(app_module.app), calls


def test_duplicate_message_sid_replays_cached_reply_without_second_agent_run(test_engine, monkeypatch):
    client, calls = _sms_app(test_engine, monkeypatch)
    _seed(test_engine)
    form = {"From": "+15550001111", "To": "+15125550100", "Body": "leaky faucet",
            "MessageSid": "SMdup1"}

    r1 = client.post("/webhook/sms", data=form)
    r2 = client.post("/webhook/sms", data=form)

    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.text == r2.text                      # replayed, not regenerated
    assert "Got it!" in r1.text
    assert len(calls) == 1                          # agent ran exactly once


# ---- xAI incoming-call webhook: call_id dedup -------------------------------

import base64
import hashlib
import hmac
import time

SECRET = base64.b64encode(b"idem-test-secret-0123456789abcdef").decode()


def _signed_call(to_number, call_id):
    body = json.dumps({"type": "realtime.call.incoming", "data": {
        "call_id": call_id,
        "sip_headers": [{"name": "To", "value": to_number},
                        {"name": "From", "value": "+15125559999"}]}}).encode()
    ts = str(int(time.time()))
    signed = f"wh_1.{ts}.".encode() + body
    sig = "v1," + base64.b64encode(
        hmac.new(base64.b64decode(SECRET), signed, hashlib.sha256).digest()).decode()
    return body, {"webhook-id": "wh_1", "webhook-timestamp": ts, "webhook-signature": sig}


def test_duplicate_xai_call_webhook_spawns_run_call_once(test_engine, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    monkeypatch.setattr(app_module, "VOICE_CAPTURE_DIR", tmp_path / "captures")
    _seed(test_engine, xai_phone_number="+15125550100", xai_signing_secret=SECRET)

    spawned = []

    def fake_run(call_id, client, caller, session_factory, connect=None, trace=None):
        spawned.append(call_id)

        async def _noop():
            return None
        return _noop()

    monkeypatch.setattr(app_module, "run_xai_call", fake_run)
    client = TestClient(app_module.app)
    body, headers = _signed_call("+15125550100", "call_dedup_1")

    r1 = client.post("/webhook/xai-incoming-call", content=body, headers=headers)
    r2 = client.post("/webhook/xai-incoming-call", content=body, headers=headers)

    assert r1.status_code == 200 and r2.status_code == 200
    assert spawned == ["call_dedup_1"]


# ---- voice: log_job re-call in same call updates, never duplicates ----------

def test_voice_log_job_recall_same_service_updates_single_job(test_engine, monkeypatch):
    from xai_voice_adapter import run_call
    from call_trace import CallTrace
    from tests.test_voice_loop_integration import FakeWS, connector_for

    import xai_voice_adapter as adapter
    monkeypatch.setattr(adapter, "notify_owner_of_booking", lambda *a, **k: True)
    b = _seed(test_engine, xai_phone_number="+15125550100")

    first = {"type": "response.function_call_arguments.done", "name": "log_job",
             "call_id": "fc_a",
             "arguments": json.dumps({"service_type": "burst pipe", "urgency": "emergency"})}
    second = {"type": "response.function_call_arguments.done", "name": "log_job",
              "call_id": "fc_b",
              "arguments": json.dumps({"service_type": "burst pipe", "urgency": "emergency",
                                       "address": "12 Oak St"})}
    ws = FakeWS([first, second])

    with Session(test_engine) as s:
        client = s.get(Business, b.id)
    asyncio.run(run_call("call_up1", client, "+15125559999", lambda: Session(test_engine),
                         connect=connector_for(ws), trace=CallTrace("call_up1")))

    with Session(test_engine) as s:
        jobs = s.exec(select(Job).where(Job.business_id == b.id)).all()
    assert len(jobs) == 1
    assert jobs[0].address == "12 Oak St"
