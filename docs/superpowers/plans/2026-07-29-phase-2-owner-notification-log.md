# Phase 2 — Owner Notification Log: Task-Level Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the future Notifications page (blueprint §4, §8) a durable,
queryable record of every owner-facing alert. Today all four owner alerts are
fire-and-forget SMS that vanish into a text thread — and a failed send is
completely invisible, swallowed by a bare `except`.

**Architecture:** Additive. One new table (`OwnerNotification`), one new
recording function and one query helper in the existing `notifications.py`,
called from the four existing alert sites *after* their primary work has
committed. The SMS sends themselves are not modified, and no
`notify_owner_of_*` return value changes — that last point is a hard
constraint, not a preference (see H4).

**Tech Stack:** Python 3, SQLModel, pytest. No new dependency.

**Parent plan:** `docs/superpowers/plans/2026-07-28-departments-migration-execution-plan.md` (Phase 2)
**Product source of truth:** `docs/superpowers/specs/2026-07-28-departments-product-blueprint-design.md`

## Global Constraints

- Run tests from `agent/`: `cd agent && .venv/bin/python -m pytest tests/ -q`.
- Baseline to preserve: **340 passing** (end of Phase 1).
- **No existing `notify_owner_of_*` return value or signature may change.** H4 explains why this one is load-bearing for a live caller in an emergency.
- **No existing test in `test_owner_notification.py` may be modified.** Its six unit tests run with no database at all; they must keep doing so (H3).
- Logging is **best-effort**: a failed log write must never raise into, or roll back, the booking/escalation that already committed.
- `record_owner_notification` is called **only after** the caller's primary work has been committed, so its own `commit()` can never flush someone else's half-finished transaction.
- No new dependency, no change to `engine.py`, `service.py`'s agent loop, channels, or any webhook's auth/dedup logic.
- Commit after every task; each task leaves the full suite green on its own.

---

## Pre-Implementation Findings

Investigation of every owner-notification path before writing this plan. Five
findings changed the plan; all five are incorporated below.

### H1 — There are FOUR alert sites, not one *(plan changed)*

The parent execution plan named `service.py` only. Actual call sites:

| # | Location | Function | Trigger |
|---|---|---|---|
| 1 | `service.py:118` | `notify_owner_of_booking` | SMS/dashboard booking |
| 2 | `xai_voice_adapter.py:145` | `notify_owner_of_booking` | Voice booking |
| 3 | `xai_voice_adapter.py:183` | `notify_owner_of_escalation` | `alert_owner` tool (emergency/complaint) |
| 4 | `xai_voice_adapter.py:247` | `notify_owner_of_escalation` | **Mid-call crash handler** — the call dropped, text the owner to call back |

Site 4 was not previously accounted for and is arguably the most valuable one
to log: it fires precisely when the software failed, which is exactly what an
owner needs a durable record of. All four are wired in this phase.

### H2 — Three of four sites run in a worker thread *(plan changed)*

Sites 2, 3, and 4 are invoked via `asyncio.to_thread(...)`. A `Session` is not
thread-safe, so the log write must **not** be pushed across that boundary.

**Resolution:** logging is a separate call (`record_owner_notification`) made
on the calling thread, *outside* the `to_thread(...)`, using the session
already in scope. The `to_thread` call itself is untouched. This also means
the send's blocking HTTP call stays off the event loop exactly as it is today.

### H3 — `notifications.py`'s unit tests use no database *(plan changed)*

All six tests in `test_owner_notification.py` construct bare `Business(...)`
and `Job(...)` objects with no `session` fixture — so `business.id` is `None`
and there is no engine in scope. Making `notify_owner_of_booking` itself write
to the database would break every one of them, and would write rows with a
null `business_id`.

**Resolution:** `notify_owner_of_*` stay pure senders with unchanged
signatures. Recording is a separate function taking an explicit `session` and
`business_id`. New tests go in a new file; the existing six are not touched.

### H4 — The escalation return value is load-bearing for a live caller ⚠️

`notify_owner_of_escalation`'s bool flows into `xai_voice_adapter.py:185-188`
as `"status": "owner_alerted" | "alert_failed"`, which is fed back to the
model — and `engine.py:113-114` instructs it: *"If alert_owner reports the
text failed, say so plainly and give them [the owner's number] to call
themselves — never claim help is coming when it isn't."*

So this boolean decides what a caller in an emergency is told. **If a
notification-log failure could flip it to False, the AI would tell someone
with a gas leak that the owner wasn't reached when they were.** Logging must
be strictly downstream of, and incapable of affecting, that value. Task 3
includes an explicit regression test for this.

### H5 — "Skipped" and "failed" are indistinguishable today *(plan changed)*

`notify_owner_of_booking` returns `False` for three different situations: no
escalation phone configured, a dashboard-test booking, and a genuine send
failure. And on failure the exception is swallowed with no log line at all
(unlike `trial_cap.py:76` and `app.py:450`, which at least `print`). A failed
owner text is currently **completely invisible**.

**Resolution:** the table gains a `delivered: bool` column (not in the parent
plan). Two of the three cases are then cleanly separated:
- Dashboard-test bookings → **no row at all** (not real activity; mirrors the
  SMS skip, and matches how `portal.py:322` already tags test jobs).
- Send failed, or no escalation phone configured → **a row with
  `delivered=False`**. The owner sees "we tried to tell you this" in the
  dashboard, which is strictly more than they get today.

### Confirmed, no change needed

- **Booking alerts are already de-duplicated** — `service.py:117` iterates
  `newly_created` and `xai_voice_adapter.py:144` checks `if created`, so a
  detail-merge never re-texts. Logging inherits this by living inside the same
  guard. Task 2 tests it.
- **`SQLModel.create_all()` creates missing tables automatically**
  (`db.py:125` documents this) — a new table needs **no** entry in
  `_migrate_add_columns` and no migration code. Only new *columns* on existing
  tables need that.
- **The trial-cap alert goes to the founder, not the owner**
  (`trial_cap.py:62`, `FOUNDER_ALERT_PHONE`). It is Roster's internal billing
  alert and must **not** appear in a customer's notification log.
- **The review-request SMS goes to the customer**, not the owner
  (`app.py:444`). Also not an owner notification.
- **`notifications._owner_channel` must stay monkeypatchable** —
  `test_owner_notification.py:90,113` patch it directly. Nothing in this phase
  changes how the channel is resolved.
- **"A department going live"**, listed in blueprint §4 as a notification
  type, has no producer until Phase 4/6 deploys departments. Deferred to
  those phases — the `kind` column accommodates it with no schema change.

---

## Task 1: The `OwnerNotification` table and `record_owner_notification()`

**Files:**
- Modify: `agent/db_models.py` (append the model)
- Modify: `agent/notifications.py` (append kinds, `is_test_thread`, `record_owner_notification`)
- Test: `agent/tests/test_owner_notification_log.py` (new)

**Interfaces:**
- Produces:
  - `db_models.OwnerNotification` — `id`, `business_id: int` (FK `business.id`, indexed), `kind: str`, `source: str`, `message: str`, `delivered: bool`, `created_at: datetime`, `read_at: Optional[datetime]`.
  - `notifications.KIND_JOB_BOOKED = "job_booked"`, `KIND_ESCALATION = "escalation"`, `KIND_CALL_DROPPED = "call_dropped"`.
  - `notifications.SOURCE_SMS_BOOKING = "sms_booking"`, `SOURCE_VOICE_BOOKING = "voice_booking"`, `SOURCE_ALERT_OWNER = "alert_owner"`, `SOURCE_CALL_DROPPED = "call_dropped"`.
  - `notifications.is_test_thread(customer_phone: str) -> bool`.
  - `notifications.record_owner_notification(session, business_id: int, kind: str, source: str, message: str, delivered: bool) -> Optional[OwnerNotification]` — returns the row, or `None` if the write failed. Never raises.

**`kind` vs `source` — two axes, deliberately not collapsed (founder, 2026-07-29):**
`kind` is *what happened*, semantically — the axis a customer-facing view
would group or filter on. `source` is *where it originated* — operational
only, for debugging and analytics, never parsed out of free-form text. They
are not redundant: `job_booked` arrives from two different origins, and
telling them apart without string-matching the message body is the entire
point.

| Call site | `kind` | `source` |
|---|---|---|
| `service.py` booking | `job_booked` | `sms_booking` |
| `xai_voice_adapter._persist_job` | `job_booked` | `voice_booking` |
| `alert_owner` tool | `escalation` | `alert_owner` |
| mid-call crash handler | `call_dropped` | `call_dropped` |

- [ ] **Step 1: Write the failing tests**

Create `agent/tests/test_owner_notification_log.py`:

```python
"""The durable owner-notification log. Today every owner alert is a
fire-and-forget SMS: it vanishes into a text thread, and a failed send is
swallowed silently (notifications.py's bare `except`). These rows are what
the Notifications page reads (blueprint §4), and the first place a failed
owner alert is visible at all."""
from sqlmodel import select

from db_models import OwnerNotification
from notifications import (
    KIND_ESCALATION,
    KIND_JOB_BOOKED,
    is_test_thread,
    record_owner_notification,
)


def test_records_a_delivered_notification(session):
    row = record_owner_notification(
        session, business_id=1, kind=KIND_JOB_BOOKED,
        message="Frontdesk just booked a job", delivered=True,
    )
    assert row.id is not None
    stored = session.exec(select(OwnerNotification)).all()
    assert len(stored) == 1
    assert stored[0].business_id == 1
    assert stored[0].kind == KIND_JOB_BOOKED
    assert stored[0].message == "Frontdesk just booked a job"
    assert stored[0].delivered is True


def test_records_an_undelivered_notification(session):
    """H5: a send that failed, or a business with no escalation phone set,
    still gets a row — marked undelivered. The dashboard is then the only
    place that alert exists, which is the entire point of the log."""
    record_owner_notification(
        session, business_id=1, kind=KIND_ESCALATION,
        message="URGENT — caller needs you", delivered=False,
    )
    stored = session.exec(select(OwnerNotification)).all()
    assert stored[0].delivered is False


def test_a_new_notification_starts_unread(session):
    """read_at has no producer until the Notifications page ships (Phase 5).
    It defaults to None so that page has a real unread signal to read."""
    row = record_owner_notification(
        session, business_id=1, kind=KIND_JOB_BOOKED, message="m", delivered=True,
    )
    assert row.read_at is None


def test_a_failed_write_returns_none_and_never_raises():
    """The log is strictly less important than the booking that just
    committed. A broken session must not propagate an exception into the
    alert path — the caller has already done the work that matters."""
    class BrokenSession:
        def add(self, _row):
            raise RuntimeError("database is gone")

        def rollback(self):
            raise RuntimeError("rollback also fails")

    assert record_owner_notification(
        BrokenSession(), business_id=1, kind=KIND_JOB_BOOKED,
        message="m", delivered=True,
    ) is None


def test_is_test_thread_matches_the_threads_that_skip_owner_sms():
    """Must stay in lockstep with notifications._TEST_THREADS — the log and
    the SMS have to agree about what counts as real activity, or the
    dashboard shows the owner their own test bookings."""
    assert is_test_thread("dashboard") is True
    assert is_test_thread("portal-test") is True
    assert is_test_thread("+15125550123") is False
```

- [ ] **Step 2: Run and confirm failure**

Run: `cd agent && .venv/bin/python -m pytest tests/test_owner_notification_log.py -q 2>&1 | tail -6`

Expected: collection error — `ImportError: cannot import name 'OwnerNotification' from 'db_models'`.

- [ ] **Step 3: Add the model**

Append to `agent/db_models.py`:

```python
class OwnerNotification(SQLModel, table=True):
    """A durable record of an owner-facing alert. Every alert is also sent as
    an SMS (see notifications.py) — this table is what the dashboard's
    Notifications page reads, and the only place a FAILED send is visible at
    all (`delivered=False`); today a failed owner text is swallowed silently.

    Deliberately separate from the `event` table: nothing in the live
    SMS/voice path publishes through eventbus.py today, and wiring the bus
    into that path is a much larger change than a notifications list needs.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    business_id: int = Field(foreign_key="business.id", index=True)
    kind: str  # notifications.KIND_* — WHAT happened: job_booked | escalation | call_dropped
    source: str = ""  # notifications.SOURCE_* — WHERE it came from: sms_booking |
    # voice_booking | alert_owner | call_dropped. Operational, not customer-facing:
    # lets us tell an SMS booking from a voice one without parsing `message`.
    message: str  # the exact text the owner was sent, so the log never drifts from the SMS
    delivered: bool = True  # False when the SMS send failed or no escalation phone is set
    created_at: datetime = Field(default_factory=datetime.utcnow)
    read_at: Optional[datetime] = None  # no producer until the Notifications page ships
```

- [ ] **Step 4: Add the recording function**

Append to `agent/notifications.py`:

```python
# Notification kinds. Stored as plain strings so a new kind (e.g. a
# department going live, Phase 4/6) needs no migration.
KIND_JOB_BOOKED = "job_booked"
KIND_ESCALATION = "escalation"
KIND_CALL_DROPPED = "call_dropped"

# Where a notification originated. Operational, not customer-facing: it
# exists so we can tell an SMS booking from a voice one (both KIND_JOB_BOOKED)
# without string-matching the message body.
SOURCE_SMS_BOOKING = "sms_booking"
SOURCE_VOICE_BOOKING = "voice_booking"
SOURCE_ALERT_OWNER = "alert_owner"
SOURCE_CALL_DROPPED = "call_dropped"


def is_test_thread(customer_phone: str) -> bool:
    """True for the owner's own test conversations, which never produce a
    real owner alert. Shares _TEST_THREADS with notify_owner_of_booking so
    the SMS and the log can't disagree about what's real activity."""
    return customer_phone in _TEST_THREADS


def record_owner_notification(session, business_id: int, kind: str, source: str,
                              message: str, delivered: bool):
    """Persist an owner alert so it survives the SMS. Returns the row, or None
    if the write failed.

    Best-effort by the same rule as the sends above: the booking or escalation
    this describes has ALREADY committed and matters far more than its record,
    so nothing here may raise into the caller.

    Call this only AFTER the caller's primary work is committed — this commits
    the session, and an in-flight transaction would be flushed with it.
    """
    try:
        row = OwnerNotification(
            business_id=business_id, kind=kind, source=source,
            message=message, delivered=delivered,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row
    except Exception as e:
        # Loud, unlike the sends above: a lost notification row is invisible
        # everywhere else, and this is the module that's supposed to fix
        # exactly that class of silence.
        print(f"[notifications] failed to record {kind} for business {business_id}: {e}",
              file=sys.stderr)
        try:
            session.rollback()
        except Exception:
            pass
        return None
```

…and add the imports `notifications.py` now needs, at the top of the file:

```python
import sys

from channels import get_channel
from db_models import Business, Job, OwnerNotification
```

- [ ] **Step 5: Run new tests, then the full suite**

Run: `cd agent && .venv/bin/python -m pytest tests/test_owner_notification_log.py tests/test_owner_notification.py -v`
Expected: 11 passed — 5 new, and the **6 pre-existing SMS tests still passing unmodified** (H3).

Run: `cd agent && .venv/bin/python -m pytest tests/ -q`
Expected: `345 passed`. No failures.

- [ ] **Step 6: Commit**

```bash
git add agent/db_models.py agent/notifications.py agent/tests/test_owner_notification_log.py
git commit -m "feat(notifications): durable owner-notification log

New OwnerNotification table plus record_owner_notification(), which is
best-effort and never raises into the alert path it describes. Carries a
delivered flag so a failed owner SMS is visible somewhere for the first
time — today it's swallowed by a bare except and invisible everywhere.

No call site is wired yet, and no notify_owner_* signature or return value
changed: the six existing SMS unit tests run with no database and pass
unmodified. New table needs no migration (create_all handles tables)."
```

---

## Task 2: Log the two booking paths

**Files:**
- Modify: `agent/service.py:117-118`
- Modify: `agent/xai_voice_adapter.py:135-146` (`_persist_job`)
- Test: `agent/tests/test_owner_notification_log.py` (append)

**Interfaces:**
- Consumes: `notifications.record_owner_notification`, `KIND_JOB_BOOKED`, `is_test_thread`, `build_owner_message`.
- Produces: no new public interface. Both booking paths now leave one `OwnerNotification` row per newly-created job.

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_owner_notification_log.py`:

```python
# --- the SMS booking path ----------------------------------------------------

from db_models import Business


class _SpyChannel:
    def __init__(self):
        self.sent = []

    def send(self, from_number, to_number, body):
        self.sent.append((from_number, to_number, body))


class _BoomChannel:
    def send(self, *_a, **_kw):
        raise RuntimeError("twilio down")


class _BookingAgent:
    def respond(self, client_config, history, tools=None, system_prompt=None, max_iters=None):
        return {
            "reply": "You're booked!",
            "jobs": [{"id": "t1", "input": {
                "service_type": "AC Repair", "urgency": "same_day", "customer_name": "Sarah",
            }}],
            "new_messages": [], "pending_tool_call": None,
        }


def _live_business(session, email):
    biz = Business(
        business_name="B", trade="hvac", email=email, frontdesk_live=True,
        trial_cap_cents=10000, escalation_phone="+15550001111",
        inbound_number="+15550002222",
    )
    session.add(biz)
    session.commit()
    session.refresh(biz)
    return biz


def test_sms_booking_records_a_delivered_notification(session, monkeypatch):
    import notifications
    import service
    monkeypatch.setattr(service, "agent", _BookingAgent())
    monkeypatch.setattr(notifications, "_owner_channel", _SpyChannel())
    biz = _live_business(session, "log1@test.io")

    service.handle_customer_message(session, biz, "+15557778888", "my AC is dead")

    rows = session.exec(select(OwnerNotification)).all()
    assert len(rows) == 1
    assert rows[0].kind == KIND_JOB_BOOKED
    assert rows[0].business_id == biz.id
    assert rows[0].delivered is True
    assert "AC Repair" in rows[0].message


def test_a_failed_owner_sms_still_records_an_undelivered_notification(session, monkeypatch):
    """H5: the whole reason the log exists. The SMS is gone, but the owner can
    still find out this job was booked."""
    import notifications
    import service
    monkeypatch.setattr(service, "agent", _BookingAgent())
    monkeypatch.setattr(notifications, "_owner_channel", _BoomChannel())
    biz = _live_business(session, "log2@test.io")

    service.handle_customer_message(session, biz, "+15557778888", "my AC is dead")

    rows = session.exec(select(OwnerNotification)).all()
    assert len(rows) == 1
    assert rows[0].delivered is False


def test_dashboard_test_bookings_are_not_logged(session, monkeypatch):
    """Mirrors the SMS skip exactly — the owner testing their own AI must not
    fill their notifications feed with fake activity."""
    import notifications
    import service
    monkeypatch.setattr(service, "agent", _BookingAgent())
    monkeypatch.setattr(notifications, "_owner_channel", _SpyChannel())
    biz = _live_business(session, "log3@test.io")

    service.handle_customer_message(session, biz, "portal-test", "testing my receptionist")

    assert session.exec(select(OwnerNotification)).all() == []


def test_a_repeat_turn_on_the_same_job_does_not_log_twice(session, monkeypatch):
    """Booking alerts are already deduplicated by book_job's `created` flag —
    a detail-merge re-runs the turn but must not produce a second row."""
    import notifications
    import service
    monkeypatch.setattr(service, "agent", _BookingAgent())
    monkeypatch.setattr(notifications, "_owner_channel", _SpyChannel())
    biz = _live_business(session, "log4@test.io")

    service.handle_customer_message(session, biz, "+15557778888", "my AC is dead")
    service.handle_customer_message(session, biz, "+15557778888", "it's also leaking")

    assert len(session.exec(select(OwnerNotification)).all()) == 1
```

- [ ] **Step 2: Run and confirm failure**

Run: `cd agent && .venv/bin/python -m pytest tests/test_owner_notification_log.py -q 2>&1 | tail -8`
Expected: the 4 new tests FAIL with `assert 0 == 1` / `assert [] == [...]` — no rows are being written yet. The 5 tests from Task 1 still pass.

- [ ] **Step 3: Wire the SMS path**

In `agent/service.py`, replace the notify loop (lines 117-118):

```python
    for job in newly_created:
        delivered = notify_owner_of_booking(client, job)
        if not is_test_thread(job.customer_phone):
            record_owner_notification(
                session, client.id, KIND_JOB_BOOKED,
                build_owner_message(job, "Frontdesk"), delivered,
            )
```

…and widen its import (line 16):

```python
from notifications import (
    KIND_JOB_BOOKED,
    build_owner_message,
    is_test_thread,
    notify_owner_of_booking,
    record_owner_notification,
)
```

- [ ] **Step 4: Wire the voice path**

In `agent/xai_voice_adapter.py`, in `_persist_job`, replace the `if created:` block:

```python
    if created:
        # The SMS send is a blocking HTTP call — offloaded so it can't stall
        # every other live call's audio on this process. The log write stays
        # on THIS thread with the session already in scope: a Session is not
        # thread-safe and must never cross the to_thread boundary.
        delivered = await asyncio.to_thread(notify_owner_of_booking, client, job)
        record_owner_notification(
            session, client.id, KIND_JOB_BOOKED,
            build_owner_message(job, "Frontdesk"), delivered,
        )
```

…and widen its import (line 44):

```python
from notifications import (
    KIND_JOB_BOOKED,
    build_owner_message,
    notify_owner_of_booking,
    notify_owner_of_escalation,
    record_owner_notification,
)
```

No `is_test_thread` guard is needed here: a voice call's thread is always
`xai-voice:{call_id}` (`_thread_id`), never a test thread.

- [ ] **Step 5: Run new tests, then the full suite**

Run: `cd agent && .venv/bin/python -m pytest tests/test_owner_notification_log.py -v`
Expected: 9 passed.

Run: `cd agent && .venv/bin/python -m pytest tests/ -q`
Expected: `349 passed`. No failures — in particular `test_idempotency.py` and `test_voice_loop_integration.py` must stay green, since they exercise `_persist_job`.

- [ ] **Step 6: Commit**

```bash
git add agent/service.py agent/xai_voice_adapter.py agent/tests/test_owner_notification_log.py
git commit -m "feat(notifications): log owner alerts for both booking paths

SMS and voice bookings now leave a durable row alongside the owner text,
carrying whether the text actually went out. Dashboard-test bookings are
skipped exactly as the SMS is, and the existing created-flag dedup means a
detail-merge doesn't log twice.

The log write deliberately stays on the calling thread rather than going
into asyncio.to_thread with the send: a Session is not thread-safe."
```

---

## Task 3: Log both escalation paths

**Files:**
- Modify: `agent/notifications.py` (extract `build_escalation_message`)
- Modify: `agent/xai_voice_adapter.py:183-184` and `:246-251`
- Test: `agent/tests/test_owner_notification_log.py` (append)

**Interfaces:**
- Produces: `notifications.build_escalation_message(business, caller_number, reason) -> str` — extracted so the SMS body and the logged message are the same string and cannot drift.

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_owner_notification_log.py`:

```python
# --- escalation paths --------------------------------------------------------

from notifications import KIND_CALL_DROPPED, build_escalation_message


def test_build_escalation_message_is_shared_by_the_sms_and_the_log(session):
    """One string, one source. If the SMS body and the logged message were
    built separately they would drift, and the owner's dashboard would show
    something subtly different from the text they actually got."""
    import notifications
    biz = _live_business(session, "esc0@test.io")
    spy = _SpyChannel()

    notifications.notify_owner_of_escalation(biz, "+15125559999", "gas smell", channel=spy)

    assert spy.sent[0][2] == build_escalation_message(biz, "+15125559999", "gas smell")


def test_a_failed_log_write_cannot_change_what_the_caller_is_told(session, monkeypatch):
    """H4 — THE critical guard in this phase.

    notify_owner_of_escalation's bool becomes "owner_alerted" vs
    "alert_failed" in the model's tool result, and engine.py instructs the
    voice agent to tell the caller plainly when the alert failed. If a
    notification-log failure could flip that bool, the AI would tell someone
    with a gas leak that the owner wasn't reached when in fact they were."""
    import notifications
    biz = _live_business(session, "esc1@test.io")
    monkeypatch.setattr(
        notifications, "record_owner_notification",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("log is down")),
    )

    # The send itself succeeds; only the log is broken.
    assert notifications.notify_owner_of_escalation(
        biz, "+15125559999", "gas smell", channel=_SpyChannel(),
    ) is True
```

Add to the same file, driving the adapter directly:

```python
def test_escalation_tool_records_a_notification(session, monkeypatch):
    import asyncio

    import xai_voice_adapter as adapter
    from engine import TRANSFER_CALL_TOOL

    biz = _live_business(session, "esc2@test.io")
    monkeypatch.setattr(adapter, "notify_owner_of_escalation", lambda *a, **k: True)

    class _WS:
        def __init__(self):
            self.sent = []

        async def send(self, payload):
            self.sent.append(payload)

    asyncio.run(adapter._handle_function_call(
        _WS(), session, biz, "xai-voice:c1", "+15125559999",
        {"name": TRANSFER_CALL_TOOL["name"], "call_id": "fc1",
         "arguments": json.dumps({"reason": "gas smell"})},
        adapter.CallTrace("c1"),
    ))

    rows = session.exec(
        select(OwnerNotification).where(OwnerNotification.kind == KIND_ESCALATION)
    ).all()
    assert len(rows) == 1
    assert rows[0].delivered is True
    assert "gas smell" in rows[0].message


def test_a_dropped_call_records_a_notification(session, monkeypatch):
    """H1 site 4: the crash handler. This fires exactly when the software
    failed the customer, which is the alert an owner most needs a durable
    record of."""
    import asyncio

    import xai_voice_adapter as adapter

    biz = _live_business(session, "esc3@test.io")
    monkeypatch.setattr(adapter, "notify_owner_of_escalation", lambda *a, **k: True)

    def _explode(*_a, **_kw):
        raise RuntimeError("websocket died")

    monkeypatch.setattr(adapter, "_run_call_session", _explode)

    asyncio.run(adapter.run_call(
        "c2", biz, "+15125559999", lambda: Session(session.get_bind()),
        trace=adapter.CallTrace("c2"),
    ))

    rows = session.exec(
        select(OwnerNotification).where(OwnerNotification.kind == KIND_CALL_DROPPED)
    ).all()
    assert len(rows) == 1
```

…and add the imports this file now needs at the top:

```python
import json

from sqlmodel import Session, select
```

- [ ] **Step 2: Run and confirm failure**

Run: `cd agent && .venv/bin/python -m pytest tests/test_owner_notification_log.py -q 2>&1 | tail -8`
Expected: the 4 new tests fail — first on `ImportError: cannot import name 'build_escalation_message'`. Fix the import by implementing Step 3, then re-run to see the remaining assertion failures before Step 4.

- [ ] **Step 3: Extract the escalation message builder**

In `agent/notifications.py`, pull the message out of `notify_owner_of_escalation` so both the send and the log use one string:

```python
def build_escalation_message(business: Business, caller_number: str, reason: str) -> str:
    return (
        f"URGENT — {business.business_name or 'your business'}: caller "
        f"{caller_number} needs you NOW. Reason: {reason}. "
        f"Call them back immediately."
    )
```

…and use it in `notify_owner_of_escalation`, replacing the inline f-string:

```python
    try:
        ch.send(
            business.inbound_number or "",
            business.escalation_phone,
            build_escalation_message(business, caller_number, reason),
        )
    except Exception:
        return False
    return True
```

This is a pure refactor — `test_voice_loop_integration.py:471` already asserts
the message content and must stay green unchanged.

- [ ] **Step 4: Wire both escalation sites**

In `agent/xai_voice_adapter.py`'s `_handle_function_call`, after the existing
`trace.stage(...)` line:

```python
        alerted = await asyncio.to_thread(notify_owner_of_escalation, client, caller_number, reason)
        trace.stage("owner_alerted" if alerted else "owner_alert_failed")
        record_owner_notification(
            session, client.id, KIND_ESCALATION,
            build_escalation_message(client, caller_number, reason), alerted,
        )
```

In `run_call`'s exception handler, replace the crash-alert block:

```python
        trace.stage("call_failed", error=repr(e))
        dropped_reason = "the AI call with this customer dropped mid-call — call them back"
        alerted = await asyncio.to_thread(
            notify_owner_of_escalation, client, caller_number, dropped_reason,
        )
        # Its own short-lived session: run_call's exception path has no open
        # one, and the crashed call's session is not safe to reuse.
        with session_factory() as session:
            record_owner_notification(
                session, client.id, KIND_CALL_DROPPED,
                build_escalation_message(client, caller_number, dropped_reason), alerted,
            )
```

…and widen the import to add `KIND_CALL_DROPPED`, `KIND_ESCALATION`, and
`build_escalation_message`.

- [ ] **Step 5: Run new tests, then the full suite**

Run: `cd agent && .venv/bin/python -m pytest tests/test_owner_notification_log.py -v`
Expected: 13 passed.

Run: `cd agent && .venv/bin/python -m pytest tests/ -q`
Expected: `353 passed`. `test_voice_loop_integration.py` must be green with **no test modified** — it asserts the escalation return value and message content, which this task must not change (H4).

- [ ] **Step 6: Commit**

```bash
git add agent/notifications.py agent/xai_voice_adapter.py agent/tests/test_owner_notification_log.py
git commit -m "feat(notifications): log both escalation paths

The alert_owner tool and the mid-call crash handler now leave durable
rows. The crash one matters most: it fires exactly when the software
failed the customer.

The escalation message is extracted to one builder so the SMS body and
the logged message can't drift, and a test asserts a broken log CANNOT
change notify_owner_of_escalation's return value — that bool decides
whether the voice agent tells an emergency caller help is coming."
```

---

## Task 4: `recent_notifications()` — the read seam Phase 5 needs

**Files:**
- Modify: `agent/notifications.py` (append)
- Test: `agent/tests/test_owner_notification_log.py` (append)

**Interfaces:**
- Produces: `notifications.recent_notifications(session, business_id: int, limit: int = 50) -> list[OwnerNotification]` — newest first, strictly scoped to one business.

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_owner_notification_log.py`:

```python
# --- reading the log ---------------------------------------------------------

from notifications import recent_notifications


def test_recent_notifications_returns_newest_first(session):
    for i in range(3):
        record_owner_notification(session, 1, KIND_JOB_BOOKED, f"job {i}", True)

    assert [n.message for n in recent_notifications(session, 1)] == ["job 2", "job 1", "job 0"]


def test_recent_notifications_never_leaks_another_business(session):
    """Business-scoped isolation is the security boundary throughout Roster
    (platform PRD §12). One business must never see another's alerts."""
    record_owner_notification(session, 1, KIND_JOB_BOOKED, "mine", True)
    record_owner_notification(session, 2, KIND_JOB_BOOKED, "theirs", True)

    assert [n.message for n in recent_notifications(session, 1)] == ["mine"]


def test_recent_notifications_respects_the_limit(session):
    for i in range(5):
        record_owner_notification(session, 1, KIND_JOB_BOOKED, f"job {i}", True)

    assert len(recent_notifications(session, 1, limit=2)) == 2


def test_recent_notifications_on_an_empty_log(session):
    assert recent_notifications(session, 1) == []
```

- [ ] **Step 2: Run and confirm failure**

Run: `cd agent && .venv/bin/python -m pytest tests/test_owner_notification_log.py -q 2>&1 | tail -5`
Expected: `ImportError: cannot import name 'recent_notifications'`.

- [ ] **Step 3: Implement**

Append to `agent/notifications.py`:

```python
def recent_notifications(session, business_id: int, limit: int = 50):
    """This business's owner alerts, newest first — what the dashboard's
    Notifications page renders (blueprint §4).

    Scoped to one business_id, which is the security boundary everywhere in
    Roster (platform PRD §12): no query may cross businesses.
    """
    from sqlmodel import select

    return list(session.exec(
        select(OwnerNotification)
        .where(OwnerNotification.business_id == business_id)
        .order_by(OwnerNotification.id.desc())
        .limit(limit)
    ).all())
```

- [ ] **Step 4: Run new tests, then the full suite**

Run: `cd agent && .venv/bin/python -m pytest tests/test_owner_notification_log.py -v`
Expected: 17 passed.

Run: `cd agent && .venv/bin/python -m pytest tests/ -q`
Expected: `357 passed`. No failures.

- [ ] **Step 5: Commit**

```bash
git add agent/notifications.py agent/tests/test_owner_notification_log.py
git commit -m "feat(notifications): read the owner-notification log

recent_notifications() returns one business's alerts newest-first, with a
test asserting it never returns another business's rows — business-scoped
isolation is the security boundary throughout Roster. This is the seam
Phase 5's Notifications page reads."
```

---

## Phase 2 Acceptance Criteria

1. **Full suite green at 359 passed**, up from Phase 1's 340 — 19 new tests, **zero pre-existing tests modified**. (Two more than originally planned: adding `source` surfaced that the voice-booking path had no direct assertion of its own, only indirect coverage via existing tests staying green.)
1b. **Every notification carries a `source`** — one test per site asserts its
   exact `source` constant, so an origin can never be inferred by parsing
   `message`.
2. **The six SMS unit tests in `test_owner_notification.py` still run with no database** and pass unmodified (H3).
3. **No `notify_owner_of_*` signature or return value changed** — verified by `git diff main -- agent/notifications.py` showing only additions plus the `build_escalation_message` extraction, and by `test_voice_loop_integration.py` passing unmodified (H4).
4. **All four alert sites log** — one test each for SMS booking, voice booking, the `alert_owner` tool, and the mid-call crash handler (H1).
5. **A broken log cannot affect an alert** — the H4 guard test passes, and a failed write returns `None` without raising.
6. **No migration code added** — `git diff main -- agent/db.py` is empty; `create_all` handles the new table.
7. **Test-thread bookings produce no rows**, and a detail-merge produces no second row.
8. **Four commits**, one per task, each green independently.
9. **App still boots** — `cd agent && .venv/bin/python -c "import app"` exits 0.

**Deliberately NOT in this phase:** no UI, no route, no `read_at` producer (Phase 5 owns marking-as-read), no "department went live" notifications (no producer until Phase 4/6), no retry or dead-letter for failed sends (the log makes failure *visible*, which is the prerequisite for deciding whether retry is worth building), and no migration of the founder's trial-cap alert into this table — that one is Roster's internal billing alert, not the customer's.
