# Referrals Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Referrals agent — the first half of Lead-gen — that automatically texts a customer a few days after their job is marked done, asking them to refer a friend for an incentive, and captures whatever comes back as a referral lead for the owner to follow up on by hand.

**Architecture:** A new, self-contained module pair (`referral_engine.py` + `referral_service.py`) mirroring Recovery's shape but simpler — no campaigns, no manually-pasted customer list, no multi-day sequence, no booking state machine. The trigger is fully automatic (`Job.completed_at` + a fixed delay), the reply-handling is a single Claude call that extracts a referred name/phone with a raw-text fallback, and everything rides the existing daily cron and SMS channel. Deliberately independent of `recovery_engine.py`/`recovery_service.py` — no imports between them, same spirit as Frontdesk and Recovery not importing each other.

**Tech Stack:** Same as the rest of `agent/` — FastAPI, SQLModel/SQLite, Jinja2, pytest, no new dependencies.

## Global Constraints

- No new pip dependencies.
- `REFERRAL_DELAY_DAYS` and `REFERRAL_MESSAGE_TEMPLATE` are fixed constants, not owner-editable — only `Client.referral_incentive` is owner-set, matching how Chaser/Rebooker/Renewals' own templates aren't owner-editable in the UI yet either.
- Referrals is fully automatic — no campaign-creation step. Every job marked done is eligible once the client has an incentive set; there is no pasted customer list for this agent.
- A `ReferralLead` is always created when a reply arrives to an active referral ask, regardless of whether Claude can extract a clean name/phone — `raw_reply_text` is never optional, `referred_name`/`referred_phone` may be `None`.
- `find_active_referral_ask` must be time-bounded by `REFERRAL_REPLY_WINDOW_DAYS` (3 days) — an inbound text outside that window must fall through to Frontdesk normally, not match an old referral ask indefinitely.
- Routing priority in `/webhook/sms`: active Recovery thread first, active referral ask second, Frontdesk fallback last.
- `referral_engine.py` must stay a pure module (no DB/AgentEngine imports) and must not import from `recovery_engine.py` — small duplication (a `render_referral_template` helper) is preferred over cross-module coupling between sibling agent engines.
- Reuses the existing daily cron (`recovery_tick.py`) — no second cron script.

---

### Task 1: Data model extensions

**Files:**
- Modify: `agent/db_models.py`
- Test: `agent/tests/test_recovery_models.py`

**Interfaces:**
- Produces: `Job.referral_sent_at: Optional[datetime]` (`NULL` until the ask is sent)
- Produces: `Client.referral_incentive: Optional[str]` (`NULL` = referrals off for that client)
- Produces: `ReferralLead(id, client_id, source_job_id, asker_phone, referred_name, referred_phone, raw_reply_text, created_at)`

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_recovery_models.py`:
```python
from db_models import ReferralLead


def test_job_referral_sent_at_defaults_to_none(session):
    client = make_client(session)
    job = Job(client_id=client.id, service_type="AC repair", urgency="routine")
    session.add(job)
    session.commit()
    session.refresh(job)
    assert job.referral_sent_at is None


def test_client_referral_incentive_defaults_to_none(session):
    client = make_client(session)
    assert client.referral_incentive is None

    client.referral_incentive = "$25 off your next service"
    session.add(client)
    session.commit()
    session.refresh(client)
    assert client.referral_incentive == "$25 off your next service"


def test_referral_lead_roundtrips(session):
    client = make_client(session)
    job = Job(client_id=client.id, service_type="AC repair", urgency="routine", callback_number="+1")
    session.add(job)
    session.commit()
    session.refresh(job)

    lead = ReferralLead(
        client_id=client.id, source_job_id=job.id, asker_phone="+1",
        referred_name="Sarah", referred_phone="+15559998888",
        raw_reply_text="my friend Sarah, 555-998-8888",
    )
    session.add(lead)
    session.commit()
    session.refresh(lead)

    assert lead.source_job_id == job.id
    assert lead.referred_name == "Sarah"
    assert lead.raw_reply_text == "my friend Sarah, 555-998-8888"


def test_referral_lead_allows_null_referred_fields(session):
    client = make_client(session)
    job = Job(client_id=client.id, service_type="AC repair", urgency="routine", callback_number="+1")
    session.add(job)
    session.commit()
    session.refresh(job)

    lead = ReferralLead(client_id=client.id, source_job_id=job.id, asker_phone="+1", raw_reply_text="no thanks")
    session.add(lead)
    session.commit()
    session.refresh(lead)

    assert lead.referred_name is None
    assert lead.referred_phone is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_models.py -v`
Expected: FAIL — `referral_sent_at`/`referral_incentive` are unexpected keyword arguments, and `ImportError: cannot import name 'ReferralLead' from 'db_models'`

- [ ] **Step 3: Add the columns and the new table to `agent/db_models.py`**

Add `referral_incentive` to `Client`, right after `review_link`:
```python
class Client(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    business_name: str
    trade: str
    services_json: str  # JSON-encoded list[str]
    hours: str
    pricing_faq: str
    escalation_phone: str
    answer_mode: str = Field(default="backup")  # "primary" or "backup" - set during onboarding
    inbound_number: Optional[str] = None  # the business line customers text/call; routes inbound SMS
    review_link: Optional[str] = None  # owner's Google/Yelp review URL; unset until they provide one
    referral_incentive: Optional[str] = None  # e.g. "$25 off"; unset = referrals off for this client
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def services(self) -> List[str]:
        return json.loads(self.services_json)

    def to_config(self) -> ClientConfig:
        return ClientConfig(
            client_id=str(self.id),
            business_name=self.business_name,
            trade=self.trade,
            services=self.services,
            hours=self.hours,
            pricing_faq=self.pricing_faq,
            escalation_phone=self.escalation_phone,
            answer_mode=self.answer_mode,
        )
```

Add `referral_sent_at` to `Job`, right after `completed_at`:
```python
class Job(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="client.id")
    customer_phone: str = "dashboard"
    customer_name: Optional[str] = None
    service_type: str
    urgency: str
    address: Optional[str] = None
    callback_number: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None  # set by the "Mark done" action; drives the Reviews SMS
    referral_sent_at: Optional[datetime] = None  # set once the referral ask has gone out for this job
```

Add a new `ReferralLead` table at the end of the file, after `RecoveryMessageLog`:
```python
class ReferralLead(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="client.id")
    source_job_id: int = Field(foreign_key="job.id")  # which completed job triggered this ask
    asker_phone: str  # the existing customer who was asked
    referred_name: Optional[str] = None  # extracted by Claude, if present
    referred_phone: Optional[str] = None  # extracted by Claude, if present
    raw_reply_text: str  # always stored, regardless of extraction outcome
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_models.py -v`
Expected: PASS (10 tests total in this file)

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `cd agent && .venv/bin/python -m pytest -v`
Expected: PASS — all existing tests unaffected (every new field/table is additive and nullable/optional)

- [ ] **Step 6: Commit**

```bash
git add agent/db_models.py agent/tests/test_recovery_models.py
git commit -m "Add referral_sent_at, referral_incentive, and ReferralLead"
```

---

### Task 2: `referral_engine.py` — constants and extraction tool

**Files:**
- Create: `agent/referral_engine.py`
- Test: `agent/tests/test_referral_engine.py`

**Interfaces:**
- Produces: `REFERRAL_DELAY_DAYS: int` = `4`
- Produces: `REFERRAL_REPLY_WINDOW_DAYS: int` = `3`
- Produces: `REFERRAL_MESSAGE_TEMPLATE: str` (uses `{customer_name}`, `{service_type}`, `{incentive}`)
- Produces: `RECORD_REFERRAL_TOOL: dict` (Anthropic tool schema, no required fields — both `referred_name` and `referred_phone` are optional)
- Produces: `render_referral_template(text: str, **variables) -> str`
- Produces: `build_referral_reply_prompt() -> str`
- Consumes: nothing (pure module, no DB/engine imports, no import from `recovery_engine.py`)

- [ ] **Step 1: Write the failing tests**

Create `agent/tests/test_referral_engine.py`:
```python
from referral_engine import (
    RECORD_REFERRAL_TOOL,
    REFERRAL_DELAY_DAYS,
    REFERRAL_MESSAGE_TEMPLATE,
    REFERRAL_REPLY_WINDOW_DAYS,
    build_referral_reply_prompt,
    render_referral_template,
)


def test_render_referral_template_substitutes_known_variables():
    text = render_referral_template(
        REFERRAL_MESSAGE_TEMPLATE,
        customer_name="Mike", service_type="AC repair", incentive="$25 off your next service",
    )
    assert "Mike" in text
    assert "AC repair" in text
    assert "$25 off your next service" in text


def test_render_referral_template_leaves_missing_variables_blank():
    text = render_referral_template("Hi {customer_name}, {incentive}", customer_name="Mike")
    assert text == "Hi Mike, "


def test_referral_delay_and_window_are_positive_integers():
    assert REFERRAL_DELAY_DAYS > 0
    assert REFERRAL_REPLY_WINDOW_DAYS > 0


def test_record_referral_tool_has_no_required_fields():
    assert RECORD_REFERRAL_TOOL["input_schema"].get("required", []) == []
    assert "referred_name" in RECORD_REFERRAL_TOOL["input_schema"]["properties"]
    assert "referred_phone" in RECORD_REFERRAL_TOOL["input_schema"]["properties"]


def test_build_referral_reply_prompt_mentions_record_referral():
    prompt = build_referral_reply_prompt()
    assert "record_referral" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent && .venv/bin/python -m pytest tests/test_referral_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'referral_engine'`

- [ ] **Step 3: Write `agent/referral_engine.py`**

```python
"""Referrals' Claude-facing pieces: constants and the extraction tool schema.
Pure — no DB or AgentEngine imports here. Deliberately does not import from
recovery_engine.py (a tiny render_template equivalent is duplicated below
instead) so Referrals stays independent of Recovery's module structure — the
same spirit as Frontdesk and Recovery not importing each other.
"""

REFERRAL_DELAY_DAYS = 4
REFERRAL_REPLY_WINDOW_DAYS = 3

REFERRAL_MESSAGE_TEMPLATE = (
    "Hey {customer_name}, glad we could help with your {service_type}! Know "
    "anyone else who could use us? {incentive} — just reply with their name "
    "and number and we'll take it from there."
)

RECORD_REFERRAL_TOOL = {
    "name": "record_referral",
    "description": (
        "Call this after reading the customer's reply to a referral request. "
        "Extract the referred person's name and/or phone number if present. "
        "If the reply doesn't contain a clear name or phone number, call this "
        "with both fields omitted rather than guessing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "referred_name": {
                "type": "string",
                "description": "The referred person's name, if the customer mentioned one.",
            },
            "referred_phone": {
                "type": "string",
                "description": "The referred person's phone number, if the customer mentioned one.",
            },
        },
    },
}


class _SafeDict(dict):
    def __missing__(self, key):
        return ""


def render_referral_template(text: str, **variables) -> str:
    return text.format_map(_SafeDict(variables))


def build_referral_reply_prompt() -> str:
    return (
        "You just sent this customer a referral request after completing their "
        "service. Read their reply and call record_referral with the referred "
        "person's name and/or phone number if they gave one. If their reply "
        "doesn't contain a referral (e.g. they declined, asked a question, or "
        "said something unrelated), call record_referral with both fields "
        "omitted — do not guess or invent a name or number. Keep any spoken "
        "reply short and warm."
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && .venv/bin/python -m pytest tests/test_referral_engine.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add agent/referral_engine.py agent/tests/test_referral_engine.py
git commit -m "Add Referrals constants, message template, and extraction tool"
```

---

### Task 3: `referral_service.send_due_referral_asks` — the daily check

**Files:**
- Create: `agent/referral_service.py`
- Test: `agent/tests/test_referral_service.py`

**Interfaces:**
- Consumes: `Client`, `Job` from `db_models` (Task 1)
- Consumes: `REFERRAL_DELAY_DAYS`, `REFERRAL_MESSAGE_TEMPLATE`, `render_referral_template` from `referral_engine` (Task 2)
- Produces module-level: `agent = AgentEngine()`, `sms_channel = get_channel()` (mirrors `recovery_service.py`)
- Produces: `send_due_referral_asks(session: Session) -> list[Job]` (the jobs a referral text was just sent to)

- [ ] **Step 1: Write the failing tests**

Create `agent/tests/test_referral_service.py`:
```python
import json
from datetime import datetime, timedelta

from sqlmodel import Session, select

from db_models import Client, Job
import referral_service


def make_client(session: Session, referral_incentive=None) -> Client:
    client = Client(
        business_name="Test Co", trade="HVAC", services_json=json.dumps(["AC repair"]),
        hours="9-5", pricing_faq="n/a", escalation_phone="+15550000000",
        inbound_number="+15559990000", referral_incentive=referral_incentive,
    )
    session.add(client)
    session.commit()
    session.refresh(client)
    return client


class FakeSMSChannel:
    def __init__(self):
        self.sent = []

    def send(self, from_number, to_number, body):
        self.sent.append({"from": from_number, "to": to_number, "body": body})


def test_send_due_referral_asks_sends_to_eligible_job(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(referral_service, "sms_channel", fake_channel)

    client = make_client(session, referral_incentive="$25 off your next service")
    job = Job(
        client_id=client.id, service_type="AC repair", urgency="routine",
        customer_name="Mike", callback_number="+15551234567",
        completed_at=datetime.utcnow() - timedelta(days=5),
    )
    session.add(job)
    session.commit()

    sent = referral_service.send_due_referral_asks(session)

    assert len(sent) == 1
    assert fake_channel.sent[0]["to"] == "+15551234567"
    assert "Mike" in fake_channel.sent[0]["body"]
    assert "$25 off your next service" in fake_channel.sent[0]["body"]
    session.refresh(job)
    assert job.referral_sent_at is not None


def test_send_due_referral_asks_skips_job_completed_too_recently(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(referral_service, "sms_channel", fake_channel)

    client = make_client(session, referral_incentive="$25 off")
    job = Job(
        client_id=client.id, service_type="AC repair", urgency="routine",
        callback_number="+1", completed_at=datetime.utcnow() - timedelta(days=1),
    )
    session.add(job)
    session.commit()

    sent = referral_service.send_due_referral_asks(session)

    assert sent == []
    assert fake_channel.sent == []


def test_send_due_referral_asks_skips_client_without_incentive(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(referral_service, "sms_channel", fake_channel)

    client = make_client(session, referral_incentive=None)
    job = Job(
        client_id=client.id, service_type="AC repair", urgency="routine",
        callback_number="+1", completed_at=datetime.utcnow() - timedelta(days=5),
    )
    session.add(job)
    session.commit()

    sent = referral_service.send_due_referral_asks(session)

    assert sent == []


def test_send_due_referral_asks_does_not_resend(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(referral_service, "sms_channel", fake_channel)

    client = make_client(session, referral_incentive="$25 off")
    job = Job(
        client_id=client.id, service_type="AC repair", urgency="routine",
        callback_number="+1", completed_at=datetime.utcnow() - timedelta(days=5),
    )
    session.add(job)
    session.commit()

    referral_service.send_due_referral_asks(session)
    second = referral_service.send_due_referral_asks(session)

    assert second == []
    assert len(fake_channel.sent) == 1


class RaisingSMSChannel:
    """Raises for one phone number, sends normally for everyone else — simulates
    a single bad number failing mid-batch."""

    def __init__(self, bad_phone):
        self.bad_phone = bad_phone
        self.sent = []

    def send(self, from_number, to_number, body):
        if to_number == self.bad_phone:
            raise RuntimeError("simulated Twilio failure")
        self.sent.append({"from": from_number, "to": to_number, "body": body})


def test_send_due_referral_asks_isolates_per_job_failure(session, monkeypatch):
    fake_channel = RaisingSMSChannel(bad_phone="+1")
    monkeypatch.setattr(referral_service, "sms_channel", fake_channel)

    client = make_client(session, referral_incentive="$25 off")
    bad_job = Job(
        client_id=client.id, service_type="AC repair", urgency="routine",
        callback_number="+1", completed_at=datetime.utcnow() - timedelta(days=5),
    )
    good_job = Job(
        client_id=client.id, service_type="Furnace repair", urgency="routine",
        callback_number="+2", completed_at=datetime.utcnow() - timedelta(days=5),
    )
    session.add(bad_job)
    session.add(good_job)
    session.commit()

    sent = referral_service.send_due_referral_asks(session)

    assert len(sent) == 1
    assert sent[0].callback_number == "+2"
    session.refresh(bad_job)
    assert bad_job.referral_sent_at is None
    session.refresh(good_job)
    assert good_job.referral_sent_at is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent && .venv/bin/python -m pytest tests/test_referral_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'referral_service'`

- [ ] **Step 3: Write `agent/referral_service.py`**

```python
"""Referrals: automatic referral-nudge texts triggered off Job.completed_at.
Independent of Recovery — no campaigns, no manually-pasted customer list, no
multi-touch sequence. Every job marked done becomes automatically eligible
once the client has an incentive line set.
"""
from datetime import datetime, timedelta
from typing import List

from sqlmodel import Session, select

from channels import get_channel
from db_models import Client, Job
from engine import AgentEngine
from referral_engine import REFERRAL_DELAY_DAYS, REFERRAL_MESSAGE_TEMPLATE, render_referral_template

agent = AgentEngine()
sms_channel = get_channel()


def send_due_referral_asks(session: Session) -> List[Job]:
    """Find every completed job old enough, not yet asked, whose client has
    an incentive set, and send the referral text. Meant to be called once a
    day (see recovery_tick.py) — safe to call more often since
    referral_sent_at gates re-sending."""
    sent: List[Job] = []
    cutoff = datetime.utcnow() - timedelta(days=REFERRAL_DELAY_DAYS)
    jobs = session.exec(
        select(Job).where(
            Job.completed_at.is_not(None),
            Job.completed_at <= cutoff,
            Job.referral_sent_at.is_(None),
        )
    ).all()

    for job in jobs:
        client = session.get(Client, job.client_id)
        if client is None or not client.referral_incentive or not job.callback_number:
            continue

        try:
            text = render_referral_template(
                REFERRAL_MESSAGE_TEMPLATE,
                customer_name=job.customer_name or "there",
                service_type=job.service_type,
                incentive=client.referral_incentive,
            )
            sms_channel.send(from_number=client.inbound_number or "", to_number=job.callback_number, body=text)
            job.referral_sent_at = datetime.utcnow()
            session.add(job)
            session.commit()
            sent.append(job)
        except Exception as e:
            print(f"Referrals: failed to send to job {job.id}: {e}")
            session.rollback()
            continue

    return sent
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && .venv/bin/python -m pytest tests/test_referral_service.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add agent/referral_service.py agent/tests/test_referral_service.py
git commit -m "Add Referrals daily check: send due referral asks"
```

---

### Task 4: `referral_service` — reply handling and active-ask lookup

**Files:**
- Modify: `agent/referral_service.py`
- Test: `agent/tests/test_referral_service.py`

**Interfaces:**
- Consumes: `RECORD_REFERRAL_TOOL`, `REFERRAL_REPLY_WINDOW_DAYS`, `build_referral_reply_prompt` from `referral_engine` (Task 2)
- Consumes: `ReferralLead` from `db_models` (Task 1)
- Consumes: `AgentEngine.respond()` contract: returns `{"reply": str, "jobs": list, "new_messages": list, "pending_tool_call": {"name": str, "input": dict} | None}` (from `engine.py`)
- Produces: `find_active_referral_ask(session: Session, client_id: int, customer_phone: str) -> Optional[Job]`
- Produces: `handle_referral_reply(session: Session, client: Client, job: Job, text: str) -> str`

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_referral_service.py`:
```python
from db_models import ReferralLead
from conftest import StubAgent


def test_find_active_referral_ask_matches_within_window(session):
    client = make_client(session, referral_incentive="$25 off")
    job = Job(
        client_id=client.id, service_type="AC repair", urgency="routine",
        callback_number="+1", completed_at=datetime.utcnow() - timedelta(days=5),
        referral_sent_at=datetime.utcnow() - timedelta(days=1),
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    found = referral_service.find_active_referral_ask(session, client.id, "+1")
    assert found is not None
    assert found.id == job.id


def test_find_active_referral_ask_ignores_expired_window(session):
    client = make_client(session, referral_incentive="$25 off")
    job = Job(
        client_id=client.id, service_type="AC repair", urgency="routine",
        callback_number="+1", completed_at=datetime.utcnow() - timedelta(days=10),
        referral_sent_at=datetime.utcnow() - timedelta(days=5),  # outside the 3-day window
    )
    session.add(job)
    session.commit()

    assert referral_service.find_active_referral_ask(session, client.id, "+1") is None


def test_find_active_referral_ask_ignores_already_captured(session):
    client = make_client(session, referral_incentive="$25 off")
    job = Job(
        client_id=client.id, service_type="AC repair", urgency="routine",
        callback_number="+1", completed_at=datetime.utcnow() - timedelta(days=5),
        referral_sent_at=datetime.utcnow(),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    session.add(ReferralLead(
        client_id=client.id, source_job_id=job.id, asker_phone="+1", raw_reply_text="already replied",
    ))
    session.commit()

    assert referral_service.find_active_referral_ask(session, client.id, "+1") is None


def test_handle_referral_reply_extracts_structured_info(session, monkeypatch):
    client = make_client(session, referral_incentive="$25 off")
    job = Job(
        client_id=client.id, service_type="AC repair", urgency="routine",
        customer_name="Mike", callback_number="+1",
        completed_at=datetime.utcnow() - timedelta(days=5),
        referral_sent_at=datetime.utcnow(),
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    monkeypatch.setattr(
        referral_service,
        "agent",
        StubAgent({
            "reply": "",
            "jobs": [],
            "new_messages": [],
            "pending_tool_call": {
                "name": "record_referral",
                "input": {"referred_name": "Sarah", "referred_phone": "+15559998888"},
            },
        }),
    )

    reply = referral_service.handle_referral_reply(
        session, client, job, "yeah, my neighbor Sarah needs this, her number is 555-998-8888"
    )

    lead = session.exec(select(ReferralLead).where(ReferralLead.source_job_id == job.id)).first()
    assert lead is not None
    assert lead.referred_name == "Sarah"
    assert lead.referred_phone == "+15559998888"
    assert lead.raw_reply_text == "yeah, my neighbor Sarah needs this, her number is 555-998-8888"
    assert reply != ""


def test_handle_referral_reply_falls_back_to_raw_text_when_extraction_fails(session, monkeypatch):
    client = make_client(session, referral_incentive="$25 off")
    job = Job(
        client_id=client.id, service_type="AC repair", urgency="routine",
        customer_name="Mike", callback_number="+1",
        completed_at=datetime.utcnow() - timedelta(days=5),
        referral_sent_at=datetime.utcnow(),
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    monkeypatch.setattr(
        referral_service,
        "agent",
        StubAgent({"reply": "No worries!", "jobs": [], "new_messages": [], "pending_tool_call": None}),
    )

    reply = referral_service.handle_referral_reply(session, client, job, "no thanks")

    lead = session.exec(select(ReferralLead).where(ReferralLead.source_job_id == job.id)).first()
    assert lead is not None
    assert lead.referred_name is None
    assert lead.referred_phone is None
    assert lead.raw_reply_text == "no thanks"
    assert reply == "No worries!"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent && .venv/bin/python -m pytest tests/test_referral_service.py -v`
Expected: FAIL with `AttributeError: module 'referral_service' has no attribute 'find_active_referral_ask'`

- [ ] **Step 3: Add reply handling to `agent/referral_service.py`**

Replace the import block at the top with:
```python
from datetime import datetime, timedelta
from typing import List, Optional

from sqlmodel import Session, select

from channels import get_channel
from db_models import Client, Job, ReferralLead
from engine import AgentEngine
from referral_engine import (
    RECORD_REFERRAL_TOOL,
    REFERRAL_DELAY_DAYS,
    REFERRAL_MESSAGE_TEMPLATE,
    REFERRAL_REPLY_WINDOW_DAYS,
    build_referral_reply_prompt,
    render_referral_template,
)
```

Append these two functions after `send_due_referral_asks`:
```python
def find_active_referral_ask(session: Session, client_id: int, customer_phone: str) -> Optional[Job]:
    """A referral ask is 'active' — eligible to have an inbound reply routed to
    it — if it was sent within the last REFERRAL_REPLY_WINDOW_DAYS and hasn't
    already produced a captured lead. Time-bounded (unlike Recovery's
    status-based matching) because a referral ask has no ongoing status to
    track; without a bound, an unrelated text months later would still match
    "no lead captured yet" and get misrouted forever."""
    cutoff = datetime.utcnow() - timedelta(days=REFERRAL_REPLY_WINDOW_DAYS)
    jobs = session.exec(
        select(Job)
        .where(
            Job.client_id == client_id,
            Job.callback_number == customer_phone,
            Job.referral_sent_at.is_not(None),
            Job.referral_sent_at >= cutoff,
        )
        .order_by(Job.referral_sent_at.desc())
    ).all()

    for job in jobs:
        already_captured = session.exec(
            select(ReferralLead).where(ReferralLead.source_job_id == job.id)
        ).first()
        if already_captured is None:
            return job
    return None


def handle_referral_reply(session: Session, client: Client, job: Job, text: str) -> str:
    """Process an inbound reply to a referral ask. Always logs a ReferralLead,
    regardless of whether Claude can extract a clean name/phone from the
    reply — the raw text is never lost just because extraction was messy."""
    history = [{"role": "user", "content": [{"type": "text", "text": text}]}]
    result = agent.respond(
        client.to_config(),
        history,
        tools=[RECORD_REFERRAL_TOOL],
        system_prompt=build_referral_reply_prompt(),
        max_iters=2,
    )
    pending = result["pending_tool_call"]
    referred_name = None
    referred_phone = None
    if pending and pending["name"] == "record_referral":
        referred_name = pending["input"].get("referred_name")
        referred_phone = pending["input"].get("referred_phone")

    session.add(
        ReferralLead(
            client_id=client.id,
            source_job_id=job.id,
            asker_phone=job.callback_number,
            referred_name=referred_name,
            referred_phone=referred_phone,
            raw_reply_text=text,
        )
    )
    session.commit()
    return result["reply"] or "Thanks so much! We'll follow up with them directly."
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && .venv/bin/python -m pytest tests/test_referral_service.py -v`
Expected: PASS (all tests in this file)

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `cd agent && .venv/bin/python -m pytest -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agent/referral_service.py agent/tests/test_referral_service.py
git commit -m "Add Referrals reply handling: Claude extraction, raw-text fallback"
```

---

### Task 5: Wire the daily check into the existing cron

**Files:**
- Modify: `agent/recovery_tick.py`
- Test: `agent/tests/test_recovery_tick.py`

**Interfaces:**
- Consumes: `send_due_referral_asks` from `referral_service` (Task 3)

- [ ] **Step 1: Write the failing test**

Replace `agent/tests/test_recovery_tick.py` in full:
```python
import recovery_tick


def test_run_executes_without_error(monkeypatch, test_engine, capsys):
    monkeypatch.setattr(recovery_tick, "engine", test_engine)
    recovery_tick.run()
    captured = capsys.readouterr()
    assert "Recovery tick: sent 0 message(s)." in captured.out
    assert "Referrals: sent 0 message(s)." in captured.out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_tick.py -v`
Expected: FAIL — `"Referrals: sent 0 message(s)."` not in captured output

- [ ] **Step 3: Update `agent/recovery_tick.py`**

Replace the file in full:
```python
"""Cron entry point: send any due Recovery sequence messages and referral asks.

Run once a day, e.g. via crontab:
  0 9 * * * cd /path/to/agent && .venv/bin/python recovery_tick.py >> recovery.log 2>&1
Safe to run more than once a day — tick() and send_due_referral_asks() each
only ever send once per job (tracked by last_sent_day / referral_sent_at).
"""
from sqlmodel import Session

from db import engine, init_db
from recovery_service import tick
from referral_service import send_due_referral_asks


def run():
    init_db()
    with Session(engine) as session:
        sent = tick(session)
        print(f"Recovery tick: sent {len(sent)} message(s).")
        referral_sent = send_due_referral_asks(session)
        print(f"Referrals: sent {len(referral_sent)} message(s).")


if __name__ == "__main__":
    run()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_tick.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `cd agent && .venv/bin/python -m pytest -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agent/recovery_tick.py agent/tests/test_recovery_tick.py
git commit -m "Send due referral asks from the existing daily cron"
```

---

### Task 6: Inbound SMS routing for referral replies

**Files:**
- Modify: `agent/app.py`
- Test: `agent/tests/test_recovery_endpoint.py`

**Interfaces:**
- Consumes: `find_active_referral_ask`, `handle_referral_reply` from `referral_service` (Task 4)

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_recovery_endpoint.py`:
```python
from datetime import datetime, timedelta

import referral_service


def test_inbound_sms_routes_active_referral_reply(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)

    with Session(test_engine) as session:
        job = Job(
            client_id=client_id, service_type="AC repair", urgency="routine",
            customer_name="Mike", callback_number="+15551112222",
            completed_at=datetime.utcnow() - timedelta(days=5),
            referral_sent_at=datetime.utcnow(),
        )
        session.add(job)
        session.commit()

    monkeypatch.setattr(
        referral_service,
        "agent",
        StubAgent({
            "reply": "Thanks, we'll reach out to them!",
            "jobs": [],
            "new_messages": [],
            "pending_tool_call": {"name": "record_referral", "input": {"referred_name": "Sarah"}},
        }),
    )

    test_client = TestClient(app_module.app)
    response = test_client.post(
        "/webhook/sms",
        data={"From": "+15551112222", "To": "+15559990000", "Body": "my friend Sarah needs this"},
    )

    assert response.status_code == 200
    assert "Thanks, we'll reach out to them!" in response.text


def test_inbound_sms_prioritizes_active_recovery_over_referral(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)
    test_client.post(
        f"/clients/{client_id}/recovery/new",
        data={"face": "quote", "name": "June quotes", "customers_raw": "+15551112222,Mike,AC install,8000"},
    )
    with Session(test_engine) as session:
        recovery_job = session.exec(select(RecoveryJob)).first()
        recovery_job.last_sent_day = 1
        session.add(recovery_job)
        referral_job = Job(
            client_id=client_id, service_type="AC repair", urgency="routine",
            callback_number="+15551112222",
            completed_at=datetime.utcnow() - timedelta(days=5),
            referral_sent_at=datetime.utcnow(),
        )
        session.add(referral_job)
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

    response = test_client.post(
        "/webhook/sms",
        data={"From": "+15551112222", "To": "+15559990000", "Body": "Yes!"},
    )

    assert response.status_code == 200
    assert "1)" in response.text  # Recovery's slot-offer reply wins, not the referral handler
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_endpoint.py -v`
Expected: FAIL — the first new test gets a Frontdesk-style reply (or errors calling the real API) instead of the referral handler's reply, since no referral routing exists yet

- [ ] **Step 3: Update `agent/app.py`**

Add this import alongside the existing `recovery_service` import:
```python
from referral_service import find_active_referral_ask, handle_referral_reply
```

Replace the `inbound_sms` function body:
```python
@app.post("/webhook/sms")
async def inbound_sms(From: str = Form(...), To: str = Form(...), Body: str = Form(...)):
    """Twilio inbound SMS. Routes by the business line texted (To) and replies via
    TwiML — so the AI's response is sent with no outbound credentials required.
    An active Revenue Recovery conversation for this customer takes priority over
    Frontdesk, since Recovery started this thread; once it resolves (booked,
    declined, or no_response) future texts fall through to Frontdesk as before.
    An active referral ask (sent within the last few days, not yet replied to)
    is checked next — a one-shot capture with nothing time-sensitive about it,
    so it comes after Recovery's active negotiation but still ahead of Frontdesk."""
    with Session(engine) as session:
        client = _find_client_by_inbound(session, To)
        if client is None:
            return twiml_reply("Sorry, this number isn't set up to receive messages.")
        recovery_job = find_active_recovery_job(session, client.id, From)
        if recovery_job is not None:
            reply = handle_recovery_reply(session, client, recovery_job, Body)
            return twiml_reply(reply)
        referral_job = find_active_referral_ask(session, client.id, From)
        if referral_job is not None:
            reply = handle_referral_reply(session, client, referral_job, Body)
            return twiml_reply(reply)
        result = handle_customer_message(session, client, From, Body)
    return twiml_reply(result["reply"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_endpoint.py -v`
Expected: PASS (all tests in this file)

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `cd agent && .venv/bin/python -m pytest -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agent/app.py agent/tests/test_recovery_endpoint.py
git commit -m "Route active referral replies before Frontdesk in /webhook/sms"
```

---

### Task 7: Dashboard — Lead-gen tile, incentive setting, captured leads

**Files:**
- Modify: `agent/app.py`
- Modify: `agent/templates/client_detail.html`
- Test: `agent/tests/test_recovery_endpoint.py`

**Interfaces:**
- Produces route: `POST /clients/{client_id}/referral-incentive`
- Consumes: `ReferralLead` from `db_models` (Task 1)

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_recovery_endpoint.py`:
```python
from db_models import ReferralLead


def test_set_referral_incentive_saves_and_shows_on_client_detail(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)

    response = test_client.post(
        f"/clients/{client_id}/referral-incentive",
        data={"referral_incentive": "$25 off your next service"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    detail = test_client.get(f"/clients/{client_id}")
    assert "$25 off your next service" in detail.text
    assert "Lead-gen" in detail.text


def test_client_detail_shows_captured_referral_leads(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)

    with Session(test_engine) as session:
        job = Job(client_id=client_id, service_type="AC repair", urgency="routine", callback_number="+1")
        session.add(job)
        session.commit()
        session.refresh(job)
        session.add(ReferralLead(
            client_id=client_id, source_job_id=job.id, asker_phone="+1",
            referred_name="Sarah", referred_phone="+15559998888",
            raw_reply_text="my friend Sarah, 555-998-8888",
        ))
        session.commit()

    test_client = TestClient(app_module.app)
    response = test_client.get(f"/clients/{client_id}")

    assert "Sarah" in response.text
    assert "+15559998888" in response.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_endpoint.py -v`
Expected: FAIL with 404 (route doesn't exist), and the second test fails since `referral_leads` isn't in the template context yet

- [ ] **Step 3: Add the route and update `client_detail`**

Add `ReferralLead` to the `db_models` import in `agent/app.py`:
```python
from db_models import Client, Job, Message, RecoveryCampaign, RecoveryJob, ReferralLead
```

Add this route right after `complete_job`:
```python
@app.post("/clients/{client_id}/referral-incentive")
def set_referral_incentive(client_id: int, referral_incentive: str = Form(...)):
    with Session(engine) as session:
        client = session.get(Client, client_id)
        client.referral_incentive = referral_incentive.strip() or None
        session.add(client)
        session.commit()
    return RedirectResponse(f"/clients/{client_id}", status_code=303)
```

Replace the `client_detail` route:
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
        referral_leads = session.exec(
            select(ReferralLead).where(ReferralLead.client_id == client_id).order_by(ReferralLead.created_at.desc())
        ).all()

    chat = [
        {"role": m.role, "text": extract_display_text(json.loads(m.content_json))}
        for m in messages
    ]
    chat = [c for c in chat if c["text"]]

    campaigns_by_face = {"quote": [], "reactivation": [], "membership": []}
    for c in campaigns:
        campaigns_by_face.setdefault(c.face, []).append(c)

    return templates.TemplateResponse(
        request,
        "client_detail.html",
        {
            "client": client,
            "chat": chat,
            "jobs": jobs,
            "campaigns_by_face": campaigns_by_face,
            "face_display_names": FACE_DISPLAY_NAMES,
            "referral_leads": referral_leads,
        },
    )
```

- [ ] **Step 4: Add the Lead-gen tile to `agent/templates/client_detail.html`**

Add this as the last child of `.agent-roster-grid`, right after the existing Reviews `<div class="agent-tile">` and before the closing `</div>` of the grid:
```html
    <div class="agent-tile">
      <div class="agent-tile-head">
        <span class="agent-badge {{ 'agent-badge-active' if client.referral_incentive else 'agent-badge-off' }}">
          Lead-gen{% if client.referral_incentive %} &middot; on{% endif %}
        </span>
      </div>
      <p class="agent-tile-desc">Asks happy customers to refer a friend, a few days after the job's done.</p>
      <form method="post" action="/clients/{{ client.id }}/referral-incentive" class="review-link-form">
        <input type="text" name="referral_incentive" placeholder="$25 off your next service" value="{{ client.referral_incentive or '' }}">
        <button class="btn btn-secondary btn-small" type="submit">Save</button>
      </form>
      {% for lead in referral_leads %}
      <div class="job-card">
        {% if lead.referred_name or lead.referred_phone %}
        <strong>{{ lead.referred_name or "Unknown name" }}</strong>
        {% if lead.referred_phone %}<div>{{ lead.referred_phone }}</div>{% endif %}
        {% else %}
        <p class="job-notes">{{ lead.raw_reply_text }}</p>
        {% endif %}
      </div>
      {% endfor %}
      {% if not referral_leads %}<p class="empty">No referrals yet.</p>{% endif %}
    </div>
  </div>
</section>
```

Note: this reuses the existing `.review-link-form` CSS class (the same flex-row input+button layout Reviews already uses) — no new CSS needed for this task.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_endpoint.py -v`
Expected: PASS (all tests in this file)

- [ ] **Step 6: Run the full suite**

Run: `cd agent && .venv/bin/python -m pytest -v`
Expected: PASS — every test in the whole suite green

- [ ] **Step 7: Commit**

```bash
git add agent/app.py agent/templates/client_detail.html agent/tests/test_recovery_endpoint.py
git commit -m "Add Lead-gen dashboard tile: incentive setting and captured leads"
```

---

## Manual smoke test (after all tasks)

1. `cd agent && ./run.sh`
2. Open a client, set an incentive on the Lead-gen tile (e.g. "$25 off").
3. Create a job and mark it done (existing "Mark done" flow).
4. Manually backdate that job's `completed_at` a few days (or wait `REFERRAL_DELAY_DAYS`), then run `.venv/bin/python recovery_tick.py` — confirm you see `Referrals: sent 1 message(s).` and receive (or see printed to console, if no Twilio creds) the referral text with your incentive line in it.
5. Reply with a friend's name and number from that phone — confirm a `ReferralLead` shows up on the Lead-gen tile with the extracted name/phone.
6. Reply with something unrelated to a *different* test job's referral ask — confirm a `ReferralLead` still gets created with the raw text and no name/phone.
7. Confirm a customer with an active Recovery conversation still gets routed to Recovery, not the referral handler, even if they also have a pending referral ask.
