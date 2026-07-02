# Revenue Recovery Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone Roster agent that pursues unsold estimates ("quote" face) and lapsed customers ("reactivation" face) via a 6-touch outbound SMS sequence, and closes the booking itself when the customer says yes.

**Architecture:** Reuse the existing `AgentEngine.respond()` Think→Act→Observe loop (already generic over `tools`/`system_prompt`, proven by the voice-mode reuse in `voice_adapter.py`) with two new tool schemas (`record_response`, `confirm_slot`) instead of `log_job`. A daily cron script (`recovery_tick.py`) sends due sequence messages; the existing `/webhook/sms` route is extended to check for an active Recovery conversation before falling through to Frontdesk. Recovery never imports or calls Frontdesk's `service.py` — the two agents share only the DB and the SMS channel.

**Tech Stack:** Same as the rest of `agent/` — FastAPI, SQLModel/SQLite, Jinja2, pytest, no new dependencies.

## Global Constraints

- No new pip dependencies — reuse `anthropic`, `sqlmodel`, `fastapi`, `twilio` already in `requirements.txt`.
- Recovery must work with zero Frontdesk dependency: a client can have Recovery enabled and Frontdesk disabled.
- Sequence cadence is fixed at `SEQUENCE_DAYS = [1, 3, 7, 14, 21, 28]` (5-6 touches over ~4 weeks, per the approved design).
- Follow existing file granularity: one file per concern (engine/service/tick/adapter), matching `engine.py` / `service.py` / `seed.py` / `voice_adapter.py`.
- All new DB tables go in `db_models.py` (the codebase keeps every table in one file; do not fragment it).

## Scope decisions carried into this plan (flagging for visibility)

1. **Calendar integration:** only `ManualCalendarProvider` (business-hours-based slot proposal) ships now. Google Calendar / Jobber / Housecall Pro adapters are *not* built — per `ROSTER.md`'s own rule ("Guess integrations — never do this"), no real client has named a system yet. The `CalendarProvider` Protocol (mirroring `channels.py`'s `SMSChannel` Protocol) leaves a clean seam to add one later as a single new class. No "pick your calendar" dropdown is added to onboarding yet, since there's nothing real to choose between.
2. **Inbound SMS routing:** Recovery and Frontdesk share one Twilio inbound number per client. `/webhook/sms` now checks for an active `RecoveryJob` (status `pending` or `awaiting_slot`) for the sender before falling through to Frontdesk's `handle_customer_message`. This is additive — Frontdesk's existing behavior for any client/customer without an active Recovery conversation is unchanged.
3. **Scheduling mechanism:** no scheduler library is introduced. `recovery_tick.py` is a script meant to be cron'd (documented in the README), matching the project's Phase 1 "you are the operation" concierge model — same spirit as `seed.py` being a manually-run script.
4. **Template override UI deferred:** the approved spec allows the owner to override individual message templates. The data model (`RecoveryCampaign.template_overrides_json`) and the lookup in `tick()` fully support this, but the Task 8 campaign-creation form does not yet expose a UI for setting per-message overrides — campaigns launch with Roster's default templates. If a real client wants custom copy before that UI exists, set `template_overrides_json` directly (e.g. via a short script), same as `seed.py` edits data directly today. Adding the UI is a small follow-up once it's actually needed.

---

### Task 1: Recovery data model

**Files:**
- Modify: `agent/db_models.py`
- Test: `agent/tests/test_recovery_models.py`

**Interfaces:**
- Produces: `RecoveryCampaign(id, client_id, face, name, customer_list_json, template_overrides_json, started_at, is_active, created_at)` with properties `.customer_list -> list[dict]`, `.template_overrides -> dict`
- Produces: `RecoveryJob(id, campaign_id, client_id, customer_phone, customer_name, service_type, estimate_amount, days_since, current_status, last_sent_day, offered_slots_json, booked_job_id, created_at, updated_at)` with property `.offered_slots -> list[str]`
- Produces: `RecoveryMessageLog(id, recovery_job_id, message_day, message_text, sent_at, customer_reply, replied_at)`

- [ ] **Step 1: Write the failing tests**

Create `agent/tests/test_recovery_models.py`:
```python
import json

from sqlmodel import Session

from db_models import Client, RecoveryCampaign, RecoveryJob, RecoveryMessageLog


def make_client(session: Session) -> Client:
    client = Client(
        business_name="Test Co", trade="HVAC", services_json=json.dumps(["AC repair"]),
        hours="9-5", pricing_faq="n/a", escalation_phone="+15550000000",
    )
    session.add(client)
    session.commit()
    session.refresh(client)
    return client


def test_recovery_campaign_customer_list_roundtrips(session):
    client = make_client(session)
    campaign = RecoveryCampaign(
        client_id=client.id, face="quote", name="June quotes",
        customer_list_json=json.dumps([{"phone": "+15551112222", "service_type": "AC install"}]),
    )
    session.add(campaign)
    session.commit()
    session.refresh(campaign)

    assert campaign.customer_list == [{"phone": "+15551112222", "service_type": "AC install"}]
    assert campaign.template_overrides == {}


def test_recovery_job_defaults_to_pending_status(session):
    client = make_client(session)
    campaign = RecoveryCampaign(
        client_id=client.id, face="reactivation", name="Dormant list", customer_list_json="[]",
    )
    session.add(campaign)
    session.commit()
    session.refresh(campaign)

    job = RecoveryJob(
        campaign_id=campaign.id, client_id=client.id, customer_phone="+15551112222",
        service_type="Tune-up",
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    assert job.current_status == "pending"
    assert job.offered_slots == []


def test_recovery_message_log_links_to_job(session):
    client = make_client(session)
    campaign = RecoveryCampaign(client_id=client.id, face="quote", name="X", customer_list_json="[]")
    session.add(campaign)
    session.commit()
    session.refresh(campaign)
    job = RecoveryJob(campaign_id=campaign.id, client_id=client.id, customer_phone="+1", service_type="AC")
    session.add(job)
    session.commit()
    session.refresh(job)

    log = RecoveryMessageLog(recovery_job_id=job.id, message_day=1, message_text="Hi there!")
    session.add(log)
    session.commit()
    session.refresh(log)

    assert log.recovery_job_id == job.id
    assert log.customer_reply is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_models.py -v`
Expected: FAIL with `ImportError: cannot import name 'RecoveryCampaign' from 'db_models'`

- [ ] **Step 3: Add the tables to `db_models.py`**

Append to `agent/db_models.py` (after the existing `Job` class):
```python
class RecoveryCampaign(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="client.id")
    face: str  # "quote" or "reactivation"
    name: str
    customer_list_json: str  # JSON list of {phone, name, service_type, estimate_amount, days_since}
    template_overrides_json: str = "{}"  # JSON map of message_day (str) -> custom text
    started_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def customer_list(self) -> List[dict]:
        return json.loads(self.customer_list_json)

    @property
    def template_overrides(self) -> dict:
        return json.loads(self.template_overrides_json)


class RecoveryJob(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="recoverycampaign.id")
    client_id: int = Field(foreign_key="client.id")
    customer_phone: str
    customer_name: Optional[str] = None
    service_type: str
    estimate_amount: Optional[str] = None
    days_since: Optional[str] = None
    current_status: str = "pending"  # pending, awaiting_slot, booked, declined, no_response
    last_sent_day: Optional[int] = None
    offered_slots_json: str = "[]"
    booked_job_id: Optional[int] = Field(default=None, foreign_key="job.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def offered_slots(self) -> List[str]:
        return json.loads(self.offered_slots_json)


class RecoveryMessageLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    recovery_job_id: int = Field(foreign_key="recoveryjob.id")
    message_day: int
    message_text: str
    sent_at: datetime = Field(default_factory=datetime.utcnow)
    customer_reply: Optional[str] = None
    replied_at: Optional[datetime] = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_models.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add agent/db_models.py agent/tests/test_recovery_models.py
git commit -m "Add Recovery data model (RecoveryCampaign, RecoveryJob, RecoveryMessageLog)"
```

---

### Task 2: Calendar provider abstraction

**Files:**
- Create: `agent/calendar_provider.py`
- Test: `agent/tests/test_calendar_provider.py`

**Interfaces:**
- Produces: `CalendarProvider` Protocol with `.get_available_slots(business_hours: str, days_ahead: int = 7, count: int = 3) -> list[str]`
- Produces: `ManualCalendarProvider` (implements the above)
- Produces: `get_calendar_provider(client) -> CalendarProvider`

- [ ] **Step 1: Write the failing tests**

Create `agent/tests/test_calendar_provider.py`:
```python
from calendar_provider import ManualCalendarProvider, get_calendar_provider


def test_manual_provider_returns_requested_count():
    provider = ManualCalendarProvider()
    slots = provider.get_available_slots("Mon-Sat 7am-7pm", count=3)
    assert len(slots) == 3


def test_manual_provider_skips_sundays():
    provider = ManualCalendarProvider()
    slots = provider.get_available_slots("Mon-Sat 7am-7pm", count=5)
    for s in slots:
        weekday_name = s.split()[0]
        assert weekday_name != "Sunday"


def test_get_calendar_provider_returns_manual_provider():
    provider = get_calendar_provider(client=None)
    assert isinstance(provider, ManualCalendarProvider)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent && .venv/bin/python -m pytest tests/test_calendar_provider.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'calendar_provider'`

- [ ] **Step 3: Write `agent/calendar_provider.py`**

```python
"""Where Recovery gets bookable time slots.

Owner picks a calendar system per client at onboarding in the long run (Google
Calendar, Jobber, Housecall Pro), but building real OAuth/API integrations before
a real client asks for a specific one violates Roster's own build rule (ROSTER.md:
"Guess integrations — never do this"). Only the manual fallback is wired up today.
Adding a live provider later is a new class here, same shape as channels.py.
"""
from datetime import datetime, timedelta
from typing import List, Protocol


class CalendarProvider(Protocol):
    def get_available_slots(self, business_hours: str, days_ahead: int = 7, count: int = 3) -> List[str]: ...


class ManualCalendarProvider:
    """No live calendar connected: propose slots on the next weekdays (skipping
    Sunday), alternating morning/afternoon windows, starting 2 days out to leave
    booking lead time."""

    def get_available_slots(self, business_hours: str, days_ahead: int = 7, count: int = 3) -> List[str]:
        slots: List[str] = []
        day = datetime.utcnow().date() + timedelta(days=2)
        while len(slots) < count:
            if day.weekday() != 6:  # skip Sunday
                window = "morning (9am-12pm)" if len(slots) % 2 == 0 else "afternoon (1pm-4pm)"
                slots.append(f"{day.strftime('%A %m/%d')} {window}")
            day += timedelta(days=1)
        return slots


def get_calendar_provider(client) -> CalendarProvider:
    # Every client uses the manual fallback until a live Google/Jobber/Housecall
    # Pro adapter is built for a real client who asks for it.
    return ManualCalendarProvider()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && .venv/bin/python -m pytest tests/test_calendar_provider.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add agent/calendar_provider.py agent/tests/test_calendar_provider.py
git commit -m "Add pluggable calendar provider (manual fallback only)"
```

---

### Task 3: Recovery engine — templates, tool schemas, prompts

**Files:**
- Create: `agent/recovery_engine.py`
- Test: `agent/tests/test_recovery_engine.py`

**Interfaces:**
- Produces: `SEQUENCE_DAYS: list[int]` = `[1, 3, 7, 14, 21, 28]`
- Produces: `TEMPLATES: dict[str, dict[int, str]]` keyed by face ("quote"/"reactivation") then day
- Produces: `RECORD_RESPONSE_TOOL`, `CONFIRM_SLOT_TOOL` (Anthropic tool schemas, same shape as `engine.LOG_JOB_TOOL`)
- Produces: `render_template(text: str, **variables) -> str`
- Produces: `build_recovery_reply_prompt(recovery_job, offered_slots: list[str] | None = None) -> str`
- Consumes: nothing (pure module, no DB/engine imports)

- [ ] **Step 1: Write the failing tests**

Create `agent/tests/test_recovery_engine.py`:
```python
from recovery_engine import SEQUENCE_DAYS, TEMPLATES, build_recovery_reply_prompt, render_template


class FakeRecoveryJob:
    def __init__(self, customer_name="Mike", service_type="AC install"):
        self.customer_name = customer_name
        self.service_type = service_type


def test_render_template_substitutes_known_variables():
    text = render_template(
        "Hi {customer_name}, about your {service_type}", customer_name="Mike", service_type="AC repair"
    )
    assert text == "Hi Mike, about your AC repair"


def test_render_template_leaves_missing_variables_blank():
    text = render_template("Hi {customer_name}, {estimate_amount}", customer_name="Mike")
    assert text == "Hi Mike, "


def test_templates_cover_every_sequence_day_for_both_faces():
    for face in ("quote", "reactivation"):
        for day in SEQUENCE_DAYS:
            assert day in TEMPLATES[face]
            assert "{service_type}" in TEMPLATES[face][day] or "{customer_name}" in TEMPLATES[face][day]


def test_prompt_without_slots_asks_for_intent_tool():
    prompt = build_recovery_reply_prompt(FakeRecoveryJob())
    assert "record_response" in prompt


def test_prompt_with_slots_asks_for_confirm_slot_tool():
    prompt = build_recovery_reply_prompt(FakeRecoveryJob(), offered_slots=["Monday morning", "Tuesday afternoon"])
    assert "confirm_slot" in prompt
    assert "Monday morning" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recovery_engine'`

- [ ] **Step 3: Write `agent/recovery_engine.py`**

```python
"""Revenue Recovery's Claude-facing pieces: message templates, tool schemas, and
system prompts. Pure — no DB or AgentEngine imports here; recovery_service.py
wires this into AgentEngine.respond() the same way engine.py's own tools do.
"""
from typing import List, Optional

SEQUENCE_DAYS = [1, 3, 7, 14, 21, 28]

TEMPLATES = {
    "quote": {
        1: "Hi {customer_name}, just following up on that {service_type} estimate. Still interested? Let me know!",
        3: "{customer_name}, spots are filling up for {service_type} work — want to lock in a time?",
        7: "Quick reminder: your {service_type} quote won't hold forever. Reply YES to book now.",
        14: "{customer_name}, anything holding you back on the {service_type} quote? Happy to answer questions.",
        21: "Your {service_type} quote is expiring soon. Ready to move forward — yes or no?",
        28: "Last chance: ready to book your {service_type}? Reply YES or let me know.",
    },
    "reactivation": {
        1: "Hi {customer_name}, it's been a while since your last {service_type} service. Time for a check-up! Want to book?",
        3: "{customer_name}, regular {service_type} maintenance keeps things running smooth. Let's get you scheduled.",
        7: "Heads up: {service_type} appointments are filling fast this season. Ready to book?",
        14: "{customer_name}, been a while! Ready for your {service_type} maintenance?",
        21: "Last call for {service_type} before the rush. Lock in your appointment today?",
        28: "{customer_name}, your system could use some attention. Book your {service_type} today?",
    },
}

RECORD_RESPONSE_TOOL = {
    "name": "record_response",
    "description": (
        "Call this once the customer's reply makes their intent clear: are they "
        "interested in booking, not interested, or asking to stop messages entirely."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "enum": ["interested", "not_interested", "unsubscribe"],
            },
        },
        "required": ["intent"],
    },
}

CONFIRM_SLOT_TOOL = {
    "name": "confirm_slot",
    "description": (
        "Call this once the customer has clearly picked one of the offered time "
        "slots. Match their reply to the closest offered slot by index."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "slot_index": {
                "type": "integer",
                "description": "0-based index into the offered slots list that best matches the customer's choice.",
            },
        },
        "required": ["slot_index"],
    },
}


class _SafeDict(dict):
    def __missing__(self, key):
        return ""


def render_template(text: str, **variables) -> str:
    return text.format_map(_SafeDict(variables))


def build_recovery_reply_prompt(recovery_job, offered_slots: Optional[List[str]] = None) -> str:
    base = (
        f"You are following up with {recovery_job.customer_name or 'a customer'} about "
        f"{recovery_job.service_type} on behalf of the business. This is an outbound "
        "follow-up conversation over text, not a fresh inquiry.\n\n"
    )
    if offered_slots:
        slot_list = "\n".join(f"{i}: {s}" for i, s in enumerate(offered_slots))
        return base + (
            f"You already offered these time slots:\n{slot_list}\n\n"
            "Figure out which slot the customer's reply matches and call confirm_slot "
            "with its index. If their reply doesn't clearly match any slot, ask a short "
            "clarifying question instead of guessing."
        )
    return base + (
        "Read the customer's reply and call record_response with their intent: "
        "'interested' if they want to book, 'not_interested' if they're declining, or "
        "'unsubscribe' if they're asking to stop texts. Keep any spoken reply short and warm."
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_engine.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add agent/recovery_engine.py agent/tests/test_recovery_engine.py
git commit -m "Add Recovery templates, tool schemas, and system prompts"
```

---

### Task 4: `recovery_service.create_campaign`

**Files:**
- Create: `agent/recovery_service.py`
- Test: `agent/tests/test_recovery_service.py`

**Interfaces:**
- Consumes: `RecoveryCampaign`, `RecoveryJob` from `db_models` (Task 1)
- Produces: `create_campaign(session, client, face: str, name: str, customers: list[dict], template_overrides: dict | None = None) -> RecoveryCampaign`
- Produces module-level: `agent = AgentEngine()`, `sms_channel = get_channel()` (mirrors `service.py`'s `agent = AgentEngine()`)

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_recovery_service.py`:
```python
import json

from sqlmodel import Session, select

from db_models import Client, RecoveryJob
import recovery_service


def make_client(session: Session) -> Client:
    client = Client(
        business_name="Test Co", trade="HVAC", services_json=json.dumps(["AC repair"]),
        hours="9-5", pricing_faq="n/a", escalation_phone="+15550000000",
        inbound_number="+15559990000",
    )
    session.add(client)
    session.commit()
    session.refresh(client)
    return client


def test_create_campaign_creates_one_job_per_customer(session):
    client = make_client(session)
    customers = [
        {"phone": "+1", "name": "Mike", "service_type": "AC install", "estimate_amount": "8000"},
        {"phone": "+2", "name": "Sue", "service_type": "Furnace repair", "estimate_amount": "3000"},
    ]

    campaign = recovery_service.create_campaign(session, client, "quote", "June quotes", customers)

    jobs = session.exec(select(RecoveryJob).where(RecoveryJob.campaign_id == campaign.id)).all()
    assert len(jobs) == 2
    assert {j.customer_phone for j in jobs} == {"+1", "+2"}
    assert all(j.current_status == "pending" for j in jobs)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recovery_service'`

- [ ] **Step 3: Write `agent/recovery_service.py`**

```python
"""Revenue Recovery: standalone outbound SMS agent that chases unsold quotes and
lapsed customers. Independent of the Frontdesk agent — Recovery owns its own
intake, reply handling, and booking, so a client can run Recovery without ever
enabling Frontdesk.
"""
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlmodel import Session, select

from channels import get_channel
from db_models import Client, RecoveryCampaign, RecoveryJob
from engine import AgentEngine

agent = AgentEngine()
sms_channel = get_channel()

ACTIVE_STATUSES = ("pending", "awaiting_slot")


def create_campaign(
    session: Session,
    client: Client,
    face: str,
    name: str,
    customers: List[Dict[str, Any]],
    template_overrides: Optional[Dict[str, str]] = None,
) -> RecoveryCampaign:
    """`customers` is a list of dicts with keys: phone, name (optional),
    service_type, estimate_amount (optional), days_since (optional)."""
    campaign = RecoveryCampaign(
        client_id=client.id,
        face=face,
        name=name,
        customer_list_json=json.dumps(customers),
        template_overrides_json=json.dumps(template_overrides or {}),
    )
    session.add(campaign)
    session.commit()
    session.refresh(campaign)

    for c in customers:
        session.add(
            RecoveryJob(
                campaign_id=campaign.id,
                client_id=client.id,
                customer_phone=c["phone"],
                customer_name=c.get("name"),
                service_type=c["service_type"],
                estimate_amount=c.get("estimate_amount"),
                days_since=c.get("days_since"),
            )
        )
    session.commit()
    return campaign


def find_active_recovery_job(session: Session, client_id: int, customer_phone: str) -> Optional[RecoveryJob]:
    return session.exec(
        select(RecoveryJob).where(
            RecoveryJob.client_id == client_id,
            RecoveryJob.customer_phone == customer_phone,
            RecoveryJob.current_status.in_(ACTIVE_STATUSES),
        )
    ).first()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_service.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add agent/recovery_service.py agent/tests/test_recovery_service.py
git commit -m "Add Recovery campaign creation"
```

---

### Task 5: `recovery_service.tick` — send due sequence messages

**Files:**
- Modify: `agent/recovery_service.py`
- Test: `agent/tests/test_recovery_service.py`

**Interfaces:**
- Consumes: `SEQUENCE_DAYS`, `TEMPLATES`, `render_template` from `recovery_engine` (Task 3)
- Consumes: `RecoveryMessageLog` from `db_models`
- Produces: `tick(session: Session) -> list[RecoveryJob]` (the jobs a message was just sent to)

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_recovery_service.py`:
```python
from datetime import datetime, timedelta

from db_models import RecoveryMessageLog


class FakeSMSChannel:
    def __init__(self):
        self.sent = []

    def send(self, from_number, to_number, body):
        self.sent.append({"from": from_number, "to": to_number, "body": body})


def test_tick_sends_day_one_message_once_elapsed(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(recovery_service, "sms_channel", fake_channel)

    client = make_client(session)
    campaign = recovery_service.create_campaign(
        session, client, "quote", "June quotes",
        [{"phone": "+1", "name": "Mike", "service_type": "AC install", "estimate_amount": "8000"}],
    )
    campaign.started_at = datetime.utcnow() - timedelta(days=1)
    session.add(campaign)
    session.commit()

    sent = recovery_service.tick(session)

    assert len(sent) == 1
    assert fake_channel.sent[0]["to"] == "+1"
    assert "Mike" in fake_channel.sent[0]["body"]
    logs = session.exec(select(RecoveryMessageLog)).all()
    assert len(logs) == 1
    assert logs[0].message_day == 1


def test_tick_does_not_resend_same_day_twice(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(recovery_service, "sms_channel", fake_channel)

    client = make_client(session)
    campaign = recovery_service.create_campaign(
        session, client, "quote", "June quotes",
        [{"phone": "+1", "name": "Mike", "service_type": "AC install"}],
    )
    campaign.started_at = datetime.utcnow() - timedelta(days=1)
    session.add(campaign)
    session.commit()

    recovery_service.tick(session)
    second = recovery_service.tick(session)

    assert second == []
    assert len(fake_channel.sent) == 1


def test_tick_marks_no_response_after_final_day(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(recovery_service, "sms_channel", fake_channel)

    client = make_client(session)
    campaign = recovery_service.create_campaign(
        session, client, "quote", "June quotes",
        [{"phone": "+1", "name": "Mike", "service_type": "AC install"}],
    )
    campaign.started_at = datetime.utcnow() - timedelta(days=40)
    session.add(campaign)
    session.commit()

    recovery_service.tick(session)  # catches up: sends the one unsent due message (day 28)
    recovery_service.tick(session)  # nothing left to send -> marks no_response

    job = session.exec(select(RecoveryJob)).first()
    assert job.current_status == "no_response"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_service.py -v`
Expected: FAIL with `AttributeError: module 'recovery_service' has no attribute 'tick'`

- [ ] **Step 3: Add `tick()` to `agent/recovery_service.py`**

Add these imports at the top (extend the existing import lines):
```python
from db_models import Client, RecoveryCampaign, RecoveryJob, RecoveryMessageLog
from recovery_engine import SEQUENCE_DAYS, TEMPLATES, render_template
```

Append this function:
```python
def _next_due_day(job: RecoveryJob, elapsed_days: int) -> Optional[int]:
    """Latest unsent sequence day whose threshold has passed. Returns the
    furthest one (not the first) so a job that missed several thresholds
    because tick() didn't run for a while jumps straight to where it should be,
    instead of replaying the whole backlog as a burst of texts."""
    candidate = None
    for day in SEQUENCE_DAYS:
        if job.last_sent_day is not None and day <= job.last_sent_day:
            continue
        if elapsed_days >= day:
            candidate = day
        else:
            break
    return candidate


def tick(session: Session) -> List[RecoveryJob]:
    """Send any due sequence messages across all active campaigns. Meant to be
    called once a day (see recovery_tick.py) — safe to call more often since
    it only ever sends a given sequence day's message once (tracked by
    last_sent_day)."""
    sent: List[RecoveryJob] = []
    jobs = session.exec(select(RecoveryJob).where(RecoveryJob.current_status == "pending")).all()

    for job in jobs:
        campaign = session.get(RecoveryCampaign, job.campaign_id)
        if campaign is None or not campaign.is_active:
            continue
        elapsed = (datetime.utcnow() - campaign.started_at).days
        due_day = _next_due_day(job, elapsed)

        if due_day is None:
            if job.last_sent_day == SEQUENCE_DAYS[-1] and elapsed > SEQUENCE_DAYS[-1]:
                job.current_status = "no_response"
                job.updated_at = datetime.utcnow()
                session.add(job)
                session.commit()
            continue

        client = session.get(Client, job.client_id)
        template = campaign.template_overrides.get(str(due_day)) or TEMPLATES[campaign.face][due_day]
        text = render_template(
            template,
            customer_name=job.customer_name or "there",
            service_type=job.service_type,
            estimate_amount=job.estimate_amount or "",
            days_since=job.days_since or "",
        )

        sms_channel.send(from_number=client.inbound_number or "", to_number=job.customer_phone, body=text)
        session.add(RecoveryMessageLog(recovery_job_id=job.id, message_day=due_day, message_text=text))
        job.last_sent_day = due_day
        job.updated_at = datetime.utcnow()
        session.add(job)
        session.commit()
        sent.append(job)

    return sent
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_service.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add agent/recovery_service.py agent/tests/test_recovery_service.py
git commit -m "Add Recovery tick: send due sequence messages"
```

---

### Task 6: `recovery_service.handle_recovery_reply` — intent + booking

**Files:**
- Modify: `agent/recovery_service.py`
- Test: `agent/tests/test_recovery_service.py`

**Interfaces:**
- Consumes: `RECORD_RESPONSE_TOOL`, `CONFIRM_SLOT_TOOL`, `build_recovery_reply_prompt` from `recovery_engine`
- Consumes: `get_calendar_provider` from `calendar_provider` (Task 2)
- Consumes: `Job` from `db_models`; `AgentEngine.respond()` contract: returns `{"reply": str, "jobs": list, "new_messages": list, "pending_tool_call": {"name": str, "input": dict} | None}` (from `engine.py`)
- Produces: `handle_recovery_reply(session: Session, client: Client, job: RecoveryJob, text: str) -> str`

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_recovery_service.py`:
```python
from db_models import Job
from conftest import StubAgent


def test_handle_recovery_reply_interested_offers_slots(session, monkeypatch):
    client = make_client(session)
    recovery_service.create_campaign(
        session, client, "quote", "June quotes",
        [{"phone": "+1", "name": "Mike", "service_type": "AC install"}],
    )
    job = session.exec(select(RecoveryJob)).first()
    job.last_sent_day = 1
    session.add(job)
    session.commit()
    session.add(RecoveryMessageLog(recovery_job_id=job.id, message_day=1, message_text="Hi Mike..."))
    session.commit()

    monkeypatch.setattr(
        recovery_service,
        "agent",
        StubAgent({
            "reply": "",
            "jobs": [],
            "new_messages": [],
            "pending_tool_call": {"name": "record_response", "input": {"intent": "interested"}},
        }),
    )

    reply = recovery_service.handle_recovery_reply(session, client, job, "Yes I'm interested!")

    session.refresh(job)
    assert job.current_status == "awaiting_slot"
    assert len(job.offered_slots) == 3
    assert "1)" in reply


def test_handle_recovery_reply_confirm_slot_books_job(session, monkeypatch):
    client = make_client(session)
    recovery_service.create_campaign(
        session, client, "quote", "June quotes",
        [{"phone": "+1", "name": "Mike", "service_type": "AC install"}],
    )
    job = session.exec(select(RecoveryJob)).first()
    job.current_status = "awaiting_slot"
    job.offered_slots_json = json.dumps(["Monday morning", "Tuesday afternoon", "Wednesday morning"])
    session.add(job)
    session.commit()

    monkeypatch.setattr(
        recovery_service,
        "agent",
        StubAgent({
            "reply": "",
            "jobs": [],
            "new_messages": [],
            "pending_tool_call": {"name": "confirm_slot", "input": {"slot_index": 1}},
        }),
    )

    reply = recovery_service.handle_recovery_reply(session, client, job, "Tuesday afternoon works")

    session.refresh(job)
    assert job.current_status == "booked"
    assert job.booked_job_id is not None
    booked = session.get(Job, job.booked_job_id)
    assert booked.service_type == "AC install"
    assert "Tuesday afternoon" in reply


def test_handle_recovery_reply_declined_stops_sequence(session, monkeypatch):
    client = make_client(session)
    recovery_service.create_campaign(
        session, client, "reactivation", "Dormant", [{"phone": "+1", "name": "Sue", "service_type": "Tune-up"}],
    )
    job = session.exec(select(RecoveryJob)).first()

    monkeypatch.setattr(
        recovery_service,
        "agent",
        StubAgent({
            "reply": "",
            "jobs": [],
            "new_messages": [],
            "pending_tool_call": {"name": "record_response", "input": {"intent": "not_interested"}},
        }),
    )

    recovery_service.handle_recovery_reply(session, client, job, "No thanks")

    session.refresh(job)
    assert job.current_status == "declined"


def test_find_active_recovery_job_only_matches_active_statuses(session):
    client = make_client(session)
    recovery_service.create_campaign(
        session, client, "quote", "June", [{"phone": "+1", "name": "Mike", "service_type": "AC install"}],
    )
    job = session.exec(select(RecoveryJob)).first()

    found = recovery_service.find_active_recovery_job(session, client.id, "+1")
    assert found is not None

    job.current_status = "booked"
    session.add(job)
    session.commit()

    assert recovery_service.find_active_recovery_job(session, client.id, "+1") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_service.py -v`
Expected: FAIL with `AttributeError: module 'recovery_service' has no attribute 'handle_recovery_reply'`

- [ ] **Step 3: Add `handle_recovery_reply()` to `agent/recovery_service.py`**

Add these imports at the top (extend the existing import lines):
```python
from calendar_provider import get_calendar_provider
from db_models import Client, Job, RecoveryCampaign, RecoveryJob, RecoveryMessageLog
from recovery_engine import (
    CONFIRM_SLOT_TOOL,
    RECORD_RESPONSE_TOOL,
    SEQUENCE_DAYS,
    TEMPLATES,
    build_recovery_reply_prompt,
    render_template,
)
```

Append this function:
```python
def handle_recovery_reply(session: Session, client: Client, job: RecoveryJob, text: str) -> str:
    """Process an inbound reply to an active Recovery sequence. Returns the text
    to send back to the customer (caller sends it — TwiML for SMS)."""
    log = session.exec(
        select(RecoveryMessageLog)
        .where(RecoveryMessageLog.recovery_job_id == job.id)
        .order_by(RecoveryMessageLog.id.desc())
    ).first()
    if log is not None and log.customer_reply is None:
        log.customer_reply = text
        log.replied_at = datetime.utcnow()
        session.add(log)

    history = [{"role": "user", "content": [{"type": "text", "text": text}]}]

    if job.current_status == "awaiting_slot":
        result = agent.respond(
            client.to_config(),
            history,
            tools=[CONFIRM_SLOT_TOOL],
            system_prompt=build_recovery_reply_prompt(job, offered_slots=job.offered_slots),
            max_iters=2,
        )
        reply = result["reply"] or "Sorry, could you confirm which time works — the first, second, or third option?"
        pending = result["pending_tool_call"]
        if pending and pending["name"] == "confirm_slot":
            idx = pending["input"]["slot_index"]
            slots = job.offered_slots
            if 0 <= idx < len(slots):
                chosen = slots[idx]
                new_job = Job(
                    client_id=client.id,
                    customer_phone=job.customer_phone,
                    customer_name=job.customer_name,
                    service_type=job.service_type,
                    urgency="routine",
                    callback_number=job.customer_phone,
                    notes=f"Booked via Revenue Recovery for {chosen}",
                )
                session.add(new_job)
                session.commit()
                session.refresh(new_job)
                job.booked_job_id = new_job.id
                job.current_status = "booked"
                reply = f"Perfect, you're booked for {chosen}! We'll text you a reminder. Any questions, just reply here."
        job.updated_at = datetime.utcnow()
        session.add(job)
        session.commit()
        return reply

    # current_status == "pending": first reply after a sequence message
    result = agent.respond(
        client.to_config(),
        history,
        tools=[RECORD_RESPONSE_TOOL],
        system_prompt=build_recovery_reply_prompt(job),
        max_iters=2,
    )
    pending = result["pending_tool_call"]
    intent = pending["input"]["intent"] if pending and pending["name"] == "record_response" else None

    if intent == "interested":
        provider = get_calendar_provider(client)
        slots = provider.get_available_slots(client.hours)
        job.offered_slots_json = json.dumps(slots)
        job.current_status = "awaiting_slot"
        slot_text = "; ".join(f"{i + 1}) {s}" for i, s in enumerate(slots))
        reply = f"Great! Which works best: {slot_text}?"
    elif intent in ("not_interested", "unsubscribe"):
        job.current_status = "declined"
        reply = "No problem, thanks for letting us know! We won't follow up further."
    else:
        reply = result["reply"] or "Thanks for the reply! Are you still interested in booking?"

    job.updated_at = datetime.utcnow()
    session.add(job)
    session.commit()
    return reply
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_service.py -v`
Expected: PASS (8 tests total in this file)

- [ ] **Step 5: Commit**

```bash
git add agent/recovery_service.py agent/tests/test_recovery_service.py
git commit -m "Add Recovery reply handling: intent capture, slot offer, booking"
```

---

### Task 7: Cron entry point (`recovery_tick.py`)

**Files:**
- Create: `agent/recovery_tick.py`
- Test: `agent/tests/test_recovery_tick.py`

**Interfaces:**
- Consumes: `tick` from `recovery_service` (Task 5); `engine`, `init_db` from `db`
- Produces: `run()` — callable entry point, also invoked via `if __name__ == "__main__"`

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_recovery_tick.py`:
```python
import recovery_tick


def test_run_executes_without_error(monkeypatch, test_engine, capsys):
    monkeypatch.setattr(recovery_tick, "engine", test_engine)
    recovery_tick.run()
    captured = capsys.readouterr()
    assert "Recovery tick: sent 0 message(s)." in captured.out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_tick.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recovery_tick'`

- [ ] **Step 3: Write `agent/recovery_tick.py`**

```python
"""Cron entry point: send any due Recovery sequence messages.

Run once a day, e.g. via crontab:
  0 9 * * * cd /path/to/agent && .venv/bin/python recovery_tick.py >> recovery.log 2>&1
Safe to run more than once a day — tick() only sends each sequence day once.
"""
from sqlmodel import Session

from db import engine, init_db
from recovery_service import tick


def run():
    init_db()
    with Session(engine) as session:
        sent = tick(session)
        print(f"Recovery tick: sent {len(sent)} message(s).")


if __name__ == "__main__":
    run()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_tick.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add agent/recovery_tick.py agent/tests/test_recovery_tick.py
git commit -m "Add recovery_tick.py cron entry point"
```

---

### Task 8: Dashboard — create a Recovery campaign

**Files:**
- Modify: `agent/app.py`
- Create: `agent/templates/recovery_new.html`
- Test: `agent/tests/test_recovery_endpoint.py`

**Interfaces:**
- Consumes: `create_campaign` from `recovery_service` (Task 4)
- Produces routes: `GET /clients/{client_id}/recovery/new`, `POST /clients/{client_id}/recovery/new`

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_recovery_endpoint.py`:
```python
import json

from fastapi.testclient import TestClient
from sqlmodel import Session, select

import app as app_module
from db_models import Client, RecoveryJob


def make_client(test_engine) -> int:
    with Session(test_engine) as session:
        client = Client(
            business_name="Test Co", trade="HVAC", services_json=json.dumps(["AC repair"]),
            hours="9-5", pricing_faq="n/a", escalation_phone="+15550000000",
            inbound_number="+15559990000",
        )
        session.add(client)
        session.commit()
        session.refresh(client)
        return client.id


def test_create_campaign_via_form(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)

    response = test_client.post(
        f"/clients/{client_id}/recovery/new",
        data={
            "face": "quote",
            "name": "June quotes",
            "customers_raw": "+15551112222,Mike,AC install,8000",
        },
    )

    assert response.status_code == 303
    with Session(test_engine) as session:
        jobs = session.exec(select(RecoveryJob)).all()
    assert len(jobs) == 1
    assert jobs[0].customer_name == "Mike"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_endpoint.py -v`
Expected: FAIL with 404 (route doesn't exist)

- [ ] **Step 3: Add routes to `agent/app.py` and create the template**

Extend the imports near the top of `agent/app.py`:
```python
from db_models import Client, Job, Message, RecoveryCampaign, RecoveryJob
from recovery_service import create_campaign, find_active_recovery_job, handle_recovery_reply
```

Add these routes (after `create_client`, before `client_detail`):
```python
@app.get("/clients/{client_id}/recovery/new")
def new_recovery_campaign_form(request: Request, client_id: int):
    with Session(engine) as session:
        client = session.get(Client, client_id)
    return templates.TemplateResponse(request, "recovery_new.html", {"client": client})


@app.post("/clients/{client_id}/recovery/new")
def create_recovery_campaign(
    client_id: int,
    face: str = Form(...),
    name: str = Form(...),
    customers_raw: str = Form(...),
):
    """`customers_raw` is one customer per line: phone,name,service_type,amount_or_days_since"""
    customers = []
    for line in customers_raw.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        entry = {"phone": parts[0], "name": parts[1], "service_type": parts[2]}
        if len(parts) > 3 and parts[3]:
            if face == "quote":
                entry["estimate_amount"] = parts[3]
            else:
                entry["days_since"] = parts[3]
        customers.append(entry)

    with Session(engine) as session:
        client = session.get(Client, client_id)
        campaign = create_campaign(session, client, face, name, customers)
    return RedirectResponse(f"/clients/{client_id}/recovery/{campaign.id}", status_code=303)
```

Create `agent/templates/recovery_new.html`:
```html
{% extends "base.html" %}
{% block title %}New Recovery campaign — Roster{% endblock %}
{% block content %}
<a class="back-link" href="/clients/{{ client.id }}">&larr; {{ client.business_name }}</a>
<h1>New Recovery campaign</h1>
<form method="post" action="/clients/{{ client.id }}/recovery/new" class="form">
  <label>Campaign type
    <select name="face">
      <option value="quote">Quote follow-up — chase unsold estimates</option>
      <option value="reactivation">Reactivation — rebook lapsed customers</option>
    </select>
  </label>
  <label>Campaign name<input name="name" placeholder="June unsold quotes" required></label>
  <label>Customers (one per line: phone,name,service_type,amount_or_days_since)
    <textarea name="customers_raw" rows="8" placeholder="+15551112222,Mike,AC install,8000" required></textarea>
  </label>
  <button class="btn btn-primary" type="submit">Start campaign</button>
</form>
{% endblock %}
```

Note: this task's route redirects to `/clients/{client_id}/recovery/{campaign.id}`, built in Task 9 — that route doesn't exist yet, so the test above only checks the redirect status/DB state, not the redirect target's content.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_endpoint.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add agent/app.py agent/templates/recovery_new.html agent/tests/test_recovery_endpoint.py
git commit -m "Add Recovery campaign creation form and route"
```

---

### Task 9: Dashboard — campaign detail view

**Files:**
- Modify: `agent/app.py`
- Create: `agent/templates/recovery_detail.html`
- Test: `agent/tests/test_recovery_endpoint.py`

**Interfaces:**
- Produces route: `GET /clients/{client_id}/recovery/{campaign_id}`

- [ ] **Step 1: Write the failing test**

Append to `agent/tests/test_recovery_endpoint.py`:
```python
def test_recovery_campaign_detail_renders(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)
    test_client.post(
        f"/clients/{client_id}/recovery/new",
        data={"face": "reactivation", "name": "Dormant list", "customers_raw": "+1,Sue,Tune-up,400"},
    )

    with Session(test_engine) as session:
        campaign_id = session.exec(select(app_module.RecoveryCampaign)).first().id

    response = test_client.get(f"/clients/{client_id}/recovery/{campaign_id}")
    assert response.status_code == 200
    assert "Sue" in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_endpoint.py -v`
Expected: FAIL with 404 (route doesn't exist)

- [ ] **Step 3: Add route to `agent/app.py` and create the template**

Add this route in `agent/app.py` (after `create_recovery_campaign`):
```python
@app.get("/clients/{client_id}/recovery/{campaign_id}")
def recovery_campaign_detail(request: Request, client_id: int, campaign_id: int):
    with Session(engine) as session:
        client = session.get(Client, client_id)
        campaign = session.get(RecoveryCampaign, campaign_id)
        jobs = session.exec(
            select(RecoveryJob).where(RecoveryJob.campaign_id == campaign_id).order_by(RecoveryJob.id)
        ).all()
    return templates.TemplateResponse(
        request, "recovery_detail.html", {"client": client, "campaign": campaign, "jobs": jobs}
    )
```

Create `agent/templates/recovery_detail.html`:
```html
{% extends "base.html" %}
{% block title %}{{ campaign.name }} — Roster{% endblock %}
{% block content %}
<a class="back-link" href="/clients/{{ client.id }}">&larr; {{ client.business_name }}</a>
<h1>{{ campaign.name }}</h1>
<span class="trade-tag">{{ campaign.face }}</span>

<div class="jobs-panel">
  <h2>Customers</h2>
  {% for j in jobs %}
  <div class="job-card">
    <div class="job-urgency status-{{ j.current_status }}">{{ j.current_status }}</div>
    <strong>{{ j.service_type }}</strong>
    {% if j.customer_name %}<div>{{ j.customer_name }}</div>{% endif %}
    <div>{{ j.customer_phone }}</div>
    {% if j.estimate_amount %}<div>Estimate: {{ j.estimate_amount }}</div>{% endif %}
  </div>
  {% endfor %}
  {% if not jobs %}<p class="empty">No customers in this campaign.</p>{% endif %}
</div>
{% endblock %}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_endpoint.py -v`
Expected: PASS (2 tests total in this file)

- [ ] **Step 5: Commit**

```bash
git add agent/app.py agent/templates/recovery_detail.html agent/tests/test_recovery_endpoint.py
git commit -m "Add Recovery campaign detail view"
```

---

### Task 10: Wire inbound SMS routing (Recovery before Frontdesk)

**Files:**
- Modify: `agent/app.py`
- Test: `agent/tests/test_recovery_endpoint.py`

**Interfaces:**
- Consumes: `find_active_recovery_job`, `handle_recovery_reply` from `recovery_service`

- [ ] **Step 1: Write the failing test**

Append to `agent/tests/test_recovery_endpoint.py`:
```python
from conftest import StubAgent
import recovery_service


def test_inbound_sms_routes_active_recovery_reply_to_recovery(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)
    test_client.post(
        f"/clients/{client_id}/recovery/new",
        data={"face": "quote", "name": "June quotes", "customers_raw": "+15551112222,Mike,AC install,8000"},
    )

    monkeypatch.setattr(
        recovery_service,
        "agent",
        StubAgent({
            "reply": "",
            "jobs": [],
            "new_messages": [],
            "pending_tool_call": {"name": "record_response", "input": {"intent": "interested"}},
        }),
    )

    response = test_client.post(
        "/webhook/sms",
        data={"From": "+15551112222", "To": "+15559990000", "Body": "Yes!"},
    )

    assert response.status_code == 200
    assert "1)" in response.text  # slot options offered, not a Frontdesk reply
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_endpoint.py -v`
Expected: FAIL — the webhook currently always routes to Frontdesk's `handle_customer_message`, which calls the real Anthropic API (no API key in test env) and errors, or at minimum doesn't produce "1)" in the response.

- [ ] **Step 3: Modify `/webhook/sms` in `agent/app.py`**

Replace the existing `inbound_sms` function body:
```python
@app.post("/webhook/sms")
async def inbound_sms(From: str = Form(...), To: str = Form(...), Body: str = Form(...)):
    """Twilio inbound SMS. Routes by the business line texted (To) and replies via
    TwiML — so the AI's response is sent with no outbound credentials required.
    An active Revenue Recovery conversation for this customer takes priority over
    Frontdesk, since Recovery started this thread; once it resolves (booked,
    declined, or no_response) future texts fall through to Frontdesk as before."""
    with Session(engine) as session:
        client = _find_client_by_inbound(session, To)
        if client is None:
            return twiml_reply("Sorry, this number isn't set up to receive messages.")
        recovery_job = find_active_recovery_job(session, client.id, From)
        if recovery_job is not None:
            reply = handle_recovery_reply(session, client, recovery_job, Body)
            return twiml_reply(reply)
        result = handle_customer_message(session, client, From, Body)
    return twiml_reply(result["reply"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_endpoint.py -v`
Expected: PASS (3 tests total in this file)

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `cd agent && .venv/bin/python -m pytest -v`
Expected: PASS — all existing Frontdesk/voice tests unaffected, all new Recovery tests pass

- [ ] **Step 6: Commit**

```bash
git add agent/app.py agent/tests/test_recovery_endpoint.py
git commit -m "Route active Recovery replies before Frontdesk in /webhook/sms"
```

---

### Task 11: Dashboard link + styling

**Files:**
- Modify: `agent/app.py` (pass `campaigns` into `client_detail`)
- Modify: `agent/templates/client_detail.html`
- Modify: `agent/static/styles.css`
- Test: `agent/tests/test_recovery_endpoint.py`

**Interfaces:** none new — this task only wires an existing query into an existing route/template.

- [ ] **Step 1: Write the failing test**

Append to `agent/tests/test_recovery_endpoint.py`:
```python
def test_client_detail_lists_recovery_campaigns(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)
    test_client.post(
        f"/clients/{client_id}/recovery/new",
        data={"face": "quote", "name": "June quotes", "customers_raw": "+1,Mike,AC install,8000"},
    )

    response = test_client.get(f"/clients/{client_id}")

    assert response.status_code == 200
    assert "June quotes" in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_endpoint.py -v`
Expected: FAIL — "June quotes" not in the client detail page yet

- [ ] **Step 3: Wire `campaigns` into `client_detail` and update the template**

In `agent/app.py`, modify the `client_detail` function's session block and template context:
```python
@app.get("/clients/{client_id}")
def client_detail(request: Request, client_id: int):
    with Session(engine) as session:
        client = session.get(Client, client_id)
        messages = session.exec(
            select(Message)
            .where(Message.client_id == client_id, Message.customer_phone == DASHBOARD_THREAD)
            .order_by(Message.id)
        ).all()
        jobs = session.exec(
            select(Job).where(Job.client_id == client_id).order_by(Job.created_at.desc())
        ).all()
        campaigns = session.exec(
            select(RecoveryCampaign).where(RecoveryCampaign.client_id == client_id).order_by(RecoveryCampaign.created_at.desc())
        ).all()

    chat = [
        {"role": m.role, "text": extract_display_text(json.loads(m.content_json))}
        for m in messages
    ]
    chat = [c for c in chat if c["text"]]

    return templates.TemplateResponse(
        request,
        "client_detail.html",
        {"client": client, "chat": chat, "jobs": jobs, "campaigns": campaigns},
    )
```

In `agent/templates/client_detail.html`, add this section right after the `<span class="trade-tag">{{ client.trade }}</span>` line and before `<div class="detail-grid">`:
```html
<section class="recovery-panel">
  <h2>Revenue Recovery campaigns</h2>
  <a class="btn btn-primary" href="/clients/{{ client.id }}/recovery/new">+ New campaign</a>
  {% for c in campaigns %}
  <div class="job-card">
    <a href="/clients/{{ client.id }}/recovery/{{ c.id }}"><strong>{{ c.name }}</strong></a>
    <span class="trade-tag">{{ c.face }}</span>
  </div>
  {% endfor %}
  {% if not campaigns %}<p class="empty">No Recovery campaigns yet.</p>{% endif %}
</section>
```

Append to `agent/static/styles.css`:
```css
.recovery-panel {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
  margin-top: 24px;
}
.recovery-panel h2 { font-size: 16px; margin-bottom: 14px; }
.recovery-panel .btn { margin-bottom: 14px; }

.job-urgency.status-pending { background: var(--bg-alt); color: var(--text-dim); }
.job-urgency.status-awaiting_slot { background: rgba(194, 105, 61, 0.12); color: var(--accent); }
.job-urgency.status-booked { background: rgba(75, 122, 91, 0.12); color: var(--good); }
.job-urgency.status-declined { background: rgba(176, 71, 62, 0.12); color: var(--bad); }
.job-urgency.status-no_response { background: var(--bg-alt); color: var(--soon); }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_endpoint.py -v`
Expected: PASS (4 tests total in this file)

- [ ] **Step 5: Run the full test suite**

Run: `cd agent && .venv/bin/python -m pytest -v`
Expected: PASS — all tests green

- [ ] **Step 6: Commit**

```bash
git add agent/app.py agent/templates/client_detail.html agent/static/styles.css agent/tests/test_recovery_endpoint.py
git commit -m "Link Recovery campaigns from the client dashboard"
```

---

### Task 12: README documentation

**Files:**
- Modify: `agent/README.md`

- [ ] **Step 1: Add a Revenue Recovery section**

Insert into `agent/README.md`, after the "## AI receptionist (Vapi, live voice)" section and before "## Pieces":
```markdown
## Revenue Recovery (quote follow-up + reactivation)

A standalone agent — independent of Frontdesk — that chases unsold estimates
("quote" campaigns) and lapsed customers ("reactivation" campaigns) via a
6-touch SMS sequence over ~4 weeks, and books the job itself when the customer
says yes.

### Running it
1. Open a client's dashboard page, click **+ New campaign** under "Revenue
   Recovery campaigns".
2. Pick a type (quote follow-up or reactivation), name the campaign, and paste
   customers one per line: `phone,name,service_type,amount_or_days_since`.
3. Set up a daily cron job to send due messages:
   ```bash
   0 9 * * * cd /path/to/agent && .venv/bin/python recovery_tick.py >> recovery.log 2>&1
   ```
   Safe to run more than once a day — each sequence day's message is only ever sent once.
4. When a customer replies, `/webhook/sms` checks for an active Recovery
   conversation before falling through to Frontdesk, so replies get routed
   correctly even on a shared inbound number.

### Scope (Phase 1)
- Only a manual/business-hours time-slot proposal is wired up — no live Google
  Calendar/Jobber/Housecall Pro sync yet (see `calendar_provider.py`). Add one
  when a real client names the system they use.
- No CRM auto-import — campaigns are seeded from a pasted customer list.
```

Update the "## Pieces" table to add:
```markdown
| `recovery_engine.py` | Recovery's templates, tool schemas, and system prompts |
| `recovery_service.py` | Campaign creation, daily tick, reply handling, booking |
| `recovery_tick.py` | Cron entry point — sends due sequence messages |
| `calendar_provider.py` | Pluggable time-slot source (manual fallback today) |
```

- [ ] **Step 2: Commit**

```bash
git add agent/README.md
git commit -m "Document Revenue Recovery setup and cron"
```

---

## Manual smoke test (after all tasks)

1. `cd agent && ./run.sh`
2. Open a client, click **+ New campaign**, create a "quote" campaign with your own phone number as one line: `+1XXXXXXXXXX,YourName,AC install,8000`.
3. Run `.venv/bin/python recovery_tick.py` manually — confirm you receive (or see printed to console, if no Twilio creds) the day-1 text.
4. Reply "yes" from that phone (or via the Twilio number if wired up) — confirm you get 3 proposed time slots.
5. Reply with one of the slots — confirm you get a booking confirmation, and that a new Job appears on the client's dashboard with a note referencing Revenue Recovery.
6. Confirm the campaign detail page shows the customer's status progressing: pending → awaiting_slot → booked.
