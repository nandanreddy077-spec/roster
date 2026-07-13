# Roster Platform — Foundation (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-home the existing Roster app on the locked platform spine — Business aggregate root, first-class Customer, declarative-employee data model, and a synchronous EventBus — with zero regression, split into **two independently deployable checkpoints** so a regression has exactly one class of change to blame.

**Architecture:** FastAPI monolith, SQLite (WAL) behind a repository layer, ports for external systems, employees as data + a generic event-driven runner (the runner itself lands in Phase 3). Phase 1 builds the data model and the event seam only.

**Tech Stack:** Python 3.13, FastAPI, SQLModel/SQLAlchemy, SQLite, pytest, Anthropic SDK, Twilio, Authlib.

**Spec of record:** `docs/superpowers/specs/2026-07-13-roster-platform-architecture-prd.md`. Every task inherits its constraints.

## Global Constraints

- **Constitutional principles (never violate):** Business is aggregate root; Customer first-class; employees are declarative RoleDefinitions run by a generic Runner; synchronous in-proc EventBus is the seam; external systems via ports; "AI staffing" is external-only wording — internal code says business/operations; Property/Equipment reserved but NOT implemented.
- **Two-checkpoint rule (this plan's spine):** the plan is split into **Foundation A** and **Foundation B**. Each must leave the application **fully working, all tests green, and independently deployable**. Do not begin Foundation B until Foundation A is committed and green. This isolates risk: a regression after A is a data-model/rename problem; a regression after B is an event-seam problem.
- **Zero regression:** existing SMS webhook, xAI voice path, dashboard test chat, trial cap, and honest status ladder behave identically after each Foundation. The full pytest suite (baseline: **194 passing**) stays green at every commit.
- **Define ≠ implement:** ports get a stable interface + the thinnest body; no vector DB, no Postgres, no async/durable bus, no real integrations, no eval framework.
- **DB engine:** SQLite only, via `db.engine`; WAL stays on; migrations are idempotent and run in `init_db()`.
- **Naming:** after Foundation A, the aggregate-root FK is `business_id` **everywhere** (new and legacy tables). No `client_id` remains.
- **Deferred rename:** the `message` → `interaction` rename (+ `channel`/`direction`/`handled_by_employee_id`) is **Phase 2**, not the foundation. Foundation keeps the `Message` model, adding only `customer_id`.
- **Test-first:** every task writes the failing test before the implementation.
- **Commit cadence:** one commit per task; `feat:`/`refactor:`/`fix:` + scope. All existing tests pass before moving to the next task.
- **Model IDs:** do not touch model selection in Phase 1 (stale `claude-sonnet-4-6` in `engine.py` is debt, fixed in Phase 3 via the claude-api reference).
- **Run tests with:** `cd /Users/nandanreddyavanaganti/new_idea/agent && .venv/bin/python -m pytest`.

---

## Test Isolation & Engine DI (overrides the literal `Session(engine)`/`init_db()` shown in every task below)

The existing suite isolates via conftest's in-memory `test_engine`/`session` fixtures (`agent/conftest.py`). New tests MUST do the same — **never touch the real `db.engine`/`roster.db`**, or the suite becomes non-idempotent and pollutes the dev DB (this bit Task 2; the fix is commit `e219feb`). These rules are binding and supersede any task code below that shows `Session(engine)`, `init_db()`, or a DB component constructed without an engine:

1. **Tests that persist or read rows** take the `session` fixture (a `Session` on the in-memory `test_engine`) and use it directly. Do NOT call `init_db()` and do NOT `from db import engine` in a test. `test_engine` already ran `SQLModel.metadata.create_all`, so every table — including the new ones — exists.
2. **Tests that only inspect a model/dataclass** (columns, constants, dataclass defaults) need no DB and no fixture.
3. **Components that open their own DB sessions — `EventBus` (Task 9), `BusinessMemory` (Task 10) — take an `engine=None` constructor param defaulting to the real engine**, so tests inject `test_engine`:
   ```python
   class EventBus:
       def __init__(self, engine=None):
           from db import engine as _default
           self._engine = engine or _default
           self._subscribers = defaultdict(list)
       def publish(self, event):
           with Session(self._engine) as s: ...
   bus = EventBus()  # module singleton uses the real engine
   ```
   `BusinessMemory(business_id, engine=None)` likewise stores `self._engine = engine or _default` and every method does `with Session(self._engine)`. Tests: `EventBus(test_engine)`, `BusinessMemory(b.id, test_engine)`.
4. **Backfill helpers — `_backfill_customers` (Task 5), `_backfill_employees` (Task 6) — take an `engine=None` param** defaulting to the module engine: `def _backfill_customers(engine=None): eng = engine if engine is not None else globals()["engine"]; ...`. `init_db()` calls them with no arg; tests pass `test_engine`.
5. **Event-wiring test (Task 11):** the singleton `bus` uses the real engine, so in the test `monkeypatch.setattr(eventbus.bus, "_engine", test_engine)` and pass the same `session` (on `test_engine`) into `handle_customer_message` — job/message writes and the published event then all land in the one in-memory DB (StaticPool shares the connection).

---

## File Structure

**Foundation A — modify:**
- `agent/app.py` — session-secret fail-closed helper.
- `agent/db_models.py` — `Client`→`Business`; FK attrs `client_id`→`business_id` on `Job`/`Message`/recovery/referral; add `Customer`; add `customer_id` to `Job`/`Message`.
- `agent/db.py` — rename-table migration, column renames, add-column migrations, `_backfill_customers()`.
- `agent/repositories.py` *(new)* — `get_or_create_customer`.
- `agent/portal.py`, `agent/service.py`, `agent/activation.py`, `agent/seed.py`, `agent/recovery_service.py`, `agent/referral_service.py` — reference `Business`/`business_id`.
- Tests: `test_session_secret.py`, `test_business_rename.py`, `test_customer.py`, `test_customer_id_columns.py`, `test_backfill_customers.py`.

**Foundation B — create:**
- `agent/events.py`, `agent/eventbus.py`, `agent/memory.py`.
- `agent/db_models.py` — add `Employee`, `Event`.
- `agent/db.py` — `_backfill_employees()`, event-table creation.
- Tests: `test_employee_model.py`, `test_event_model.py`, `test_events.py`, `test_eventbus.py`, `test_memory.py`, `test_event_wiring.py`.

**Do NOT touch in Phase 1:** `roles/` runner, RoleDefinition capabilities, Channel/LLM/Integration bodies beyond what exists, the `message`→`interaction` rename. Those are Phases 2–3.

---

# FOUNDATION A — Business + Customer

*End state: aggregate root is `Business`, homeowner is a first-class `Customer` linked to every Job and Message, app fully working. Deployable.*

### Task 1: Production fail-closed on `SESSION_SECRET_KEY`

**Files:** Modify `agent/app.py:39-43`; Test `agent/tests/test_session_secret.py`.
**Interfaces:** Produces `resolve_session_secret(environ) -> str` (raises `RuntimeError` in prod when unset).

- [ ] **Step 1: Write the failing test**

```python
# agent/tests/test_session_secret.py
import pytest
from app import resolve_session_secret

def test_prod_requires_secret():
    with pytest.raises(RuntimeError):
        resolve_session_secret({"ROSTER_ENV": "production"})

def test_dev_allows_fallback():
    assert resolve_session_secret({"ROSTER_ENV": "dev"})

def test_explicit_secret_always_wins():
    assert resolve_session_secret({"ROSTER_ENV": "production", "SESSION_SECRET_KEY": "abc"}) == "abc"
```

- [ ] **Step 2: Run test to verify it fails** — `.venv/bin/python -m pytest tests/test_session_secret.py -v` → FAIL (`ImportError`).

- [ ] **Step 3: Implement**

```python
# agent/app.py — add near top; replace the inline secret_key= line
def resolve_session_secret(environ) -> str:
    secret = environ.get("SESSION_SECRET_KEY")
    if secret:
        return secret
    if environ.get("ROSTER_ENV") == "production":
        raise RuntimeError(
            "SESSION_SECRET_KEY must be set in production — refusing to start with the dev fallback."
        )
    return "dev-only-insecure-secret-change-in-production"

app.add_middleware(SessionMiddleware, secret_key=resolve_session_secret(os.environ))
```

- [ ] **Step 4: Run** — `.venv/bin/python -m pytest tests/test_session_secret.py -v` → PASS (3).

- [ ] **Step 5: Commit** — `git add agent/app.py agent/tests/test_session_secret.py && git commit -m "fix: fail closed when SESSION_SECRET_KEY unset in production"`

---

### Task 2: Rename `Client` → `Business` (aggregate root, including FK columns)

One deliverable: the complete rename. The gate is the **entire** suite green.

**Files:** Modify `agent/db_models.py`, `agent/db.py`, and every non-venv `.py` importing `Client` or using `client_id`; Test `agent/tests/test_business_rename.py`.
**Interfaces:** Produces `db_models.Business` (table `business`); FK attribute `business_id` on `Job`/`Message`/`RecoveryCampaign`/`RecoveryJob`/`RecoveryMessageLog`(via job)/`ReferralLead`. `Business.to_config()` unchanged.

- [ ] **Step 1: Write the failing test**

```python
# agent/tests/test_business_rename.py
from sqlmodel import Session, select
from db import engine, init_db
from db_models import Business, Job

def test_business_persists():
    init_db()
    with Session(engine) as s:
        b = Business(business_name="Test Plumbing", trade="plumbing", email="rename@test.io")
        s.add(b); s.commit(); s.refresh(b)
        assert s.exec(select(Business).where(Business.email == "rename@test.io")).first().business_name == "Test Plumbing"

def test_no_client_symbol_remains():
    import db_models
    assert not hasattr(db_models, "Client")

def test_job_uses_business_id():
    assert "business_id" in Job.__table__.columns.keys()
    assert "client_id" not in Job.__table__.columns.keys()
```

- [ ] **Step 2: Run** — `.venv/bin/python -m pytest tests/test_business_rename.py -v` → FAIL.

- [ ] **Step 3: Rename in `db_models.py`**

Rename `class Client(SQLModel, table=True)` → `class Business`. On every child table replace `client_id: int = Field(foreign_key="client.id")` → `business_id: int = Field(foreign_key="business.id")`. (`RecoveryMessageLog` references `recoveryjob.id`, unaffected.)

- [ ] **Step 4: Update all references**

```bash
cd /Users/nandanreddyavanaganti/new_idea
grep -rl "Client\|client_id" agent --include=*.py | grep -v .venv | grep -v __pycache__
```
In each hit: import `Business` instead of `Client`; replace `client_id` attribute reads/writes with `business_id`. **Leave `Client as TwilioRest` in `channels.py` untouched** (different symbol). Local variables named `client` may remain (they hold a Business) — cosmetic; not required. `_current_client` keeps its name.

- [ ] **Step 5: Migrations in `db.py`**

Add, guarded/idempotent, BEFORE the existing add-column block; also change the existing `ALTER TABLE client ADD COLUMN ...` statements to `business`:

```python
from sqlalchemy import inspect, text
insp = inspect(engine)
tables = set(insp.get_table_names())
with engine.connect() as conn:
    if "client" in tables and "business" not in tables:
        conn.execute(text("ALTER TABLE client RENAME TO business")); conn.commit()
    for tbl in ("job", "message", "recoverycampaign", "recoveryjob", "referrallead"):
        cols = {c["name"] for c in inspect(engine).get_columns(tbl)} if tbl in inspect(engine).get_table_names() else set()
        if "client_id" in cols and "business_id" not in cols:
            try:
                conn.execute(text(f"ALTER TABLE {tbl} RENAME COLUMN client_id TO business_id")); conn.commit()
            except Exception:
                conn.rollback()
```
Update every `"ALTER TABLE client ADD COLUMN ..."` string in `_migrate_add_columns` to `"ALTER TABLE business ADD COLUMN ..."`.

- [ ] **Step 6: Run the full suite** — `.venv/bin/python -m pytest -q` → all green (fix any test importing `Client`/using `client_id`).

- [ ] **Step 7: Commit** — `git add -A && git commit -m "refactor: rename Client aggregate root to Business (incl. business_id FK columns)"`

---

### Task 3: First-class `Customer` model + repository

**Files:** Modify `agent/db_models.py`; Create `agent/repositories.py`; Test `agent/tests/test_customer.py`.
**Interfaces:** Produces `db_models.Customer` (`id, business_id, phone, name, source, tags_json="[]", first_seen_at, created_at, updated_at`; unique `(business_id, phone)`; `.tags` property) and `repositories.get_or_create_customer(session, business_id, phone, name=None) -> Customer`.

- [ ] **Step 1: Write the failing test**

```python
# agent/tests/test_customer.py
from sqlmodel import Session
from db import engine, init_db
from db_models import Business, Customer
from repositories import get_or_create_customer

def _biz(s):
    b = Business(business_name="B", trade="hvac", email=f"c{id(s)}@test.io"); s.add(b); s.commit(); s.refresh(b); return b

def test_get_or_create_idempotent():
    init_db()
    with Session(engine) as s:
        b = _biz(s)
        c1 = get_or_create_customer(s, b.id, "+15551234567", name="Pat")
        c2 = get_or_create_customer(s, b.id, "+15551234567")
        assert c1.id == c2.id and c1.name == "Pat"

def test_same_phone_distinct_per_business():
    init_db()
    with Session(engine) as s:
        b1, b2 = _biz(s), _biz(s)
        assert get_or_create_customer(s, b1.id, "+15550000000").id != get_or_create_customer(s, b2.id, "+15550000000").id
```

- [ ] **Step 2: Run** → FAIL (`ImportError`).

- [ ] **Step 3: Add the model**

```python
# agent/db_models.py
class Customer(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    business_id: int = Field(foreign_key="business.id", index=True)
    phone: str = Field(index=True)
    name: Optional[str] = None
    source: Optional[str] = None
    tags_json: str = "[]"
    first_seen_at: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def tags(self) -> List[str]:
        return json.loads(self.tags_json)
```

- [ ] **Step 4: Add the repository**

```python
# agent/repositories.py
from datetime import datetime
from typing import Optional
from sqlmodel import Session, select
from db_models import Customer

def get_or_create_customer(session: Session, business_id: int, phone: str, name: Optional[str] = None) -> Customer:
    existing = session.exec(
        select(Customer).where(Customer.business_id == business_id, Customer.phone == phone)
    ).first()
    if existing:
        if name and not existing.name:
            existing.name = name; existing.updated_at = datetime.utcnow()
            session.add(existing); session.commit(); session.refresh(existing)
        return existing
    c = Customer(business_id=business_id, phone=phone, name=name)
    session.add(c); session.commit(); session.refresh(c)
    return c
```

- [ ] **Step 5: Run** → PASS (2).
- [ ] **Step 6: Commit** — `git add agent/db_models.py agent/repositories.py agent/tests/test_customer.py && git commit -m "feat: first-class Customer model + get_or_create repository"`

---

### Task 4: Add `customer_id` to `Job` and `Message`

**Files:** Modify `agent/db_models.py`, `agent/db.py`; Test `agent/tests/test_customer_id_columns.py`.
**Interfaces:** `Job` and `Message` each gain `customer_id: Optional[int] = Field(default=None, foreign_key="customer.id", index=True)`. `customer_phone` retained on both.

- [ ] **Step 1: Write the failing test**

```python
# agent/tests/test_customer_id_columns.py
from db_models import Job, Message
def test_job_and_message_have_customer_id():
    assert "customer_id" in Job.__table__.columns.keys()
    assert "customer_id" in Message.__table__.columns.keys()
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** — add the `customer_id` field to `Job` and `Message` in `db_models.py`; in `db.py` add to the add-column block: `"ALTER TABLE job ADD COLUMN customer_id INTEGER"`, `"ALTER TABLE message ADD COLUMN customer_id INTEGER"` (existing try/except tolerance covers re-runs).

- [ ] **Step 4: Run** → PASS. Then full suite `.venv/bin/python -m pytest -q` → green.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: add customer_id FK to Job and Message"`

---

### Task 5: Backfill Customers from existing phones; link Jobs and Messages

**Files:** Modify `agent/db.py`; Test `agent/tests/test_backfill_customers.py`.
**Interfaces:** Produces `db._backfill_customers()` — idempotent; called at the end of `init_db()`.

- [ ] **Step 1: Write the failing test**

```python
# agent/tests/test_backfill_customers.py
from sqlmodel import Session, select
from db import engine, init_db, _backfill_customers
from db_models import Business, Customer, Job

def test_backfill_creates_and_links():
    init_db()
    with Session(engine) as s:
        b = Business(business_name="B", trade="hvac", email="mig@test.io"); s.add(b); s.commit(); s.refresh(b)
        j = Job(business_id=b.id, customer_phone="+15557778888", service_type="AC", urgency="routine")
        s.add(j); s.commit(); s.refresh(j)
        assert j.customer_id is None
    _backfill_customers()
    with Session(engine) as s:
        cust = s.exec(select(Customer).where(Customer.phone == "+15557778888")).first()
        assert cust is not None
        assert s.exec(select(Job).where(Job.customer_phone == "+15557778888")).first().customer_id == cust.id
```

- [ ] **Step 2: Run** → FAIL (`ImportError`).

- [ ] **Step 3: Implement**

```python
# agent/db.py
def _backfill_customers():
    from sqlmodel import Session, select
    from db_models import Customer, Job, Message
    with Session(engine) as s:
        for model in (Job, Message):
            for r in s.exec(select(model)).all():
                phone = getattr(r, "customer_phone", None)
                if not phone or r.customer_id is not None:
                    continue
                existing = s.exec(
                    select(Customer).where(Customer.business_id == r.business_id, Customer.phone == phone)
                ).first()
                if not existing:
                    existing = Customer(business_id=r.business_id, phone=phone)
                    s.add(existing); s.commit(); s.refresh(existing)
                r.customer_id = existing.id; s.add(r)
        s.commit()
```
Call `_backfill_customers()` at the end of `init_db()` (after `_migrate_add_columns()`). Synthetic `dashboard`/`portal-test` phones become harmless Customer rows — keeps the test chat working.

- [ ] **Step 4: Run** → PASS. Full suite → green.
- [ ] **Step 5: Commit** — `git add agent/db.py agent/tests/test_backfill_customers.py && git commit -m "feat: backfill Customers from phones, link Jobs and Messages"`

---

## ✅ FOUNDATION A CHECKPOINT

- [ ] Full suite green: `cd /Users/nandanreddyavanaganti/new_idea/agent && .venv/bin/python -m pytest -q` → **all pass**.
- [ ] Manual sanity: app boots, `init_db()` runs the rename + backfill idempotently on a copy of the real `roster.db`.
- [ ] **STOP. This is independently deployable.** Business is the aggregate root; Customer is first-class and linked. No event seam yet — nothing depends on one. Deploy or pause here safely before starting Foundation B.

---

# FOUNDATION B — Employee, Event, EventBus, Memory

*End state: declarative-employee data model + a live, persisted, idempotent event seam + the Memory port. App still fully working. Deployable.*

### Task 6: `Employee` model + backfill from `requested_roster`

**Files:** Modify `agent/db_models.py`, `agent/db.py`, `agent/portal.py` (roster read/hire writes Employee rows); Test `agent/tests/test_employee_model.py`.
**Interfaces:** Produces `db_models.Employee` (`id, business_id, role_key, display_name, status="active", policy_json="{}", hired_at, fired_at`) and `db._backfill_employees()`.

- [ ] **Step 1: Write the failing test**

```python
# agent/tests/test_employee_model.py
from sqlmodel import Session, select
from db import engine, init_db, _backfill_employees
from db_models import Business, Employee

def test_live_business_gets_active_frontdesk():
    init_db()
    with Session(engine) as s:
        b = Business(business_name="B", trade="hvac", email="emp@test.io", frontdesk_live=True)
        s.add(b); s.commit(); s.refresh(b)
    _backfill_employees()
    with Session(engine) as s:
        b = s.exec(select(Business).where(Business.email == "emp@test.io")).first()
        emps = s.exec(select(Employee).where(Employee.business_id == b.id)).all()
        assert any(e.role_key == "frontdesk" and e.status == "active" for e in emps)
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Add model + backfill**

```python
# agent/db_models.py
class Employee(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    business_id: int = Field(foreign_key="business.id", index=True)
    role_key: str
    display_name: str = ""
    status: str = "active"  # active | paused | fired
    policy_json: str = "{}"
    hired_at: datetime = Field(default_factory=datetime.utcnow)
    fired_at: Optional[datetime] = None
```
```python
# agent/db.py
def _backfill_employees():
    import json as _json
    from sqlmodel import Session, select
    from db_models import Business, Employee
    with Session(engine) as s:
        for b in s.exec(select(Business)).all():
            have = {e.role_key for e in s.exec(select(Employee).where(Employee.business_id == b.id)).all()}
            if b.frontdesk_live and "frontdesk" not in have:
                s.add(Employee(business_id=b.id, role_key="frontdesk", display_name="Receptionist"))
            requested = getattr(b, "requested_roster", None)
            if requested:
                for role in _json.loads(requested):
                    key = role.strip().lower().replace(" ", "_")
                    if key and key not in have:
                        s.add(Employee(business_id=b.id, role_key=key, display_name=role))
        s.commit()
```
Call from `init_db()` after `_backfill_customers()`. Keep the `requested_roster` **column** (SQLite column-drop needs a rebuild — debt); stop *writing* it — `/roster/hire` now inserts `Employee` rows, dashboard roster reads `Employee`.

- [ ] **Step 4: Run** → PASS; update `/roster/hire` + dashboard roster read; full suite → green.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: Employee model + backfill; hire writes Employee rows"`

---

### Task 7: `Event` model (append-only)

**Files:** Modify `agent/db_models.py`; Test `agent/tests/test_event_model.py`.
**Interfaces:** Produces `db_models.Event` (`id, business_id, type, payload_json="{}", customer_id, employee_id, dedup_key, occurred_at`; `dedup_key` unique+indexed).

- [ ] **Step 1: Write the failing test**

```python
# agent/tests/test_event_model.py
from sqlmodel import Session
from db import engine, init_db
from db_models import Business, Event

def test_event_persists():
    init_db()
    with Session(engine) as s:
        b = Business(business_name="B", trade="hvac", email="ev@test.io"); s.add(b); s.commit(); s.refresh(b)
        e = Event(business_id=b.id, type="message.received", dedup_key="SM123")
        s.add(e); s.commit(); s.refresh(e)
        assert e.id and e.occurred_at
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Add the model**

```python
# agent/db_models.py
class Event(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    business_id: int = Field(foreign_key="business.id", index=True)
    type: str = Field(index=True)
    payload_json: str = "{}"
    customer_id: Optional[int] = Field(default=None, foreign_key="customer.id")
    employee_id: Optional[int] = Field(default=None, foreign_key="employee.id")
    dedup_key: Optional[str] = Field(default=None, unique=True, index=True)
    occurred_at: datetime = Field(default_factory=datetime.utcnow)
```

- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `git add agent/db_models.py agent/tests/test_event_model.py && git commit -m "feat: append-only Event model"`

---

### Task 8: `events.py` — domain event catalog

**Files:** Create `agent/events.py`; Test `agent/tests/test_events.py`.
**Interfaces:** Produces the event-type constants and `DomainEvent` dataclass.

- [ ] **Step 1: Write the failing test**

```python
# agent/tests/test_events.py
from events import DomainEvent, MESSAGE_RECEIVED, JOB_BOOKED
def test_domain_event_defaults():
    e = DomainEvent(type=MESSAGE_RECEIVED, business_id=1, payload={"text": "hi"})
    assert e.customer_id is None and e.dedup_key is None
    assert MESSAGE_RECEIVED == "message.received" and JOB_BOOKED == "job.booked"
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement**

```python
# agent/events.py
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

MESSAGE_RECEIVED = "message.received"
CALL_MISSED = "call.missed"
CALL_RECEIVED = "call.received"
CALL_COMPLETED = "call.completed"
JOB_BOOKED = "job.booked"
JOB_COMPLETED = "job.completed"
QUOTE_SENT = "quote.sent"
QUOTE_FOLLOWUP_DUE = "quote.followup_due"
CUSTOMER_DORMANT = "customer.dormant"
MEMBERSHIP_RENEWAL_DUE = "membership.renewal_due"
REVIEW_REQUESTED = "review.requested"
REFERRAL_RECEIVED = "referral.received"
LLM_COMPLETED = "llm.completed"

@dataclass
class DomainEvent:
    type: str
    business_id: int
    payload: Dict[str, Any] = field(default_factory=dict)
    customer_id: Optional[int] = None
    employee_id: Optional[int] = None
    dedup_key: Optional[str] = None
```

- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `git add agent/events.py agent/tests/test_events.py && git commit -m "feat: domain event catalog + DomainEvent dataclass"`

---

### Task 9: `EventBus` — synchronous, persisted, idempotent

**Files:** Create `agent/eventbus.py`; Test `agent/tests/test_eventbus.py`.
**Interfaces:** Consumes `events.DomainEvent`, `db_models.Event`. Produces `EventBus` (`subscribe(type, handler)`, `publish(event) -> bool` — `False` if dropped as duplicate) and a module singleton `bus = EventBus()`.

- [ ] **Step 1: Write the failing test**

```python
# agent/tests/test_eventbus.py
from eventbus import EventBus
from events import DomainEvent, MESSAGE_RECEIVED
from sqlmodel import Session, select
from db import engine, init_db
from db_models import Business, Event

def _bid():
    init_db()
    with Session(engine) as s:
        b = Business(business_name="B", trade="hvac", email="bus@test.io"); s.add(b); s.commit(); s.refresh(b); return b.id

def test_publish_dispatches_and_persists():
    bid = _bid(); seen = []
    bus = EventBus(); bus.subscribe(MESSAGE_RECEIVED, lambda e: seen.append(e.payload["text"]))
    assert bus.publish(DomainEvent(type=MESSAGE_RECEIVED, business_id=bid, payload={"text": "hi"})) is True
    assert seen == ["hi"]
    with Session(engine) as s:
        assert s.exec(select(Event).where(Event.type == MESSAGE_RECEIVED)).first() is not None

def test_duplicate_dedup_key_dropped():
    bid = _bid(); seen = []
    bus = EventBus(); bus.subscribe(MESSAGE_RECEIVED, lambda e: seen.append(1))
    mk = lambda: DomainEvent(type=MESSAGE_RECEIVED, business_id=bid, payload={}, dedup_key="SM-DUP")
    assert bus.publish(mk()) is True
    assert bus.publish(mk()) is False
    assert seen == [1]
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement**

```python
# agent/eventbus.py
import json
from collections import defaultdict
from typing import Callable, Dict, List
from sqlmodel import Session, select
from db import engine
from db_models import Event
from events import DomainEvent

class EventBus:
    def __init__(self):
        self._subscribers: Dict[str, List[Callable[[DomainEvent], None]]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Callable[[DomainEvent], None]) -> None:
        self._subscribers[event_type].append(handler)

    def publish(self, event: DomainEvent) -> bool:
        with Session(engine) as s:
            if event.dedup_key and s.exec(select(Event).where(Event.dedup_key == event.dedup_key)).first():
                return False
            s.add(Event(business_id=event.business_id, type=event.type,
                        payload_json=json.dumps(event.payload), customer_id=event.customer_id,
                        employee_id=event.employee_id, dedup_key=event.dedup_key))
            s.commit()
        for handler in self._subscribers.get(event.type, []):
            handler(event)   # synchronous, in-process (async/durable deferred behind this seam)
        return True

bus = EventBus()
```

- [ ] **Step 4: Run** → PASS (2).
- [ ] **Step 5: Commit** — `git add agent/eventbus.py agent/tests/test_eventbus.py && git commit -m "feat: synchronous persisted EventBus with dedup-key idempotency"`

---

### Task 10: `BusinessMemory` port + relational body

**Files:** Create `agent/memory.py`; Test `agent/tests/test_memory.py`.
**Interfaces:** Produces `BusinessMemory(business_id)` with `profile()`, `get_customer(phone)`, `upsert_customer(phone, name=None)`, `timeline(customer_id)`, `recall(query, limit=20)`. Queries the `Message` model (renamed to `Interaction` in Phase 2 — update then).

- [ ] **Step 1: Write the failing test**

```python
# agent/tests/test_memory.py
from sqlmodel import Session
from db import engine, init_db
from db_models import Business, Message
from memory import BusinessMemory

def test_recall_is_business_scoped():
    init_db()
    with Session(engine) as s:
        b1 = Business(business_name="One", trade="hvac", email="m1@test.io")
        b2 = Business(business_name="Two", trade="plumbing", email="m2@test.io")
        s.add(b1); s.add(b2); s.commit(); s.refresh(b1); s.refresh(b2)
        c = BusinessMemory(b1.id).upsert_customer("+15551110000", "A")
        s.add(Message(business_id=b1.id, customer_id=c.id, customer_phone="+15551110000", role="user", content_json='"help"'))
        s.add(Message(business_id=b2.id, customer_phone="x", role="user", content_json='"other"'))
        s.commit()
    recalled = BusinessMemory(b1.id).recall("anything")
    assert recalled and all(m.business_id == b1.id for m in recalled)
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement**

```python
# agent/memory.py
from typing import List, Optional
from sqlmodel import Session, select
from db import engine
from db_models import Business, Customer, Message
from repositories import get_or_create_customer

class BusinessMemory:
    """Port scoped to ONE business. Business-scoped isolation IS the security
    boundary (prevents cross-tenant leakage / prompt-injection exfiltration).
    v1 body is relational; recall() is a recency stub — semantic swap later,
    same signature, no caller changes. Queries `Message` (→ `Interaction` in Phase 2)."""
    def __init__(self, business_id: int):
        self.business_id = business_id

    def profile(self) -> Optional[Business]:
        with Session(engine) as s:
            return s.get(Business, self.business_id)

    def get_customer(self, phone: str) -> Optional[Customer]:
        with Session(engine) as s:
            return s.exec(select(Customer).where(
                Customer.business_id == self.business_id, Customer.phone == phone)).first()

    def upsert_customer(self, phone: str, name: Optional[str] = None) -> Customer:
        with Session(engine) as s:
            return get_or_create_customer(s, self.business_id, phone, name)

    def timeline(self, customer_id: int) -> List[Message]:
        with Session(engine) as s:
            return s.exec(select(Message).where(
                Message.business_id == self.business_id, Message.customer_id == customer_id
            ).order_by(Message.id)).all()

    def recall(self, query: str, limit: int = 20) -> List[Message]:
        with Session(engine) as s:
            return list(reversed(s.exec(select(Message).where(
                Message.business_id == self.business_id).order_by(Message.id.desc()).limit(limit)).all()))
```

- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `git add agent/memory.py agent/tests/test_memory.py && git commit -m "feat: BusinessMemory port with business-scoped relational body + recall stub"`

---

### Task 11: Wire inbound + job.booked to publish events (non-breaking)

Fill the log and make the seam real **without** removing existing direct calls (the Runner consumes them in Phase 3).

**Files:** Modify `agent/service.py` (publish `JOB_BOOKED` + link job to Customer), `agent/app.py` (SMS webhook publishes `MESSAGE_RECEIVED` with Twilio `MessageSid` as `dedup_key`); Test `agent/tests/test_event_wiring.py`.
**Interfaces:** Consumes `eventbus.bus`, `events.*`, `repositories.get_or_create_customer`. Produces an `Event` row per inbound SMS and per booked job; existing reply/booking behavior unchanged.

- [ ] **Step 1: Write the failing test**

```python
# agent/tests/test_event_wiring.py
import json
from sqlmodel import Session, select
from db import engine, init_db
from db_models import Business, Event
from events import JOB_BOOKED
from service import handle_customer_message

class _FakeAgent:
    def respond(self, cfg, history):
        return {"reply": "ok", "jobs": [{"id": "t1", "input": {"service_type": "AC", "urgency": "routine"}}],
                "new_messages": [], "pending_tool_call": None}

def test_job_booked_event_emitted(monkeypatch):
    init_db()
    import service
    monkeypatch.setattr(service, "agent", _FakeAgent())
    with Session(engine) as s:
        b = Business(business_name="B", trade="hvac", email="wire@test.io", frontdesk_live=True, trial_cap_cents=10000)
        s.add(b); s.commit(); s.refresh(b)
        handle_customer_message(s, b, "+15552223333", "my AC is out")
        ev = s.exec(select(Event).where(Event.type == JOB_BOOKED)).first()
        assert ev is not None and json.loads(ev.payload_json).get("service_type") == "AC"
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** — in `service.handle_customer_message`, when persisting each captured `Job` (import `from eventbus import bus`, `from events import DomainEvent, JOB_BOOKED`, `from repositories import get_or_create_customer`):

```python
cust = get_or_create_customer(session, client.id, customer_phone, ji.get("customer_name"))
job.customer_id = cust.id
# ...existing session.add(job)...
```
then after the final `session.commit()`, for each captured job:
```python
bus.publish(DomainEvent(type=JOB_BOOKED, business_id=client.id, customer_id=job.customer_id,
                        payload={"service_type": job.service_type, "urgency": job.urgency, "job_id": job.id}))
```
In `app.py`'s SMS webhook, publish `MESSAGE_RECEIVED` with `dedup_key=<Twilio MessageSid>` before handling (Twilio-retry-safe). **Keep the existing TwiML reply path exactly as-is.**

- [ ] **Step 4: Run** → PASS; full suite → green.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: publish message.received + job.booked events (seam live, behavior unchanged)"`

---

## ✅ FOUNDATION B CHECKPOINT

- [ ] Full suite green.
- [ ] Manual sanity: an inbound SMS writes a `message.received` Event; a booked job writes a `job.booked` Event; a duplicate MessageSid does not double-process; existing SMS/voice/dashboard behavior unchanged.
- [ ] **STOP. Independently deployable.** The event seam is live and persisted but nothing consumes it destructively yet. Only after this is committed and green does Phase 2 begin.

---

## Self-Review

- **Spec coverage:** session secret (1) ✔ · Business rename incl. FK columns (2) ✔ · first-class Customer + link (3–5) ✔ · Employee + kill-switch data (6) ✔ · Event log (7) ✔ · event catalog (8) ✔ · EventBus + idempotency (9) ✔ · Memory port (10) ✔ · event wiring (11) ✔. **Deferred by design:** Runner + RoleDefinitions (Phase 3), Channel/LLM/Integration formalization + owner job-delivery (Phase 2–3), `message`→`interaction` rename (Phase 2), pause/fire endpoints + UI (Phase 4).
- **Placeholder scan:** none — every step has real code or exact commands.
- **Type consistency:** `Business`/`business_id` used uniformly after Task 2; `Customer`, `Employee`, `Event`, `DomainEvent`, `EventBus.publish/subscribe`, `BusinessMemory` methods, `get_or_create_customer` consistent across tasks; Memory queries `Message` (flagged for the Phase 2 rename).
- **Two-checkpoint integrity:** Foundation A introduces no event code; Foundation B introduces no rename. A regression is attributable to exactly one class of change.

---

## Roadmap — Phases 2–6 (detailed plans written just-in-time)

Each phase is a *contract*. Its bite-sized plan is written when the phase starts, against the real signatures the prior phase produced — never ahead of it.

### Phase 2 — Communication
- **Goal:** formalize the `Channel` port, deliver booked jobs to the owner, and perform the `message`→`interaction` rename now that `channel`/`direction` are used.
- **Deliverables:** `Channel` port (`send`, `parse_inbound`, `capabilities`) over `TwilioChannel`/`ConsoleChannel`/xAI-voice; normalized `InboundMessage`/`OutboundMessage`; **owner job-delivery** subscriber on `JOB_BOOKED` (SMS/email to `escalation_phone`); `Message`→`Interaction` rename + `channel`/`direction`/`handled_by_employee_id`; update `BusinessMemory` to query `Interaction`.
- **Done when:** inbound SMS + live voice route through the port; a booked job notifies the owner immediately; behavior otherwise unchanged; tests green.

### Phase 3 — Receptionist (Frontdesk on the spine)
- **Goal:** the wedge employee runs end-to-end on the declarative model.
- **Deliverables:** `RoleDefinition` structure + registry; the generic `Runner` (event → active employees → capability → emitted events); Frontdesk `RoleDefinition` wrapping the `engine.py` loop; the `LLM` port with model-tier policy (**fix stale `claude-sonnet-4-6` here via the claude-api reference**); `LLM_COMPLETED` logged.
- **Done when:** `MESSAGE_RECEIVED`/`CALL_MISSED` drives Frontdesk via the Runner (not a direct call), books + delivers a job, old direct path removed, tests green.

### Phase 4 — Dashboard
- **Goal:** the owner surface on the new model, honest and controllable.
- **Deliverables:** status ladder unchanged; activity log from `Job`/`Customer`; employee roster from `Employee`; **pause/resume/fire** endpoints + controls honored by the Runner (`Employee.status`); Reviews & Referrals card.
- **Done when:** pausing an employee stops its dispatch; fire retains memory; DESIGN.md tokens only; tests green.

### Phase 5 — Quote Chaser
- **Goal:** the value layer begins.
- **Deliverables:** `quote_chaser` `RoleDefinition` (subscribes `QUOTE_SENT`/`QUOTE_FOLLOWUP_DUE`); a minimal `Scheduler` port emitting `QUOTE_FOLLOWUP_DUE`; hire flow creates the `Employee` row.
- **Done when:** a sent quote schedules + fires follow-ups through the Runner; conversions logged; tests green.

### Phase 6 — Retention
- **Goal:** the compounding, memory-inheriting second Recovery employee.
- **Deliverables:** `retention` `RoleDefinition` (reactivation + renewals faces; subscribes `JOB_COMPLETED`/`CUSTOMER_DORMANT`/`MEMBERSHIP_RENEWAL_DUE`); Scheduler emits dormancy/renewal events.
- **Done when:** a dormant customer captured by Frontdesk is reactivated by Retention with no re-entry of business/customer data (the network-effect proof); tests green.

**Roadmap gate (PRD §2 + frozen-architecture rule):** do not advance past Phase 3 into pure-platform polish without a real pilot in motion. Jobs booked for real businesses outrank remaining platform tasks.
