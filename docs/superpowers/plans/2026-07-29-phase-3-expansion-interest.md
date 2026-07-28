# Phase 3 — Expansion-Interest Capture: Task-Level Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every "Ask us about [Department]" moment in the blueprint
(§7 inactive cards, §8 Briefing recommendations, §4 Overview prompts) a real
place to land — one durable, de-duplicated request per business per
department, feeding Phase 4's ops pipeline.

**Architecture:** One new table (`DepartmentInterest`) and one new module
(`agent/expansion.py`). Interest is **strictly a request record with no
bearing on what is deployed** — the distinction the existing
`requested_roster` blob fails to make (H1). De-duplication is enforced by a
**database-level partial unique index**, not an application check, because
concurrency here is real on both supported dialects (H3).

**Tech Stack:** Python 3, SQLModel/SQLAlchemy, pytest. No new dependency.

**Parent plan:** `docs/superpowers/plans/2026-07-28-departments-migration-execution-plan.md` (Phase 3)
**Product source of truth:** `docs/superpowers/specs/2026-07-28-departments-product-blueprint-design.md`

## Global Constraints

- Run tests from `agent/`: `cd agent && .venv/bin/python -m pytest tests/ -q`.
- Baseline to preserve: **359 passing** (end of Phase 2).
- **Additive only.** No existing route, template, model, or function changes behavior. `departments.py` gains one lookup helper; nothing else outside the new files is touched.
- **Recording interest must never deploy anything.** No `Employee` row, no `requested_roster` write, no effect on `runner.is_active()`. See H1 — this is the whole point of the phase.
- Must work identically on **SQLite and Postgres** (`db.resolve_engine_config`): the partial index is declared for both dialects.
- **`expansion.py` is imported by nothing outside `tests/` when this phase ends.** Phase 4 (ops console) and Phase 5 (customer CTA) are its first consumers.
- Commit after every task; each task leaves the full suite green on its own.

---

## Pre-Implementation Findings

### H1 — `requested_roster` already conflates "asked for" with "deployed" ⚠️

`runner.is_active()` (`runner.py:41-56`) decides whether an employee is
**live** by checking membership in `Business.requested_roster`. And
`portal.py:419-431`'s customer-facing "Hire" button appends to that same
list. So today, **a customer asking for something is the same event as it
being deployed** — there is no state between "wants it" and "has it."

This is exactly what the new product model forbids: Roster provisions after
a discovery call, so "the customer asked" and "we deployed" must be
different, separately-observable facts.

**Consequence for this phase:** `DepartmentInterest` is a pure request log.
It must never be read by `runner.is_active()`, never create an `Employee`,
and never write `requested_roster`. Task 1 includes an explicit test that
recording interest leaves deployment state untouched — a guard against a
future edit "helpfully" wiring the two together and silently recreating the
conflation this migration exists to remove.

### H2 — The founder deploy route creates no `Employee` row (a Phase 4 bug, found now) ⚠️

`app.py:459-475`'s `deploy_employee` appends to `requested_roster` and
**stops**. Unlike `portal.py:49-58`'s `_hire_employee`, it never inserts an
`Employee` row. Rows only appear later, when `db.py:_backfill_employees`
runs on the next boot.

**Why this matters here:** Phase 1's `active_departments_for()` reads
`Employee` rows. So a business the founder deploys through the admin route
would show **zero departments** on its dashboard until the process restarts.
Phase 3 is unaffected, but Phase 4 must fix this route to create `Employee`
rows directly, and Phase 5's dashboard depends on that fix. Recorded here so
it is not rediscovered as a mystery bug two phases from now; added to the
parent execution plan's Phase 4 scope.

### H3 — Concurrency is real on both dialects; an application check is not enough ⚠️ *(plan changed)*

The parent plan said interest should be "idempotent per
`(business_id, department_key)` while `actioned_at` is null" without saying
how. A SELECT-then-INSERT cannot deliver that:

- On **SQLite** (`railway.toml`: `--workers 1`), FastAPI still runs sync
  route handlers in a **threadpool** — a double-clicked button submits twice
  concurrently in one process.
- On **Postgres** (`db.resolve_engine_config` prefers `DATABASE_URL`; it is
  in `app.py:_PRODUCTION_CRITICAL_ENV`), the app is designed for **many
  worker processes and hosts** — `locks.py` already uses `pg_advisory_lock`
  precisely because process-local locking is insufficient there.

**Resolution:** a real **partial unique index** on
`(business_id, department_key) WHERE actioned_at IS NULL`, declared with both
`sqlite_where` and `postgresql_where` so each dialect gets it. The
application then follows the pattern already proven in this codebase
(`repositories.get_or_create_customer`, `eventbus.publish`,
`app.py:inbound_sms`): try, catch `IntegrityError`, re-select the winner.

The partial predicate is what allows a business to ask **again later** —
once ops actions a request, the row leaves the index and a fresh request is
permitted.

### H4 — No hidden writers, and `AccessRequest` is a different concern

A full grep for anything that records "customer wants more" finds only:

| Existing mechanism | What it really is | Fate |
|---|---|---|
| `Business.requested_roster` | Conflated request+deployment blob (H1) | Customer half deleted in Phase 7; founder half replaced by real `Employee` rows in Phase 4 |
| `AccessRequest` | The **new-lead** contact form (no `business_id` — the business doesn't exist yet) | Kept; extended in Phase 6 |

`DepartmentInterest` overlaps neither: it is an **existing customer** asking
for **one more department**. Keeping it separate from `AccessRequest` is
deliberate — a lead and an expansion request enter the ops pipeline at
different stages (blueprint §10a) and have different required fields.

### H5 — Validation sits at a trust boundary *(plan changed)*

Phase 5 will expose `POST /dashboard/departments/{key}/interest` to a
logged-in customer, so `department_key` is attacker-controllable input.
Two rejections are required, not optional:

- An **unknown** key (typo, tampering, a stale bookmark after a rename).
- A **non-hireable** department — `leadership` is included automatically with
  any active department and is never sold (blueprint §4a). Accepting interest
  in it would put a nonsense row in front of ops.

`record_interest` raises `ValueError` for both, so Phase 5's route can
return 400 rather than silently writing a bad row. This is stricter than the
parent plan, which only said "raising on an unknown key."

### H6 — Nothing may auto-record interest

Blueprint §8's growth nudge (*"12 estimates sent this month, none followed
up"*) is **displayed** by the Briefing. Rendering a recommendation must never
record interest — only an explicit customer click does. Otherwise the ops
queue fills with requests nobody made, and the signal is worthless. Stated
here so Phase 5 does not wire the Briefing to the write path; there is no
code in this phase that could do it.

### Confirmed, no change needed

- **A new table needs no migration** — `SQLModel.metadata.create_all()` creates
  missing tables *and their indexes* (`db.py:89`); `_migrate_add_columns` is
  only for new columns on existing tables. Same as Phase 2's table.
- **`expansion.py` must be a separate module, not part of `departments.py`.**
  Phase 1 deliberately kept `departments.py` free of any database dependency
  (it duck-types, imports no `db_models`, and its 20 tests need no fixture).
  Putting DB writes there would destroy that property.
- **`__table_args__` with an `Index` is an established pattern here** —
  `db_models.Customer:61` already uses `__table_args__`, and `test_customer.py:39`
  already asserts a constraint by expecting `IntegrityError`.

---

## Task 1: The `DepartmentInterest` table and its uniqueness guarantee

**Files:**
- Modify: `agent/db_models.py` (append the model)
- Test: `agent/tests/test_expansion.py` (new)

**Interfaces:**
- Produces: `db_models.DepartmentInterest` — `id`, `business_id: int` (FK `business.id`, indexed), `department_key: str`, `created_at: datetime`, `actioned_at: Optional[datetime]`, plus a partial unique index `uq_department_interest_open` on `(business_id, department_key) WHERE actioned_at IS NULL`.

- [ ] **Step 1: Write the failing tests**

Create `agent/tests/test_expansion.py`:

```python
"""Expansion interest: an existing customer asking for one more department.

Strictly a REQUEST record. It must never imply deployment — that conflation
is exactly what Business.requested_roster gets wrong today (runner.is_active
treats "requested" as "live"), and it is what the department migration exists
to undo."""
import json
from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from db_models import Business, DepartmentInterest


def test_a_new_interest_starts_open(session):
    row = DepartmentInterest(business_id=1, department_key="finance")
    session.add(row)
    session.commit()
    session.refresh(row)
    assert row.actioned_at is None
    assert row.created_at is not None


def test_the_database_rejects_a_second_open_interest_in_one_department(session):
    """H3: de-duplication is a database guarantee, not an application check.
    A double-clicked button submits twice concurrently — on SQLite through
    FastAPI's threadpool, on Postgres across worker processes — and a
    SELECT-then-INSERT would let both through."""
    session.add(DepartmentInterest(business_id=1, department_key="finance"))
    session.commit()

    session.add(DepartmentInterest(business_id=1, department_key="finance"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_two_businesses_may_each_have_open_interest_in_the_same_department(session):
    """The index is scoped by business_id — one business's request must never
    block another's."""
    session.add(DepartmentInterest(business_id=1, department_key="finance"))
    session.add(DepartmentInterest(business_id=2, department_key="finance"))
    session.commit()

    assert len(session.exec(select(DepartmentInterest)).all()) == 2


def test_a_business_may_ask_again_once_the_previous_request_is_actioned(session):
    """The index is PARTIAL (WHERE actioned_at IS NULL) precisely so an
    actioned row leaves it. A customer declined in March can ask again in
    June without ops having to delete history."""
    first = DepartmentInterest(business_id=1, department_key="finance")
    session.add(first)
    session.commit()

    first.actioned_at = datetime.utcnow()
    session.add(first)
    session.commit()

    session.add(DepartmentInterest(business_id=1, department_key="finance"))
    session.commit()  # must not raise

    assert len(session.exec(select(DepartmentInterest)).all()) == 2
```

- [ ] **Step 2: Run and confirm failure**

Run: `cd agent && .venv/bin/python -m pytest tests/test_expansion.py -q 2>&1 | tail -6`
Expected: collection error — `ImportError: cannot import name 'DepartmentInterest' from 'db_models'`.

- [ ] **Step 3: Add the model**

Append to `agent/db_models.py`:

```python
class DepartmentInterest(SQLModel, table=True):
    """An existing customer asking Roster for one more department.

    STRICTLY a request record: recording interest deploys nothing, creates no
    Employee, and has no effect on runner.is_active(). That separation is the
    point — Business.requested_roster currently conflates "asked for" with
    "deployed" (runner.py:41), and the department model requires them to be
    different, separately-observable facts. Roster provisions after a
    discovery call, never on a click.

    Distinct from AccessRequest, which is the NEW-LEAD contact form (it has no
    business_id — the business doesn't exist yet).
    """
    __table_args__ = (
        # One OPEN request per business per department, enforced by the
        # database because a double-submit is genuinely concurrent: FastAPI
        # runs sync handlers in a threadpool even at --workers 1, and Postgres
        # deployments run many workers. Declared for both dialects since
        # db.resolve_engine_config supports both.
        # PARTIAL on purpose: an actioned row leaves the index, so a customer
        # whose request was declined months ago can ask again.
        Index(
            "uq_department_interest_open",
            "business_id", "department_key",
            unique=True,
            sqlite_where=text("actioned_at IS NULL"),
            postgresql_where=text("actioned_at IS NULL"),
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    business_id: int = Field(foreign_key="business.id", index=True)
    department_key: str  # departments.REGISTRY key; validated on write by expansion.record_interest
    created_at: datetime = Field(default_factory=datetime.utcnow)
    actioned_at: Optional[datetime] = None  # set when ops has handled it (Phase 4)
```

…and widen the SQLAlchemy import at the top of `db_models.py`:

```python
from sqlalchemy import Index, UniqueConstraint, text
```

- [ ] **Step 4: Run new tests, then the full suite**

Run: `cd agent && .venv/bin/python -m pytest tests/test_expansion.py -v`
Expected: 4 passed. If
`test_the_database_rejects_a_second_open_interest_in_one_department` fails,
the partial index is not being created — check that `Index` is inside
`__table_args__` as a tuple and that the test engine ran `create_all`.

Run: `cd agent && .venv/bin/python -m pytest tests/ -q`
Expected: `363 passed`. No failures.

- [ ] **Step 5: Commit**

```bash
git add agent/db_models.py agent/tests/test_expansion.py
git commit -m "feat(expansion): DepartmentInterest table with a real uniqueness guarantee

An existing customer asking for one more department. Strictly a request
record: it deploys nothing and has no effect on runner.is_active() —
Business.requested_roster currently conflates 'asked for' with 'deployed',
and the department model requires those to be different facts.

De-duplication is a PARTIAL unique index on (business_id, department_key)
WHERE actioned_at IS NULL, declared for both SQLite and Postgres. A
database guarantee rather than an application check because a double-click
is genuinely concurrent (threadpool at --workers 1; many workers on
Postgres). Partial so an actioned request leaves the index and a customer
can ask again later."
```

---

## Task 2: `record_interest()` — validation, idempotency, and the race

**Files:**
- Modify: `agent/departments.py` (add the public `get_department` lookup)
- Create: `agent/expansion.py`
- Test: `agent/tests/test_expansion.py` (append)

**Interfaces:**
- Consumes: `departments.get_department`, `db_models.DepartmentInterest`.
- Produces:
  - `departments.get_department(key: str) -> Department | None`.
  - `expansion.record_interest(session, business_id: int, department_key: str) -> DepartmentInterest` — returns the existing open row if there is one, else a new one. Raises `ValueError` for an unknown or non-hireable department.

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_expansion.py`:

```python
# --- record_interest ---------------------------------------------------------

from expansion import record_interest


def test_record_interest_creates_an_open_request(session):
    row = record_interest(session, business_id=1, department_key="finance")

    assert row.id is not None
    assert row.department_key == "finance"
    assert row.actioned_at is None


def test_asking_twice_returns_the_same_open_request(session):
    """A customer double-clicking, or coming back a week later while the
    first request is still open, must not put two rows in front of ops."""
    first = record_interest(session, business_id=1, department_key="finance")
    second = record_interest(session, business_id=1, department_key="finance")

    assert first.id == second.id
    assert len(session.exec(select(DepartmentInterest)).all()) == 1


def test_record_interest_rejects_an_unknown_department(session):
    """H5: department_key is attacker-controllable — it arrives from a
    customer-facing POST in Phase 5."""
    with pytest.raises(ValueError):
        record_interest(session, business_id=1, department_key="not_a_department")


def test_record_interest_rejects_leadership(session):
    """Leadership is included automatically with any active department and is
    never sold (blueprint §4a). Accepting interest in it would put a nonsense
    row in front of ops."""
    with pytest.raises(ValueError):
        record_interest(session, business_id=1, department_key="leadership")


def test_one_business_cannot_record_interest_against_another(session):
    """Business isolation is the security boundary everywhere in Roster
    (platform PRD §12). Two businesses asking for the same department produce
    two independent rows, and neither can see or block the other."""
    a = record_interest(session, business_id=1, department_key="finance")
    b = record_interest(session, business_id=2, department_key="finance")

    assert a.id != b.id
    assert a.business_id == 1
    assert b.business_id == 2


def test_a_concurrent_duplicate_resolves_to_the_existing_row(session, monkeypatch):
    """H3: simulates the interleaving the partial index exists to catch — two
    threads both pass the 'is there an open request?' check, then both
    insert. The loser must return the winner's row, not raise."""
    import expansion

    real_open = expansion._open_interest
    calls = {"n": 0}

    def _pretend_nothing_exists(*args, **kwargs):
        # First call only: report "no open request" even though one exists,
        # exactly as a racing thread would have seen before the winner
        # committed.
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return real_open(*args, **kwargs)

    winner = record_interest(session, business_id=1, department_key="finance")
    monkeypatch.setattr(expansion, "_open_interest", _pretend_nothing_exists)

    loser = record_interest(session, business_id=1, department_key="finance")

    assert loser.id == winner.id
    assert len(session.exec(select(DepartmentInterest)).all()) == 1


def test_recording_interest_deploys_nothing(session):
    """H1 — the permanent guard for this phase.

    Business.requested_roster is what runner.is_active() reads to decide
    whether an employee is LIVE. Interest must never touch it, or a customer
    asking a question would silently deploy an employee — recreating the exact
    conflation this migration exists to remove."""
    from db_models import Employee

    biz = Business(business_name="B", trade="hvac", email="exp-guard@test.io")
    session.add(biz)
    session.commit()
    session.refresh(biz)

    record_interest(session, business_id=biz.id, department_key="finance")
    session.refresh(biz)

    assert biz.requested_roster is None
    assert session.exec(
        select(Employee).where(Employee.business_id == biz.id)
    ).all() == []
```

- [ ] **Step 2: Run and confirm failure**

Run: `cd agent && .venv/bin/python -m pytest tests/test_expansion.py -q 2>&1 | tail -6`
Expected: collection error — `ModuleNotFoundError: No module named 'expansion'`.

- [ ] **Step 3: Add the department lookup**

Append to `agent/departments.py`:

```python
def get_department(key: str):
    """The Department with this key, or None. The public lookup for callers
    validating a department key that arrived from outside (see
    expansion.record_interest)."""
    return _BY_KEY.get(key)
```

- [ ] **Step 4: Create the module**

Create `agent/expansion.py`:

```python
"""Expansion interest — an existing customer asking for one more department.

Recording interest DEPLOYS NOTHING. It creates no Employee, writes no
requested_roster, and has no effect on runner.is_active(). Roster provisions
after a discovery call (blueprint §10a), so "the customer asked" and "we
deployed" are deliberately separate, separately-observable facts. The
existing requested_roster blob fails to make that distinction; this module
must not repeat it.

Kept out of departments.py on purpose: that module is a pure registry with no
database dependency, and its tests need no fixture. This one owns the writes.
"""
from datetime import datetime
from typing import List, Optional

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from db_models import DepartmentInterest
from departments import get_department


def _open_interest(session, business_id: int, department_key: str) -> Optional[DepartmentInterest]:
    return session.exec(
        select(DepartmentInterest).where(
            DepartmentInterest.business_id == business_id,
            DepartmentInterest.department_key == department_key,
            DepartmentInterest.actioned_at.is_(None),
        )
    ).first()


def record_interest(session, business_id: int, department_key: str) -> DepartmentInterest:
    """Record that this business wants `department_key`, or return the request
    already open for it.

    Raises ValueError for an unknown or non-hireable department: this value
    arrives from a customer-facing POST, so it is validated at the boundary
    rather than trusted (Leadership is never sold — blueprint §4a).

    Concurrency: the "is one already open?" check and the insert are not
    atomic, so two racing submissions can both reach the insert. The partial
    unique index makes the database reject the loser, which then returns the
    winner's row — the same try/catch/re-select pattern used by
    repositories.get_or_create_customer and eventbus.publish.
    """
    department = get_department(department_key)
    if department is None:
        raise ValueError(f"unknown department: {department_key!r}")
    if not department.hireable:
        raise ValueError(f"{department_key!r} is not a hireable department")

    existing = _open_interest(session, business_id, department_key)
    if existing is not None:
        return existing

    row = DepartmentInterest(business_id=business_id, department_key=department_key)
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        # Lost the race: another submission created the open request between
        # our check and our insert. Return theirs.
        session.rollback()
        winner = _open_interest(session, business_id, department_key)
        if winner is None:
            # Would require the winning row to be actioned within microseconds
            # of being created. Fail loudly rather than return a lie.
            raise
        return winner
    session.refresh(row)
    return row
```

- [ ] **Step 5: Run new tests, then the full suite**

Run: `cd agent && .venv/bin/python -m pytest tests/test_expansion.py -v`
Expected: 11 passed.

Run: `cd agent && .venv/bin/python -m pytest tests/ -q`
Expected: `370 passed`. No failures.

- [ ] **Step 6: Commit**

```bash
git add agent/departments.py agent/expansion.py agent/tests/test_expansion.py
git commit -m "feat(expansion): record_interest with boundary validation

Returns the already-open request rather than creating a duplicate, and
resolves a genuine insert race by returning the winner's row (the same
try/catch/re-select pattern as get_or_create_customer).

department_key arrives from a customer-facing POST in Phase 5, so it is
validated at the boundary: unknown keys and non-hireable departments
(Leadership, which is never sold) both raise ValueError so the route can
return 400 rather than writing a nonsense row for ops.

A permanent guard asserts recording interest writes no requested_roster
and creates no Employee — a customer asking a question must never deploy."
```

---

## Task 3: The Phase 4 ops seam — read open requests and close them

**Files:**
- Modify: `agent/expansion.py` (append)
- Test: `agent/tests/test_expansion.py` (append)

**Interfaces:**
- Produces:
  - `expansion.open_interests_for(session, business_id: int) -> list[DepartmentInterest]` — this business's unactioned requests, oldest first (a queue, not a feed).
  - `expansion.mark_actioned(session, interest_id: int) -> Optional[DepartmentInterest]` — closes one request; returns it, or `None` if it doesn't exist. Idempotent.

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_expansion.py`:

```python
# --- the ops seam Phase 4 consumes -------------------------------------------

from expansion import mark_actioned, open_interests_for


def test_open_interests_are_oldest_first(session):
    """A work queue, not a feed: ops should handle the request that has been
    waiting longest. (Deliberately the opposite of recent_notifications,
    which is newest-first because it's something to read, not to work.)"""
    first = record_interest(session, 1, "finance")
    second = record_interest(session, 1, "marketing")

    assert [i.id for i in open_interests_for(session, 1)] == [first.id, second.id]


def test_open_interests_never_leak_another_business(session):
    record_interest(session, 1, "finance")
    record_interest(session, 2, "marketing")

    assert [i.department_key for i in open_interests_for(session, 1)] == ["finance"]


def test_open_interests_excludes_actioned_requests(session):
    handled = record_interest(session, 1, "finance")
    record_interest(session, 1, "marketing")

    mark_actioned(session, handled.id)

    assert [i.department_key for i in open_interests_for(session, 1)] == ["marketing"]


def test_mark_actioned_sets_the_timestamp(session):
    row = record_interest(session, 1, "finance")

    actioned = mark_actioned(session, row.id)

    assert actioned.actioned_at is not None


def test_mark_actioned_is_idempotent(session):
    """Ops double-clicking 'handled' must not move the timestamp or error."""
    row = record_interest(session, 1, "finance")

    first = mark_actioned(session, row.id)
    when = first.actioned_at
    second = mark_actioned(session, row.id)

    assert second.actioned_at == when


def test_mark_actioned_returns_none_for_an_unknown_id(session):
    assert mark_actioned(session, 99999) is None


def test_a_customer_can_ask_again_after_ops_actioned_the_request(session):
    """End-to-end of the partial index's purpose: declined in March, asks
    again in June, and ops sees a NEW request rather than a stale one."""
    first = record_interest(session, 1, "finance")
    mark_actioned(session, first.id)

    second = record_interest(session, 1, "finance")

    assert second.id != first.id
    assert [i.id for i in open_interests_for(session, 1)] == [second.id]


def test_actioning_a_request_deploys_nothing(session):
    """THE lifecycle invariant (founder, 2026-07-29).

    Actioning means "operations handled the request" — NOT "the department
    was deployed". Deployment is a separate operation that arrives in Phase 4
    and produces Employee rows. Walking the full lifecycle here
    (record -> action -> still nothing deployed) is what stops a future edit
    from making mark_actioned() a shortcut that provisions, which would
    recreate the requested_roster conflation (H1) one level up.

    Asserts through Phase 1's own helper, so the customer-visible answer —
    "which departments does this business have?" — is what's being checked,
    not just the absence of rows."""
    from db_models import Employee
    from departments import active_departments_for

    biz = Business(business_name="B", trade="hvac", email="lifecycle@test.io")
    session.add(biz)
    session.commit()
    session.refresh(biz)

    interest = record_interest(session, biz.id, "finance")
    mark_actioned(session, interest.id)
    session.refresh(biz)

    employees = session.exec(
        select(Employee).where(Employee.business_id == biz.id)
    ).all()
    assert employees == []
    assert active_departments_for(employees) == []
    assert biz.requested_roster is None
```

- [ ] **Step 2: Run and confirm failure**

Run: `cd agent && .venv/bin/python -m pytest tests/test_expansion.py -q 2>&1 | tail -5`
Expected: `ImportError: cannot import name 'mark_actioned' from 'expansion'`.

- [ ] **Step 3: Implement**

Append to `agent/expansion.py`:

```python
def open_interests_for(session, business_id: int) -> List[DepartmentInterest]:
    """This business's unactioned expansion requests, oldest first.

    Oldest-first because this is a work queue for ops (blueprint §10a's
    "ongoing customer management" stage) — the request waiting longest is the
    one to handle next. Scoped to one business_id: business isolation is the
    security boundary everywhere in Roster (platform PRD §12).
    """
    return list(session.exec(
        select(DepartmentInterest)
        .where(
            DepartmentInterest.business_id == business_id,
            DepartmentInterest.actioned_at.is_(None),
        )
        .order_by(DepartmentInterest.id)
    ).all())


def mark_actioned(session, interest_id: int) -> Optional[DepartmentInterest]:
    """Close one expansion request — ops has handled it, whether that meant
    deploying the department or declining. Returns the row, or None if there
    is no such request.

    Idempotent: re-closing an already-closed request keeps the original
    timestamp, so a double-click in the admin UI can't rewrite history.

    Closing a request also frees the partial unique index, which is what lets
    the same customer ask for that department again later.
    """
    row = session.get(DepartmentInterest, interest_id)
    if row is None:
        return None
    if row.actioned_at is None:
        row.actioned_at = datetime.utcnow()
        session.add(row)
        session.commit()
        session.refresh(row)
    return row
```

- [ ] **Step 4: Run new tests, then the full suite**

Run: `cd agent && .venv/bin/python -m pytest tests/test_expansion.py -v`
Expected: 19 passed.

Run: `cd agent && .venv/bin/python -m pytest tests/ -q`
Expected: `378 passed`. No failures.

- [ ] **Step 5: Verify the phase's no-consumer constraint**

Run: `cd agent && grep -rn "import expansion\|from expansion" --include="*.py" . | grep -v tests/ | grep -v "\.venv"`
Expected: **no output.** Phase 4 and Phase 5 are its first consumers.

- [ ] **Step 6: Commit**

```bash
git add agent/expansion.py agent/tests/test_expansion.py
git commit -m "feat(expansion): the ops seam Phase 4 consumes

open_interests_for() returns one business's unactioned requests oldest
first — a work queue, so ops handles what has waited longest (deliberately
the opposite of recent_notifications, which is newest-first because it's
read, not worked).

mark_actioned() closes a request idempotently, so a double-click in the
admin UI can't rewrite the timestamp. Closing also frees the partial
unique index, which is what lets the same customer ask again later —
tested end to end."
```

---

## Phase 3 Acceptance Criteria

1. **Full suite green at 378 passed**, up from Phase 2's 359 — 19 new tests, **zero pre-existing tests modified**.
2. **De-duplication is a database guarantee** — a second open interest raises `IntegrityError` at the DB level, verified directly, not only through `record_interest`.
3. **The partial predicate works** — an actioned request leaves the index and the same customer can ask again, tested end to end.
4. **Recording interest deploys nothing** — the H1 guard asserts no `requested_roster` write and no `Employee` row.
4b. **Actioning a request deploys nothing either** — the full lifecycle
   (record → action → still no `Employee`, still no active department per
   `active_departments_for`) is asserted, so "ops handled it" can never
   silently become "we provisioned it".
5. **Boundary validation holds** — unknown and non-hireable (`leadership`) department keys both raise `ValueError`.
6. **Business isolation holds** — `open_interests_for` never returns another business's rows; two businesses can hold interest in the same department simultaneously.
7. **`expansion.py` is imported by nothing outside `tests/`** — grep-verified.
8. **No migration code added** — `git diff main -- agent/db.py` empty.
9. **Three commits**, one per task, each green independently.
10. **App still boots** — `cd agent && .venv/bin/python -c "import app"` exits 0.

**Deliberately NOT in this phase:** no route, no UI, no cross-business ops query (Phase 4 adds one if its list view needs it — the table is queryable), no `outcome`/`declined` distinction on a closed request (whether ops deployed it is already visible in the `Employee` rows; add a column only if the ops console proves it needs one), no customer-facing note field (blueprint §7's CTA is a single click), and no auto-recording from the Briefing (H6).

---

## Change Made to the Parent Execution Plan

Phase 4's scope gains one item from H2: **`/clients/{id}/employees/deploy`
must create `Employee` rows directly.** It currently only appends to
`requested_roster`, so rows appear solely via `db.py:_backfill_employees` on
the next boot — meaning a founder-deployed business would show zero
departments on its dashboard until the process restarts. Phase 5's dashboard
depends on this fix.
