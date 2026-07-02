# Roster Crew Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Grow Roster's agent roster from 2 agents to 3 named agents plus 1 feature: rebrand Revenue Recovery's two existing faces as **Chaser** (quote follow-up) and **Rebooker** (reactivation), add a third face **Renewals** (membership renewals, date-anchored per customer instead of days-since-campaign-start), and add **Reviews** as a single-button feature (not a named agent) that texts a review link when a job is marked done.

**Architecture:** Renewals rides Recovery's existing `tick()`/reply/booking machinery as a third `face` value (`"membership"`) with a new date-anchored clock — `MEMBERSHIP_OFFSETS = [-30, -14, -7, 0, 7]`, a constant parallel to (never merged into) the existing `SEQUENCE_DAYS`. A pure `FACE_DISPLAY_NAMES` mapping in `recovery_engine.py` is the single place a raw `face` string becomes "Chaser"/"Rebooker"/"Renewals" for a human. Reply handling, slot booking, and STOP compliance need zero changes — `handle_recovery_reply` never branches on `campaign.face`. Reviews is unrelated to the Recovery engine: `Client.review_link` and `Job.completed_at` are its only two new columns, and it's one button plus one best-effort SMS send.

**Tech Stack:** Same as the rest of `agent/` — FastAPI, SQLModel/SQLite, Jinja2, pytest, no new dependencies.

## Global Constraints

- No new pip dependencies.
- `SEQUENCE_DAYS = [1, 3, 7, 14, 21, 28]` stays exactly as-is for the `"quote"` and `"reactivation"` faces — every existing test for those faces must remain green, untouched, throughout this plan.
- `MEMBERSHIP_OFFSETS = [-30, -14, -7, 0, 7]` is a new, separate constant — never merge it into `SEQUENCE_DAYS`.
- `FACE_DISPLAY_NAMES` (in `recovery_engine.py`) is the single source of truth for face → human name. No raw `face` string (`"quote"`, `"reactivation"`, `"membership"`) may be shown to a human anywhere in the dashboard or landing page after Task 6.
- No new database tables — every change in this plan extends an existing table (`RecoveryJob`, `Client`, `Job`) in `db_models.py`, which stays the single file holding every table.
- The Reviews SMS send must never block job completion — wrap it in try/except; `completed_at` is set regardless of send outcome.
- Follow existing file granularity: engine/service/tick/dashboard concerns stay in their existing files (`recovery_engine.py`, `recovery_service.py`, `app.py`) — no new files needed for this plan.

---

### Task 1: Data model extensions

**Files:**
- Modify: `agent/db_models.py`
- Test: `agent/tests/test_recovery_models.py`

**Interfaces:**
- Produces: `RecoveryJob.anchor_date: Optional[str]` (ISO `YYYY-MM-DD`, `NULL` except membership face)
- Produces: `Client.review_link: Optional[str]` (`NULL` until owner sets it)
- Produces: `Job.completed_at: Optional[datetime]` (`NULL` until marked done)

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_recovery_models.py`:
```python
from db_models import Job


def test_recovery_job_anchor_date_defaults_to_none_and_roundtrips(session):
    client = make_client(session)
    campaign = RecoveryCampaign(
        client_id=client.id, face="quote", name="June quotes", customer_list_json="[]",
    )
    session.add(campaign)
    session.commit()
    session.refresh(campaign)

    unanchored = RecoveryJob(
        campaign_id=campaign.id, client_id=client.id, customer_phone="+1", service_type="AC repair",
    )
    session.add(unanchored)
    session.commit()
    session.refresh(unanchored)
    assert unanchored.anchor_date is None

    anchored = RecoveryJob(
        campaign_id=campaign.id, client_id=client.id, customer_phone="+2",
        service_type="AC tune-up", anchor_date="2026-07-15",
    )
    session.add(anchored)
    session.commit()
    session.refresh(anchored)
    assert anchored.anchor_date == "2026-07-15"


def test_client_review_link_defaults_to_none(session):
    client = make_client(session)
    assert client.review_link is None

    client.review_link = "https://g.page/r/test"
    session.add(client)
    session.commit()
    session.refresh(client)
    assert client.review_link == "https://g.page/r/test"


def test_job_completed_at_defaults_to_none(session):
    client = make_client(session)
    job = Job(client_id=client.id, service_type="AC repair", urgency="routine")
    session.add(job)
    session.commit()
    session.refresh(job)
    assert job.completed_at is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_models.py -v`
Expected: FAIL — `anchor_date`/`review_link`/`completed_at` are unexpected keyword arguments (fields don't exist yet)

- [ ] **Step 3: Add the three columns to `agent/db_models.py`**

In the `Client` class, add `review_link` right after `inbound_number`:
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

In the `Job` class, add `completed_at` right after `created_at`:
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
```

In the `RecoveryJob` class, add `anchor_date` right after `days_since`:
```python
class RecoveryJob(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="recoverycampaign.id")
    client_id: int = Field(foreign_key="client.id")
    customer_phone: str
    customer_name: Optional[str] = None
    service_type: str
    estimate_amount: Optional[str] = None
    days_since: Optional[str] = None
    anchor_date: Optional[str] = None  # ISO YYYY-MM-DD; only set for the "membership" face
    current_status: str = "pending"  # pending, awaiting_slot, booked, declined, no_response
    last_sent_day: Optional[int] = None
    offered_slots_json: str = "[]"
    booked_job_id: Optional[int] = Field(default=None, foreign_key="job.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def offered_slots(self) -> List[str]:
        return json.loads(self.offered_slots_json)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_models.py -v`
Expected: PASS (6 tests total in this file)

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `cd agent && .venv/bin/python -m pytest -v`
Expected: PASS — all existing tests unaffected (new columns are all nullable/optional)

- [ ] **Step 6: Commit**

```bash
git add agent/db_models.py agent/tests/test_recovery_models.py
git commit -m "Add anchor_date, review_link, completed_at columns"
```

---

### Task 2: Naming layer + membership templates

**Files:**
- Modify: `agent/recovery_engine.py`
- Test: `agent/tests/test_recovery_engine.py`

**Interfaces:**
- Produces: `FACE_DISPLAY_NAMES: dict[str, str]` — `{"quote": "Chaser", "reactivation": "Rebooker", "membership": "Renewals"}`
- Produces: `MEMBERSHIP_OFFSETS: list[int]` = `[-30, -14, -7, 0, 7]`
- Produces: `TEMPLATES["membership"]: dict[int, str]` keyed by offset, using `{customer_name}`, `{service_type}`, `{renewal_date}`
- Consumes: nothing new (still a pure module, no DB/engine imports)

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_recovery_engine.py`:
```python
from recovery_engine import FACE_DISPLAY_NAMES, MEMBERSHIP_OFFSETS


def test_face_display_names_covers_every_known_face():
    assert FACE_DISPLAY_NAMES["quote"] == "Chaser"
    assert FACE_DISPLAY_NAMES["reactivation"] == "Rebooker"
    assert FACE_DISPLAY_NAMES["membership"] == "Renewals"


def test_membership_templates_cover_every_offset():
    for offset in MEMBERSHIP_OFFSETS:
        assert offset in TEMPLATES["membership"]
        text = TEMPLATES["membership"][offset]
        assert "{service_type}" in text or "{customer_name}" in text


def test_membership_offsets_are_ascending():
    assert MEMBERSHIP_OFFSETS == sorted(MEMBERSHIP_OFFSETS)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_engine.py -v`
Expected: FAIL with `ImportError: cannot import name 'FACE_DISPLAY_NAMES' from 'recovery_engine'`

- [ ] **Step 3: Add the naming layer and membership templates to `agent/recovery_engine.py`**

Add this constant right after `SEQUENCE_DAYS = [1, 3, 7, 14, 21, 28]`:
```python
SEQUENCE_DAYS = [1, 3, 7, 14, 21, 28]

# Days relative to a customer's own anchor_date, not to campaign.started_at.
# A separate constant from SEQUENCE_DAYS on purpose: it can be negative
# (before the renewal date) and is anchored per-customer, not per-campaign.
MEMBERSHIP_OFFSETS = [-30, -14, -7, 0, 7]

FACE_DISPLAY_NAMES = {
    "quote": "Chaser",
    "reactivation": "Rebooker",
    "membership": "Renewals",
}
```

Add a third face to the `TEMPLATES` dict — insert after the `"reactivation"` block, still inside the `TEMPLATES = {...}` dict:
```python
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
    "membership": {
        -30: "Hi {customer_name}, your {service_type} plan renews on {renewal_date} — want to get your visit on the books before then?",
        -14: "{customer_name}, your {service_type} renewal is coming up on {renewal_date}. Ready to schedule?",
        -7: "One week left before your {service_type} plan renews on {renewal_date} — lock in your visit now?",
        0: "Today's the day — your {service_type} plan renews. Book your visit now to keep your member pricing and priority scheduling.",
        7: "{customer_name}, your {service_type} plan lapsed last week. Still want to keep your member pricing? Reply YES to renew.",
    },
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_engine.py -v`
Expected: PASS (8 tests total in this file)

- [ ] **Step 5: Commit**

```bash
git add agent/recovery_engine.py agent/tests/test_recovery_engine.py
git commit -m "Add Chaser/Rebooker/Renewals naming layer and membership templates"
```

---

### Task 3: `create_campaign` membership support

**Files:**
- Modify: `agent/recovery_service.py`
- Test: `agent/tests/test_recovery_service.py`

**Interfaces:**
- Consumes: `RecoveryJob.anchor_date` from `db_models` (Task 1)
- Modifies: `create_campaign(...)` — now also reads `c.get("anchor_date")` per customer dict; signature unchanged

- [ ] **Step 1: Write the failing test**

Append to `agent/tests/test_recovery_service.py`:
```python
def test_create_campaign_membership_sets_anchor_date(session):
    client = make_client(session)
    customers = [
        {"phone": "+1", "name": "Sarah", "service_type": "AC tune-up", "anchor_date": "2026-07-15"},
    ]

    campaign = recovery_service.create_campaign(session, client, "membership", "July renewals", customers)

    job = session.exec(select(RecoveryJob).where(RecoveryJob.campaign_id == campaign.id)).first()
    assert job.anchor_date == "2026-07-15"


def test_create_campaign_quote_leaves_anchor_date_none(session):
    client = make_client(session)
    customers = [{"phone": "+1", "name": "Mike", "service_type": "AC install", "estimate_amount": "8000"}]

    campaign = recovery_service.create_campaign(session, client, "quote", "June quotes", customers)

    job = session.exec(select(RecoveryJob).where(RecoveryJob.campaign_id == campaign.id)).first()
    assert job.anchor_date is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_service.py -v`
Expected: FAIL — `job.anchor_date` is `None` when it should be `"2026-07-15"` for the membership test (the second test passes already, since `anchor_date` defaults to `None`, but write both now since they're one cohesive change)

- [ ] **Step 3: Wire `anchor_date` through in `agent/recovery_service.py`**

In `create_campaign`, add one line to the `RecoveryJob(...)` construction:
```python
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
                anchor_date=c.get("anchor_date"),
            )
        )
    session.commit()
    return campaign
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_service.py -v`
Expected: PASS (all tests in this file, including the two new ones)

- [ ] **Step 5: Commit**

```bash
git add agent/recovery_service.py agent/tests/test_recovery_service.py
git commit -m "Wire anchor_date through create_campaign for the membership face"
```

---

### Task 4: `tick()` membership clock

**Files:**
- Modify: `agent/recovery_service.py`
- Test: `agent/tests/test_recovery_service.py`

**Interfaces:**
- Consumes: `MEMBERSHIP_OFFSETS` from `recovery_engine` (Task 2)
- Modifies: `_next_due_day(job, elapsed_days)` → `_next_due_day(job, elapsed_days, day_list)` (now takes the day/offset list as a parameter instead of hardcoding `SEQUENCE_DAYS`)
- Modifies: `tick()` — branches on `campaign.face == "membership"` to pick the elapsed-time basis and day list

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_recovery_service.py`:
```python
def test_tick_sends_membership_offset_before_renewal(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(recovery_service, "sms_channel", fake_channel)

    client = make_client(session)
    renewal = (datetime.utcnow() + timedelta(days=25)).strftime("%Y-%m-%d")
    campaign = recovery_service.create_campaign(
        session, client, "membership", "July renewals",
        [{"phone": "+1", "name": "Sarah", "service_type": "AC tune-up", "anchor_date": renewal}],
    )

    sent = recovery_service.tick(session)

    assert len(sent) == 1
    assert fake_channel.sent[0]["to"] == "+1"
    assert "Sarah" in fake_channel.sent[0]["body"]
    job = session.exec(select(RecoveryJob).where(RecoveryJob.campaign_id == campaign.id)).first()
    assert job.last_sent_day == -30


def test_tick_membership_catch_up_lands_on_latest_offset_not_burst(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(recovery_service, "sms_channel", fake_channel)

    client = make_client(session)
    renewal = (datetime.utcnow() + timedelta(days=5)).strftime("%Y-%m-%d")  # added late, 5 days out
    recovery_service.create_campaign(
        session, client, "membership", "July renewals",
        [{"phone": "+1", "name": "Sarah", "service_type": "AC tune-up", "anchor_date": renewal}],
    )

    sent = recovery_service.tick(session)

    assert len(sent) == 1
    job = session.exec(select(RecoveryJob)).first()
    assert job.last_sent_day == -7  # jumps straight to -7, doesn't replay -30/-14 as a burst


def test_tick_membership_does_not_resend_same_offset_twice(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(recovery_service, "sms_channel", fake_channel)

    client = make_client(session)
    renewal = (datetime.utcnow() + timedelta(days=25)).strftime("%Y-%m-%d")
    recovery_service.create_campaign(
        session, client, "membership", "July renewals",
        [{"phone": "+1", "name": "Sarah", "service_type": "AC tune-up", "anchor_date": renewal}],
    )

    recovery_service.tick(session)
    second = recovery_service.tick(session)

    assert second == []
    assert len(fake_channel.sent) == 1


def test_tick_membership_marks_no_response_after_final_offset(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(recovery_service, "sms_channel", fake_channel)

    client = make_client(session)
    renewal = (datetime.utcnow() - timedelta(days=10)).strftime("%Y-%m-%d")  # renewed 10 days ago
    recovery_service.create_campaign(
        session, client, "membership", "July renewals",
        [{"phone": "+1", "name": "Sarah", "service_type": "AC tune-up", "anchor_date": renewal}],
    )

    recovery_service.tick(session)  # catches up: sends the one unsent due offset (+7)
    recovery_service.tick(session)  # nothing left to send -> marks no_response

    job = session.exec(select(RecoveryJob)).first()
    assert job.current_status == "no_response"


def test_tick_quote_face_unaffected_by_membership_branch(session, monkeypatch):
    """Regression guard: byte-identical behavior for the pre-existing faces."""
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
    job = session.exec(select(RecoveryJob).where(RecoveryJob.campaign_id == campaign.id)).first()
    assert job.last_sent_day == 1
    assert job.anchor_date is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_service.py -v`
Expected: FAIL — membership jobs never get a due day (`tick()` doesn't know about `MEMBERSHIP_OFFSETS` or `anchor_date` yet), so `sent` is empty in every new membership test

- [ ] **Step 3: Add the membership clock to `agent/recovery_service.py`**

Update the `recovery_engine` import to include `MEMBERSHIP_OFFSETS`:
```python
from recovery_engine import (
    CONFIRM_SLOT_TOOL,
    MEMBERSHIP_OFFSETS,
    RECORD_RESPONSE_TOOL,
    SEQUENCE_DAYS,
    TEMPLATES,
    build_recovery_reply_prompt,
    render_template,
)
```

Replace `_next_due_day`:
```python
def _next_due_day(job: RecoveryJob, elapsed_days: int, day_list: List[int]) -> Optional[int]:
    """Latest unsent day/offset whose threshold has passed, from `day_list`
    (must be ascending). Returns the furthest one (not the first) so a job
    that missed several thresholds jumps straight to where it should be,
    instead of replaying the whole backlog as a burst of texts. Works
    identically for the day-count faces (SEQUENCE_DAYS, elapsed_days >= 0)
    and the date-anchored membership face (MEMBERSHIP_OFFSETS, elapsed_days
    can be negative — the comparison logic doesn't care about sign)."""
    candidate = None
    for day in day_list:
        if job.last_sent_day is not None and day <= job.last_sent_day:
            continue
        if elapsed_days >= day:
            candidate = day
        else:
            break
    return candidate
```

Replace `tick()`:
```python
def tick(session: Session) -> List[RecoveryJob]:
    """Send any due sequence messages across all active campaigns. Meant to be
    called once a day (see recovery_tick.py) — safe to call more often since
    it only ever sends a given sequence day/offset's message once (tracked by
    last_sent_day)."""
    sent: List[RecoveryJob] = []
    jobs = session.exec(select(RecoveryJob).where(RecoveryJob.current_status == "pending")).all()

    for job in jobs:
        campaign = session.get(RecoveryCampaign, job.campaign_id)
        if campaign is None or not campaign.is_active:
            continue

        if campaign.face == "membership":
            if job.anchor_date is None:
                continue
            anchor = datetime.strptime(job.anchor_date, "%Y-%m-%d")
            elapsed = (datetime.utcnow() - anchor).days
            day_list = MEMBERSHIP_OFFSETS
        else:
            elapsed = (datetime.utcnow() - campaign.started_at).days
            day_list = SEQUENCE_DAYS

        due_day = _next_due_day(job, elapsed, day_list)

        if due_day is None:
            if job.last_sent_day == day_list[-1] and elapsed > day_list[-1]:
                job.current_status = "no_response"
                job.updated_at = datetime.utcnow()
                session.add(job)
                session.commit()
            continue

        try:
            client = session.get(Client, job.client_id)
            template = campaign.template_overrides.get(str(due_day)) or TEMPLATES[campaign.face][due_day]
            text = render_template(
                template,
                customer_name=job.customer_name or "there",
                service_type=job.service_type,
                estimate_amount=job.estimate_amount or "",
                days_since=job.days_since or "",
                renewal_date=job.anchor_date or "",
            )

            sms_channel.send(from_number=client.inbound_number or "", to_number=job.customer_phone, body=text)
            session.add(RecoveryMessageLog(recovery_job_id=job.id, message_day=due_day, message_text=text))
            job.last_sent_day = due_day
            job.updated_at = datetime.utcnow()
            session.add(job)
            session.commit()
            sent.append(job)
        except Exception as e:
            print(f"Recovery tick: failed to send to job {job.id}: {e}")
            session.rollback()
            continue

    return sent
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_service.py -v`
Expected: PASS (all tests in this file)

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `cd agent && .venv/bin/python -m pytest -v`
Expected: PASS — every existing quote/reactivation test stays green, proving the non-membership path is unaffected

- [ ] **Step 6: Commit**

```bash
git add agent/recovery_service.py agent/tests/test_recovery_service.py
git commit -m "Add date-anchored membership clock to tick()"
```

---

### Task 5: Dashboard — campaign form gains the membership face

**Files:**
- Modify: `agent/app.py`
- Modify: `agent/templates/recovery_new.html`
- Test: `agent/tests/test_recovery_endpoint.py`

**Interfaces:**
- Modifies: `GET /clients/{client_id}/recovery/new` — now reads an optional `?face=` query param and passes it to the template as `preselect_face`
- Modifies: `POST /clients/{client_id}/recovery/new` — the pasted list's 4th column is parsed as `anchor_date` (validated `YYYY-MM-DD`) when `face == "membership"`

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_recovery_endpoint.py`:
```python
def test_new_campaign_form_preselects_face_from_query_param(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)

    response = test_client.get(f"/clients/{client_id}/recovery/new?face=membership")

    assert response.status_code == 200
    assert 'value="membership" selected' in response.text


def test_create_membership_campaign_accepts_valid_date(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)

    response = test_client.post(
        f"/clients/{client_id}/recovery/new",
        data={"face": "membership", "name": "July renewals", "customers_raw": "+1,Sarah,AC tune-up,2026-07-15"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(test_engine) as session:
        job = session.exec(select(RecoveryJob)).first()
    assert job.anchor_date == "2026-07-15"


def test_create_membership_campaign_rejects_malformed_date(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)

    response = test_client.post(
        f"/clients/{client_id}/recovery/new",
        data={"face": "membership", "name": "July renewals", "customers_raw": "+1,Sarah,AC tune-up,not-a-date"},
    )

    assert response.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_endpoint.py -v`
Expected: FAIL — no `selected` attribute is ever rendered yet, and a malformed date is currently accepted silently (200/303, not 400)

- [ ] **Step 3: Update `agent/app.py` and `agent/templates/recovery_new.html`**

Add `datetime` and `HTTPException` to the imports at the top of `agent/app.py`:
```python
import json
import time
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
```

Replace the two recovery-campaign-creation routes:
```python
@app.get("/clients/{client_id}/recovery/new")
def new_recovery_campaign_form(request: Request, client_id: int):
    with Session(engine) as session:
        client = session.get(Client, client_id)
    preselect_face = request.query_params.get("face", "quote")
    return templates.TemplateResponse(
        request, "recovery_new.html", {"client": client, "preselect_face": preselect_face}
    )


@app.post("/clients/{client_id}/recovery/new")
def create_recovery_campaign(
    client_id: int,
    face: str = Form(...),
    name: str = Form(...),
    customers_raw: str = Form(...),
):
    """`customers_raw` is one customer per line:
    phone,name,service_type,amount_or_days_since_or_renewal_date
    (the 4th column's meaning depends on `face`: estimate_amount for quote,
    days_since for reactivation, renewal_date (YYYY-MM-DD) for membership)."""
    customers = []
    for i, line in enumerate(customers_raw.strip().splitlines(), start=1):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        entry = {"phone": parts[0], "name": parts[1], "service_type": parts[2]}
        if len(parts) > 3 and parts[3]:
            if face == "quote":
                entry["estimate_amount"] = parts[3]
            elif face == "membership":
                try:
                    datetime.strptime(parts[3], "%Y-%m-%d")
                except ValueError:
                    raise HTTPException(
                        400, detail=f"Row {i}: '{parts[3]}' is not a valid renewal date (use YYYY-MM-DD)"
                    )
                entry["anchor_date"] = parts[3]
            else:
                entry["days_since"] = parts[3]
        customers.append(entry)

    with Session(engine) as session:
        client = session.get(Client, client_id)
        campaign = create_campaign(session, client, face, name, customers)
        campaign_id = campaign.id
    return RedirectResponse(f"/clients/{client_id}/recovery/{campaign_id}", status_code=303)
```

Replace `agent/templates/recovery_new.html` in full:
```html
{% extends "base.html" %}
{% block title %}New Recovery campaign — Roster{% endblock %}
{% block content %}
<a class="back-link" href="/clients/{{ client.id }}">&larr; {{ client.business_name }}</a>
<h1>New Recovery campaign</h1>
<form method="post" action="/clients/{{ client.id }}/recovery/new" class="form">
  <label>Campaign type
    <select name="face">
      <option value="quote" {{ "selected" if preselect_face == "quote" }}>Chaser — quote follow-up</option>
      <option value="reactivation" {{ "selected" if preselect_face == "reactivation" }}>Rebooker — reactivate lapsed customers</option>
      <option value="membership" {{ "selected" if preselect_face == "membership" }}>Renewals — chase membership renewals</option>
    </select>
  </label>
  <label>Campaign name<input name="name" placeholder="June unsold quotes" required></label>
  <label>Customers (one per line: phone,name,service_type,amount_or_days_since_or_renewal_date)
    <textarea name="customers_raw" rows="8" placeholder="+15551112222,Mike,AC install,8000 (Renewals: use a date like 2026-07-15)" required></textarea>
  </label>
  <button class="btn btn-primary" type="submit">Start campaign</button>
</form>
{% endblock %}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_endpoint.py -v`
Expected: PASS (all tests in this file)

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `cd agent && .venv/bin/python -m pytest -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agent/app.py agent/templates/recovery_new.html agent/tests/test_recovery_endpoint.py
git commit -m "Add Renewals face to campaign form with renewal-date validation"
```

---

### Task 6: Dashboard — split the agent roster into named tiles

**Files:**
- Modify: `agent/app.py`
- Modify: `agent/templates/client_detail.html`
- Modify: `agent/templates/recovery_detail.html`
- Test: `agent/tests/test_recovery_endpoint.py`

**Interfaces:**
- Consumes: `FACE_DISPLAY_NAMES` from `recovery_engine` (Task 2)
- Modifies: `client_detail` route — passes `campaigns_by_face: dict[str, list[RecoveryCampaign]]` and `face_display_names` instead of a flat `campaigns` list
- Modifies: `recovery_campaign_detail` route — passes `face_display_name: str` instead of relying on the template to show the raw `campaign.face`

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_recovery_endpoint.py`:
```python
def test_client_detail_groups_campaigns_by_named_agent_tile(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)
    test_client.post(
        f"/clients/{client_id}/recovery/new",
        data={"face": "quote", "name": "June quotes", "customers_raw": "+1,Mike,AC install,8000"},
    )
    test_client.post(
        f"/clients/{client_id}/recovery/new",
        data={"face": "membership", "name": "July renewals", "customers_raw": "+2,Sarah,AC tune-up,2026-07-15"},
    )

    response = test_client.get(f"/clients/{client_id}")

    assert response.status_code == 200
    assert "Chaser" in response.text
    assert "Rebooker" in response.text
    assert "Renewals" in response.text
    assert "June quotes" in response.text
    assert "July renewals" in response.text


def test_recovery_campaign_detail_shows_display_name_not_raw_face(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)
    test_client.post(
        f"/clients/{client_id}/recovery/new",
        data={"face": "membership", "name": "July renewals", "customers_raw": "+1,Sarah,AC tune-up,2026-07-15"},
    )
    with Session(test_engine) as session:
        campaign_id = session.exec(select(app_module.RecoveryCampaign)).first().id

    response = test_client.get(f"/clients/{client_id}/recovery/{campaign_id}")

    assert response.status_code == 200
    assert "Renewals" in response.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_endpoint.py -v`
Expected: FAIL — the client detail page currently shows one combined "Recovery" tile with raw face strings, not three named tiles; the campaign detail page shows the raw `"membership"` string, not `"Renewals"`

- [ ] **Step 3: Update `agent/app.py`, `agent/templates/client_detail.html`, `agent/templates/recovery_detail.html`**

Add `FACE_DISPLAY_NAMES` to the `recovery_engine` import in `agent/app.py` (add this import line near the other local imports):
```python
from recovery_engine import FACE_DISPLAY_NAMES
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
        },
    )
```

Replace the `recovery_campaign_detail` route:
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
        request,
        "recovery_detail.html",
        {
            "client": client,
            "campaign": campaign,
            "jobs": jobs,
            "face_display_name": FACE_DISPLAY_NAMES[campaign.face],
        },
    )
```

In `agent/templates/client_detail.html`, replace the whole `<section class="agent-roster">...</section>` block:
```html
<section class="agent-roster">
  <h2 class="agent-roster-heading">Agent roster</h2>
  <div class="agent-roster-grid">
    <div class="agent-tile">
      <div class="agent-tile-head">
        <span class="agent-badge agent-badge-active">Frontdesk</span>
      </div>
      <p class="agent-tile-desc">Always on — answers inbound calls &amp; texts for this client.</p>
    </div>

    {% for face in ["quote", "reactivation", "membership"] %}
    <div class="agent-tile recovery-panel">
      <div class="agent-tile-head">
        <span class="agent-badge {{ 'agent-badge-active' if campaigns_by_face[face] else 'agent-badge-off' }}">
          {{ face_display_names[face] }}{% if campaigns_by_face[face] %} &middot; {{ campaigns_by_face[face]|length }}{% endif %}
        </span>
        <a class="btn btn-primary btn-small" href="/clients/{{ client.id }}/recovery/new?face={{ face }}">+ New campaign</a>
      </div>
      {% for c in campaigns_by_face[face] %}
      <div class="job-card">
        <a href="/clients/{{ client.id }}/recovery/{{ c.id }}"><strong>{{ c.name }}</strong></a>
      </div>
      {% endfor %}
      {% if not campaigns_by_face[face] %}<p class="empty">No {{ face_display_names[face] }} campaigns yet.</p>{% endif %}
    </div>
    {% endfor %}
  </div>
</section>
```

In `agent/templates/recovery_detail.html`, replace the raw face tag:
```html
{% extends "base.html" %}
{% block title %}{{ campaign.name }} — Roster{% endblock %}
{% block content %}
<a class="back-link" href="/clients/{{ client.id }}">&larr; {{ client.business_name }}</a>
<h1>{{ campaign.name }}</h1>
<span class="trade-tag">{{ face_display_name }}</span>

<div class="jobs-panel">
  <h2>Customers</h2>
  {% for j in jobs %}
  <div class="job-card">
    <div class="job-urgency status-{{ j.current_status }}">{{ j.current_status }}</div>
    <strong>{{ j.service_type }}</strong>
    {% if j.customer_name %}<div>{{ j.customer_name }}</div>{% endif %}
    <div>{{ j.customer_phone }}</div>
    {% if j.estimate_amount %}<div>Estimate: {{ j.estimate_amount }}</div>{% endif %}
    {% if j.anchor_date %}<div>Renews: {{ j.anchor_date }}</div>{% endif %}
  </div>
  {% endfor %}
  {% if not jobs %}<p class="empty">No customers in this campaign.</p>{% endif %}
</div>
{% endblock %}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_endpoint.py -v`
Expected: PASS (all tests in this file)

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `cd agent && .venv/bin/python -m pytest -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agent/app.py agent/templates/client_detail.html agent/templates/recovery_detail.html agent/tests/test_recovery_endpoint.py
git commit -m "Split agent-roster grid into named Chaser/Rebooker/Renewals tiles"
```

---

### Task 7: Reviews — review link setting

**Files:**
- Modify: `agent/app.py`
- Modify: `agent/templates/client_detail.html`
- Modify: `agent/static/styles.css`
- Test: `agent/tests/test_recovery_endpoint.py`

**Interfaces:**
- Produces route: `POST /clients/{client_id}/review-link`
- Consumes: `Client.review_link` from `db_models` (Task 1)

- [ ] **Step 1: Write the failing test**

Append to `agent/tests/test_recovery_endpoint.py`:
```python
from db_models import Job as Job


def test_set_review_link_saves_and_shows_on_client_detail(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)

    response = test_client.post(
        f"/clients/{client_id}/review-link",
        data={"review_link": "https://g.page/r/test-review-link"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    with Session(test_engine) as session:
        client = session.get(Client, client_id)
    assert client.review_link == "https://g.page/r/test-review-link"

    detail = test_client.get(f"/clients/{client_id}")
    assert "https://g.page/r/test-review-link" in detail.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_endpoint.py -v`
Expected: FAIL with 404 (route doesn't exist)

- [ ] **Step 3: Add the route, template tile, and CSS**

Add this route in `agent/app.py`, right after `create_client`:
```python
@app.post("/clients/{client_id}/review-link")
def set_review_link(client_id: int, review_link: str = Form(...)):
    with Session(engine) as session:
        client = session.get(Client, client_id)
        client.review_link = review_link.strip() or None
        session.add(client)
        session.commit()
    return RedirectResponse(f"/clients/{client_id}", status_code=303)
```

In `agent/templates/client_detail.html`, add a Reviews tile as the last child of `.agent-roster-grid`, right after the `{% endfor %}` that closes the three named-face tiles and before the closing `</div>` of `.agent-roster-grid`:
```html
    {% endfor %}

    <div class="agent-tile">
      <div class="agent-tile-head">
        <span class="agent-badge {{ 'agent-badge-active' if client.review_link else 'agent-badge-off' }}">
          Reviews{% if client.review_link %} &middot; on{% endif %}
        </span>
      </div>
      <p class="agent-tile-desc">Texts a review link the moment a job's marked done.</p>
      <form method="post" action="/clients/{{ client.id }}/review-link" class="review-link-form">
        <input type="url" name="review_link" placeholder="https://g.page/r/..." value="{{ client.review_link or '' }}">
        <button class="btn btn-secondary btn-small" type="submit">Save</button>
      </form>
    </div>
  </div>
</section>
```

Append to `agent/static/styles.css`:
```css
.btn-secondary { background: transparent; color: var(--text); border: 1px solid var(--border); }
.btn-secondary:hover { border-color: var(--accent); color: var(--accent); }

.review-link-form { display: flex; gap: 8px; margin-top: 10px; }
.review-link-form input {
  flex: 1;
  font-size: 13px;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  font-family: inherit;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_endpoint.py -v`
Expected: PASS (all tests in this file)

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `cd agent && .venv/bin/python -m pytest -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agent/app.py agent/templates/client_detail.html agent/static/styles.css agent/tests/test_recovery_endpoint.py
git commit -m "Add review-link setting to the Reviews feature tile"
```

---

### Task 8: Reviews — mark job done + review SMS

**Files:**
- Modify: `agent/app.py`
- Modify: `agent/templates/client_detail.html`
- Test: `agent/tests/test_recovery_endpoint.py`

**Interfaces:**
- Produces route: `POST /clients/{client_id}/jobs/{job_id}/complete`
- Consumes: `Job.completed_at`, `Client.review_link` from `db_models` (Task 1)
- Consumes: module-level `sms_channel` already defined in `agent/app.py`

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_recovery_endpoint.py`:
```python
class FakeSMS:
    def __init__(self):
        self.sent = []

    def send(self, from_number, to_number, body):
        self.sent.append({"from": from_number, "to": to_number, "body": body})


class ExplodingSMS:
    def send(self, from_number, to_number, body):
        raise RuntimeError("simulated Twilio failure")


def test_mark_job_done_sends_review_sms_when_link_set(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)
    test_client.post(f"/clients/{client_id}/review-link", data={"review_link": "https://g.page/r/test"})

    with Session(test_engine) as session:
        job = Job(client_id=client_id, service_type="AC repair", urgency="routine", callback_number="+15551234567")
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    fake = FakeSMS()
    monkeypatch.setattr(app_module, "sms_channel", fake)

    response = test_client.post(f"/clients/{client_id}/jobs/{job_id}/complete", follow_redirects=False)

    assert response.status_code == 303
    assert len(fake.sent) == 1
    assert "https://g.page/r/test" in fake.sent[0]["body"]
    with Session(test_engine) as session:
        completed = session.get(Job, job_id)
    assert completed.completed_at is not None


def test_mark_job_done_without_review_link_sends_nothing(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)

    with Session(test_engine) as session:
        job = Job(client_id=client_id, service_type="AC repair", urgency="routine", callback_number="+15551234567")
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    fake = FakeSMS()
    monkeypatch.setattr(app_module, "sms_channel", fake)

    response = test_client.post(f"/clients/{client_id}/jobs/{job_id}/complete", follow_redirects=False)

    assert response.status_code == 303
    assert fake.sent == []
    with Session(test_engine) as session:
        completed = session.get(Job, job_id)
    assert completed.completed_at is not None


def test_mark_job_done_still_completes_when_sms_send_fails(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)
    test_client.post(f"/clients/{client_id}/review-link", data={"review_link": "https://g.page/r/test"})

    with Session(test_engine) as session:
        job = Job(client_id=client_id, service_type="AC repair", urgency="routine", callback_number="+15551234567")
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    monkeypatch.setattr(app_module, "sms_channel", ExplodingSMS())

    response = test_client.post(f"/clients/{client_id}/jobs/{job_id}/complete", follow_redirects=False)

    assert response.status_code == 303
    with Session(test_engine) as session:
        completed = session.get(Job, job_id)
    assert completed.completed_at is not None


def test_client_detail_shows_mark_done_then_completed_badge(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)

    with Session(test_engine) as session:
        job = Job(client_id=client_id, service_type="AC repair", urgency="routine", callback_number="+1")
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    before = test_client.get(f"/clients/{client_id}")
    assert "Mark done" in before.text

    monkeypatch.setattr(app_module, "sms_channel", FakeSMS())
    test_client.post(f"/clients/{client_id}/jobs/{job_id}/complete")

    after = test_client.get(f"/clients/{client_id}")
    assert "Completed" in after.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_endpoint.py -v`
Expected: FAIL with 404 (route doesn't exist); the "Mark done"/"Completed" assertions also fail since the job card has no such button yet

- [ ] **Step 3: Add the route and update the job card**

Add this route in `agent/app.py`, right after `set_review_link`:
```python
@app.post("/clients/{client_id}/jobs/{job_id}/complete")
def complete_job(client_id: int, job_id: int):
    """Marks a job done and, best-effort, texts a review-request link. The SMS
    send never blocks completion — a failed or skipped send still marks the job
    done, since the review text is a bonus, not the point of this action."""
    with Session(engine) as session:
        client = session.get(Client, client_id)
        job = session.get(Job, job_id)
        job.completed_at = datetime.utcnow()
        session.add(job)
        session.commit()
        if client.review_link and job.callback_number:
            try:
                sms_channel.send(
                    from_number=client.inbound_number or "",
                    to_number=job.callback_number,
                    body=f"Thanks for choosing {client.business_name}! If we did right by you, a quick review means a lot: {client.review_link}",
                )
            except Exception as e:
                print(f"Reviews: failed to send review request for job {job_id}: {e}")
    return RedirectResponse(f"/clients/{client_id}", status_code=303)
```

In `agent/templates/client_detail.html`, replace the `<aside class="jobs-panel">` job-card loop:
```html
  <aside class="jobs-panel">
    <h2>Captured jobs</h2>
    {% for j in jobs %}
    <div class="job-card">
      <div class="job-urgency {{ j.urgency }}">{{ j.urgency }}</div>
      <strong>{{ j.service_type }}</strong>
      {% if j.customer_name %}<div>{{ j.customer_name }}</div>{% endif %}
      {% if j.address %}<div>{{ j.address }}</div>{% endif %}
      {% if j.callback_number %}<div>{{ j.callback_number }}</div>{% endif %}
      {% if j.notes %}<p class="job-notes">{{ j.notes }}</p>{% endif %}
      {% if j.completed_at %}
      <span class="agent-badge agent-badge-active">Completed</span>
      {% else %}
      <form method="post" action="/clients/{{ client.id }}/jobs/{{ j.id }}/complete">
        <button class="btn btn-secondary btn-small" type="submit">Mark done</button>
      </form>
      {% endif %}
    </div>
    {% endfor %}
    {% if not jobs %}<p class="empty">No jobs captured yet.</p>{% endif %}
  </aside>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && .venv/bin/python -m pytest tests/test_recovery_endpoint.py -v`
Expected: PASS (all tests in this file)

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `cd agent && .venv/bin/python -m pytest -v`
Expected: PASS — every test in the whole suite green

- [ ] **Step 6: Commit**

```bash
git add agent/app.py agent/templates/client_detail.html agent/tests/test_recovery_endpoint.py
git commit -m "Add mark-done action that fires the Reviews SMS"
```

---

### Task 9: Landing page — promoted trio + Renewals/Reviews live on the roster

**Files:**
- Modify: `index.html`

No backend/pytest coverage exists for the static landing page (confirmed: it's plain HTML/CSS served outside the FastAPI test suite). Verify each step with the `grep` command shown instead of running pytest.

- [ ] **Step 1: Check the current state before editing**

Run: `grep -c "Chaser\|Rebooker\|Renewals" /Users/nandanreddyavanaganti/new_idea/index.html`
Expected: `0` (none of these names exist on the page yet)

- [ ] **Step 2: Split the "Recovery" product section into separate Chaser and Rebooker sections**

In `index.html`, replace the single `<section class="product product-alt">...Recovery...</section>` block with two sections — Chaser keeps the existing phone-mock example, Rebooker is new:
```html
<section class="product product-alt">
  <div class="section-inner product-grid product-grid-reverse">
    <div class="product-copy">
      <span class="product-tag">Chaser · The moneymaker</span>
      <h3>The quote that went cold isn't dead. Nobody's working it.</h3>
      <p>
        80% of deals take 8–12 touches to close. Most shops stop at one, because
        you're on a roof, not at a desk. Chaser runs a quiet 6-touch sequence
        over ~4 weeks on every open quote — the kind of follow-up that takes
        close rates from 25–35% to 45–55%. Every thread ends one of two ways:
        a booked job, or a clean opt-out.
      </p>
      <ul class="product-list">
        <li>Chases every quote you've sent but never closed</li>
        <li>8–12 touches, not the one-and-done most shops manage</li>
        <li>This is the role nobody else in the trade stack owns</li>
      </ul>
    </div>
    <div class="product-visual" aria-hidden="true">
      <div class="phone-mock">
        <div class="msg msg-in">Hey Sarah, following up on the AC quote from a few weeks back — still want to get that scheduled?</div>
        <div class="msg msg-out">Oh yes! Totally forgot, sorry</div>
        <div class="msg msg-in">No worries at all — I can get you in Thursday morning, does that work?</div>
        <div class="msg msg-out">Perfect, thank you!</div>
        <div class="msg msg-status">Job booked · Thursday 9:00 AM</div>
      </div>
    </div>
  </div>
</section>

<section class="product">
  <div class="section-inner product-grid">
    <div class="product-copy">
      <span class="product-tag">Rebooker · Wakes up your list</span>
      <h3>Your customer list isn't a graveyard. It's unworked pipeline.</h3>
      <p>
        Dormant customers convert at 15–30% when you reach back out — versus
        3–5% for a cold lead you paid to generate. Rebooker runs the same
        6-touch sequence on every customer who's gone quiet, so the list you
        already paid to build keeps paying you back instead of sitting there.
      </p>
      <ul class="product-list">
        <li>Wakes up past customers who've quietly drifted away</li>
        <li>10–20x ROI is typical for a dormant-list campaign</li>
        <li>Same clean opt-out as every other agent on the roster</li>
      </ul>
    </div>
    <div class="product-visual" aria-hidden="true">
      <div class="phone-mock">
        <div class="msg msg-in">Hi Dave, it's been a while since your last furnace tune-up. Time for a check-up! Want to book?</div>
        <div class="msg msg-out">yeah probably overdue lol</div>
        <div class="msg msg-in">No judgment! I can get you in this Friday at 10am — sound good?</div>
        <div class="msg msg-out">works for me</div>
        <div class="msg msg-status">Job booked · Friday 10:00 AM</div>
      </div>
    </div>
  </div>
</section>
```

- [ ] **Step 3: Promote Renewals and Reviews to "live" in the roster-soon strip**

Replace the `<section class="roster-soon">...</section>` block:
```html
<section class="roster-soon">
  <div class="section-inner">
    <p class="roster-soon-label">Also on the roster</p>
    <div class="trades-grid">
      <div class="trade-card available">
        <div class="trade-badge">Live now</div>
        <h3>Renewals</h3>
        <p>Chases membership renewals on each customer's own renewal date — automated reminders recover 20%+ of memberships that would otherwise lapse.</p>
      </div>
      <div class="trade-card available">
        <div class="trade-badge">Live now</div>
        <h3>Reviews</h3>
        <p>Texts a review link the moment a job's marked done — the window where customers actually leave one. A feature every agent gets, not a seat you buy.</p>
      </div>
      <div class="trade-card soon">
        <div class="trade-badge">Next hire</div>
        <h3>Analytics</h3>
        <p>One view of what every agent on the roster answered, chased, and booked.</p>
      </div>
    </div>
  </div>
</section>
```

- [ ] **Step 4: Update the meta description for consistency**

Replace the `<meta name="description">` line in `<head>`:
```html
<meta name="description" content="Roster builds and runs an AI workforce for home service businesses. Frontdesk answers every missed call. Chaser follows up every quote. Rebooker wakes up dormant customers. Hired one role at a time, run for you.">
```

- [ ] **Step 5: Verify the edit**

Run: `grep -c "Chaser\|Rebooker\|Renewals" /Users/nandanreddyavanaganti/new_idea/index.html`
Expected: `6` or more (product tags, roster-soon card, meta description all now reference the named crew)

- [ ] **Step 6: Commit**

```bash
git add index.html
git commit -m "Split Recovery into named Chaser/Rebooker sections; promote Renewals/Reviews to live"
```

---

### Task 10: ROSTER.md + README documentation

**Files:**
- Modify: `ROSTER.md`
- Modify: `agent/README.md`

- [ ] **Step 1: Update the role sequence in `ROSTER.md`**

Replace the `## Role sequence` section:
```markdown
## Role sequence (each only after the previous is rock-solid; each reuses the same data)
1. **Frontdesk** — calls → bookings. _(Built.)_
2. **Chaser** (quote follow-up) — chases every unsold estimate. _(Built.)_
3. **Rebooker** (reactivation) — wakes up dormant customers. _(Built.)_
4. **Renewals** (membership) — chases plan renewals on each customer's own date, before
   they lapse. _(Built.)_ Chaser/Rebooker/Renewals are three faces of one engine, sold
   as three named agents.
5. **Reviews** — texts a review link the moment a job's marked done. _(Built.)_ A
   feature every agent gets, not a separate seat — deliberately unnamed like the agents
   above, since review automation is already commoditized by incumbents.
6. **Workflow plumbing** — get the booked job into their real calendar/CRM. _Not a new
   role — it's what makes every agent above finish the job._ Built from what real
   clients use, never guessed.
7. **Marketing / content** — once there's a customer base and real before/after numbers.
8. **Lead-gen, reframed for trades** — Angi/Google capture, referral nudges (NOT
   LinkedIn scraping — that's a B2B-SaaS tactic, wrong for trades).
9. **Analytics** — last, because it's the exhaust of everything above.
```

- [ ] **Step 2: Add a Roster naming + Renewals/Reviews section to `agent/README.md`**

Insert into `agent/README.md`, right after the existing `## Revenue Recovery (quote follow-up + reactivation)` section (after its `### Scope (Phase 1)` block, before `## Pieces`):
```markdown
## Named crew and the third face (Renewals)

Revenue Recovery's two faces are sold under separate names — **Chaser** (`face ==
"quote"`) and **Rebooker** (`face == "reactivation"`) — via `FACE_DISPLAY_NAMES` in
`recovery_engine.py`. This is a display-layer mapping only; the underlying `face`
column, engine, and code all still say "quote"/"reactivation" internally.

A third face, **Renewals** (`face == "membership"`), chases membership/maintenance-plan
renewals on each customer's own renewal date instead of days since the campaign
started. Paste customers as `phone,name,service_type,renewal_date` (strict
`YYYY-MM-DD` — rejected with a clear error otherwise) and the sequence fires at
30/14/7 days before the renewal, on the day itself, and 7 days after if there's been
no reply. Everything else — replies, slot booking, STOP handling — is identical to
Chaser/Rebooker; only the timing clock differs (see `MEMBERSHIP_OFFSETS` in
`recovery_engine.py`).

## Reviews (feature, not an agent)

Set a client's review link via the "Reviews" tile on their dashboard page. Once set,
clicking **Mark done** on any captured job texts that customer a one-line review
request. No sequence, no Claude — deliberately the smallest possible implementation,
since review requests are already a commodity feature on every competing platform.
```

Update the `## Pieces` table — no new files were created by this plan, so no row additions are needed. Confirm the existing table still matches reality by re-reading it, but do not edit it if unchanged.

- [ ] **Step 3: Commit**

```bash
git add ROSTER.md agent/README.md
git commit -m "Document named crew (Chaser/Rebooker/Renewals) and Reviews feature"
```

---

## Manual smoke test (after all tasks)

1. `cd agent && ./run.sh`
2. Open a client, confirm the agent-roster grid shows 5 tiles: Frontdesk, Chaser, Rebooker, Renewals, Reviews.
3. Click **+ New campaign** on the Renewals tile — confirm the form opens with "Renewals — chase membership renewals" preselected.
4. Create a Renewals campaign with your own phone number and a renewal date ~25 days out: `+1XXXXXXXXXX,YourName,AC tune-up,2026-08-01` (adjust the date).
5. Run `.venv/bin/python recovery_tick.py` manually — confirm no message sends yet if you're more than 30 days out, or confirm the appropriate offset message sends if within 30 days.
6. On the Reviews tile, save a review link, then click **Mark done** on any captured job with a callback number — confirm you receive (or see printed to console) a review-request text containing that link, and the job now shows a "Completed" badge instead of the button.
7. Open `index.html` in a browser — confirm the "Meet the roster" section now shows three distinct product blocks (Frontdesk, Chaser, Rebooker) and the "Also on the roster" strip shows Renewals and Reviews marked "Live now."
