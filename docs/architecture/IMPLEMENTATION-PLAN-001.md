# IMPLEMENTATION-PLAN-001 — Event substrate and Decision record

**Version:** 1.1
**Status:** Approved 2026-08-02 — PR 1 in progress
**Baseline:** tag `architecture-baseline` (commit `65c9176`), 801 tests green
**Source:** `GAP-ANALYSIS-2026-08-01.md` steps 0, 1, 2
**Owner:** Founding Team

---

## Scope

Three changes, in strict order:

1. **E0 — EventBus binding fix.** Remove the import-time engine binding that makes the bus
   unusable from live code.
2. **E1 — Event publishing.** Publish `JOB_BOOKED` and `JOB_COMPLETED` from the chokepoints that
   already exist.
3. **D1 — Decision table.** A post-hoc audit record written at the branch points that already
   exist, delivering the explainability 000 and 002 require without restructuring anything.

## Non-goals

Explicitly **not** in this plan. Naming them is part of the plan:

- No Decision **Runtime**. No orchestrator, no `decide()`, no lifecycle refactor. That is D2, and it
  is gated on this plan's Decision rows existing to verify a refactor against.
- No Policy Engine, no Authority Engine, no evidence collection. Those are separate plans.
- No `evidence`, `policy_version`, or `authority_result` columns. Nothing produces those values yet;
  a column that is always `NULL` is a lie in table form. They are added later as one-line entries in
  `db._migrate_add_columns`, which is this codebase's proven mechanism for exactly that.
- No new event types beyond the two already declared in `events.py`. With zero subscribers, every
  additional type is speculative.
- No routing of `notifications.py` through the bus. Its "exactly one subscriber today" reasoning is
  correct and stays.

## One design decision, stated up front

**The v1 `Decision` row is a post-hoc audit record, not a lifecycle object.** It is written *after*
the outcome is known, at the branch point where the code already decides what happened. 002
describes a Decision that is created at stage 1 (Intent) and mutated through to stage 11. That is
D2's job.

This matters because someone reading 002 and then reading the code must not conclude the record is
broken. The model docstring will say this explicitly.

---

## Architecture traceability

Every PR traced to the documents it serves. A row that says *does not satisfy* is as important as
one that says *implements* — it is what stops a merged PR from being read as closing a gap it
doesn't close.

### PR 1 — EventBus binding fix

| Document | Reference | Relationship |
|---|---|---|
| 001 | §Replaceability — *"must support replacing … databases, queues"* | **Fixes a direct violation.** An engine bound at import time is precisely the coupling this section forbids. |
| 001 | §Event Platform | **Enables.** Publishes nothing itself; removes the reason nothing can. |
| 000 | Law #5 — *Every important action is observable* | Enables |
| 000 | Law #8 — *History is immutable* | Preserves — the append-only `Event` table is untouched |
| 002 | Stage 10 (Audit) | Enables |
| GAP | Step 0 | Implements in full |

**Does not satisfy:** anything. This PR makes an existing capability reachable. No architectural
claim becomes true because of it.

### PR 2 — Publish JOB_BOOKED / JOB_COMPLETED

| Document | Reference | Relationship |
|---|---|---|
| 001 | §Event Platform — *"Every significant action generates an immutable event"* | **Partially implements** — 2 of the 14 event types declared in `events.py` |
| 001 | §Canonical Data Flow — `Business Operation → Event Platform` | **Implements this hop** for the booking path |
| 001 | §Platform Invariants — *"Events are immutable"* | Satisfies — no update path exists |
| 000 | Law #5, Law #8 | Advances |
| 002 | Stage 10 (Audit) | Partially — records that an action occurred, not why |
| 003 | *"Events are append-only"*; `Event → Event Platform` | Satisfies both |
| GAP | Step 1 | Implements in full |

**Does not satisfy:** 001 §Event Platform in full — `quote.sent`, `customer.dormant`,
`membership.renewal_due`, `review.requested`, `referral.received` and 7 others remain unpublished.
No subscriber exists, so nothing yet *reads* history.

### PR 3 — Decision table

| Document | Reference | Relationship |
|---|---|---|
| 002 | §Decision Object | **Partially implements** — 9 of 16 fields. Omits `evidence`, `policy_version`, `authority_result`, `workflow_id`, `conversation_id`, `events`, `completed_at` |
| 002 | §Completion — the six terminal states | **Adopts the vocabulary verbatim** |
| 002 | §Decision Invariants | Satisfies *Observable* and *Explainable*. **Does not** satisfy *Verified*, *Authorized*, *Replayable*, *Recoverable* |
| 000 | Definition of Done — *Observable*, *Explainable* | Advances both |
| 000 | Law #5 | Advances |
| 003 | Core Entities: `Decision` | **Creates the entity** |
| 003 | Ownership: `Decision → Decision Runtime` | **Partially** — the entity exists; its stated owner does not |
| GAP | Step 2 | Implements in full |

**Does not satisfy:** 001 Layer 3 (Decision Runtime) in any part, and therefore not 001's invariant
*"Every business action flows through the Decision Runtime"* nor 002's *"No business operation may
bypass this lifecycle."* Both remain false after this plan completes. That is D2.

### Untouched by this plan

001 §Policy Engine, §Authority Engine, §Knowledge Platform; 002 stages 3/5/6; 003's `Contact`,
`Property`, `Equipment`, `Workflow`, `Policy` entities; 000 Law #4 (*policies live outside prompts*),
which remains violated by `engine.py:259-262` and `:295-298`.

---

## Sequencing

Three PRs, merged in order. Each is independently revertable and each leaves `main` green.

| PR | Title | Depends on | Net size |
|---|---|---|---|
| 1 | `fix(eventbus): take a session instead of binding an engine at import` | — | ~15 lines, 2 files |
| 2 | `feat(events): publish JOB_BOOKED and JOB_COMPLETED` | PR 1 | ~75 lines, 3 files |
| 3 | `feat(decisions): post-hoc decision audit record` | PR 2 | ~170 lines, 8 files |

Why not one PR: PR 1 touches a money path's dependency, PR 2 touches the money path itself, PR 3
adds a table. Bisecting a regression across all three at once would be unpleasant, and PR 1 is
small enough to review in two minutes.

---

# PR 1 — EventBus binding fix

### The defect

`EventBus.__init__` resolves `db.engine` at import time
([eventbus.py:12-13](../../agent/eventbus.py)):

```python
def __init__(self, engine=None):
    from db import engine as _default
    self._engine = engine or _default
```

This is why `runner.py:12-16` documents refusing to use the `bus` singleton — "a live route wired
to the global `bus` would write real Event rows into whatever database db.py resolves to on import"
— and why every test constructs its own `EventBus(test_engine)`. It is the single reason a fully
built, tested Event Platform has zero publishers.

### The fix

`publish` takes a `session`, matching every other service in this codebase
(`recovery_service`, `review_service`, `referral_service`, `expansion`, `deployment`,
`notifications` all take a session). `EventBus` then holds only subscribers and needs no engine at
all.

```python
class EventBus:
    def __init__(self) -> None:
        self._subscribers: Dict[str, List[Callable[[DomainEvent], None]]] = defaultdict(list)

    def subscribe(self, event_type: str, handler) -> None: ...

    def publish(self, session, event: DomainEvent) -> bool: ...
```

### Existing files that change

| File | Change |
|---|---|
| `agent/eventbus.py` | Drop `engine` from `__init__`; `publish(self, session, event)`; use the caller's session instead of opening its own. ~10 net lines. |
| `agent/tests/test_eventbus.py` | `EventBus(test_engine)` → `EventBus()`; `bus.publish(evt)` → `bus.publish(s, evt)`. 2 tests. |

### Existing tests

- `tests/test_eventbus.py` (2 tests) — **must be updated**, they pin the current signature.
- `tests/test_events.py` (1 test) — unaffected, tests `DomainEvent` only.
- `tests/test_event_model.py` (1 test) — unaffected, tests the table directly.
- No other test imports `eventbus`. Verified by grep.

### Migration strategy

None. No schema change, no data change. The only caller of `publish` today is the test suite.

### Rollback strategy

`git revert`. Nothing outside `eventbus.py` and its own test file depends on the signature.

### Risk assessment

**Very low.** The changed function has zero production callers — that is the entire problem being
fixed. Worst case is a broken test file, caught before merge.

One thing to get right, and it is the whole reason this PR exists separately: **`publish` must not
leave the caller's session in a broken state.** On `IntegrityError` (a duplicate `dedup_key`) it
must `session.rollback()` and return `False`, exactly as it does today. PR 2 depends on this being
correct, and PR 2's call site is a money path.

### PR size estimate

**~15 net lines, 2 files.** Two-minute review.

### Acceptance criteria

- [ ] `EventBus()` constructs with no arguments and imports no engine.
- [ ] `grep -n "from db import" agent/eventbus.py` returns nothing.
- [ ] `publish(session, event)` returns `True` on insert, `False` on duplicate `dedup_key`.
- [ ] After a duplicate-key `publish`, the caller's session is still usable — a following
      `session.add(...) + commit()` succeeds. **New test**, this is the property PR 2 relies on.
- [ ] Subscribers still fire synchronously, and only on a `True` return.
- [ ] Full suite green: 801 tests (2 rewritten, 1 added → 802).

### Success metrics

| Metric | Target | How measured |
|---|---|---|
| Test suite | 802 passed, 0 failed | `pytest -q` |
| Engine coupling removed | 0 hits | `grep -c "from db import" agent/eventbus.py` |
| Blast radius | exactly 2 files | `git diff --stat architecture-baseline` |
| Module size | ≤ current 38 lines | `wc -l agent/eventbus.py` |
| Production behaviour change | none | no non-test file outside `eventbus.py` in the diff |

### Abort conditions

Stop and re-plan — do not work around — if any of these occur:

- **A production caller of `publish` or `EventBus(...)` turns up.** The premise of this PR is that
  there are none. If one exists, this is a behaviour change, not a refactor, and needs its own risk
  assessment.
- **The session-poisoning test cannot be made to pass** — i.e. a duplicate-key `publish` leaves the
  caller's session unusable. PR 2 puts this on the booking path; if the guarantee can't be
  established here, PR 2 must not proceed.
- **Any test outside `tests/test_eventbus.py` fails.** Nothing else touches this module; a failure
  elsewhere means the dependency graph is not what this plan assumed.
- **The diff exceeds ~30 lines.** That signals the fix is entangled with something unmodelled.

---

# PR 2 — Publish JOB_BOOKED and JOB_COMPLETED

### Where

Two chokepoints that already exist and already carry an explicit "did this actually happen" signal.
No new branching logic is introduced.

| Site | Signal already present | Event |
|---|---|---|
| `bookings.book_job` ([bookings.py:25](../../agent/bookings.py)) | returns `(job, created)` | `JOB_BOOKED` when `created is True` |
| `app.complete_job` ([app.py:589](../../agent/app.py)) | `already_completed` local | `JOB_COMPLETED` when `not already_completed` |

Publishing **inside `book_job`** rather than at its three callers is deliberate: `service.py`,
`xai_voice_adapter.py` and `recovery_service.py` all route through it, so one call site covers every
booking path and there is no fourth caller to forget later.

### The ordering rule that makes this safe

`book_job` **already commits** the job before returning ([bookings.py:64, :83](../../agent/bookings.py)).
Publish strictly **after** that commit. A failed event insert then rolls back only the event — the
booking is already durable.

This is not a new convention. `notifications.record_owner_notification` states the same rule in its
own docstring: *"Call this only AFTER the caller's primary work is committed."* PR 2 follows the
precedent rather than inventing one.

### Dedup keys

`Event.dedup_key` is **globally** unique, not per-business. `Job.id` is a global primary key, so:

- `f"job.booked:{job.id}"`
- `f"job.completed:{job.id}"`

Same convention as `WebhookDelivery`'s `"twilio-sms:{sid}"` / `"xai-call:{id}"`.

### Existing files that change

| File | Change |
|---|---|
| `agent/bookings.py` | Import `bus` + `JOB_BOOKED`; publish after the create-branch commit. ~8 lines. |
| `agent/app.py` | Publish `JOB_COMPLETED` in `complete_job` under the existing `if not already_completed`. ~6 lines. |
| `agent/tests/test_event_publishing.py` | **New.** ~60 lines. |

### Existing tests

Every test that books a job now also writes an Event row. None of them assert on Event contents, so
none should break — but these are the files that exercise the changed paths and must be re-run
deliberately, not just as part of the suite:

- `tests/test_idempotency.py` (17 tests) — the closest thing to a contract test for `book_job`.
  A re-book that merges must **not** publish a second event.
- `tests/test_voice_loop_integration.py` (79 tests) — voice bookings through `_persist_job`.
- `tests/test_recovery_service.py` (37 tests) — Recovery's `confirm_slot` booking.
- `tests/test_owner_notification_log.py` (14 tests) — same post-commit region.
- `tests/test_recovery_endpoint.py` (15 tests) — the `/complete` route.

### New tests (`tests/test_event_publishing.py`)

1. A new booking publishes exactly one `JOB_BOOKED` with `business_id`, `customer_id` and the job id
   in the payload.
2. A merge re-book (same thread, same service, inside the 24h window) publishes **nothing** — this
   is the one that protects the money path's idempotency guarantee.
3. Marking a job done publishes exactly one `JOB_COMPLETED`; marking it done twice publishes one.
4. A failed publish does not roll back the booking: monkeypatch `bus.publish` to raise, assert the
   `Job` row still exists and `book_job` still returned normally.
5. Zero subscribers registered → publishing is still a no-op for behaviour.

### Migration strategy

**None required.** The `event` table already exists in every deployed database — it is created by
`SQLModel.metadata.create_all` in `db._init_db_locked` and has been part of the schema since the
platform-foundation work. No `_migrate_add_columns` entry, no backfill.

Historical jobs are **not** backfilled into events. An event means "this happened, and we observed
it happening"; manufacturing events for past rows would be inventing history, which 000 Law #8
forbids.

### Rollback strategy

`git revert`. Event rows already written become orphaned but are harmless: nothing reads them, and
`app.delete_client` already deletes from `event` ([app.py:738](../../agent/app.py)).

No data cleanup is needed on revert. If cleanup is wanted anyway:
`DELETE FROM event WHERE type IN ('job.booked','job.completed')`.

### Risk assessment

**Low, with one genuine hazard.**

| Risk | Severity | Mitigation |
|---|---|---|
| An exception in `publish` breaks a booking | **High if it happened** | Publish after the commit (booking already durable) + wrap in try/except that logs loudly and swallows, same posture as `record_owner_notification`. Test #4 pins it. |
| A merge re-book publishes a duplicate event | Medium | Publish only on `created is True`; `dedup_key` is a second line of defence. Test #2 pins it. |
| A future slow subscriber runs inside the booking path | Medium, **not today** | Zero subscribers exist. `eventbus.publish` dispatches synchronously in-process; this is a real ceiling and is already documented in the module. Add a `ponytail:` comment naming it, do not build async dispatch for a subscriber that does not exist. |
| Extra INSERT per booking | Negligible | One row, post-commit, unindexed write. |

The hazard is entirely "an audit side-effect must never damage the thing it audits." Two independent
mitigations (post-commit ordering, swallowed exceptions) plus a test.

### PR size estimate

**~75 net lines, 3 files** (2 modified, 1 new test file). Small — but review the `bookings.py` diff
carefully, it is the product's most correctness-critical function.

### Acceptance criteria

- [ ] A new booking via SMS, voice, and Recovery each produce exactly one `job.booked` event.
- [ ] A detail-merge re-book produces zero events.
- [ ] `complete_job` produces exactly one `job.completed`; a second POST produces zero.
- [ ] `bus.publish` raising does not fail the booking and does not lose the `Job` row.
- [ ] `events.py` is unchanged — no new constants.
- [ ] `notifications.py` is unchanged — the direct owner SMS still fires as it does today.
- [ ] Full suite green: 802 + 5 new = 807 tests.

### Success metrics

| Metric | Target | How measured |
|---|---|---|
| Test suite | 807 passed, 0 failed | `pytest -q` |
| Events per new booking | exactly 1 | new test + manual smoke via the dashboard chat, then `SELECT * FROM event` |
| Events per merge re-book | exactly 0 | `test_event_publishing.py` #2 |
| Events per double `/complete` | exactly 1 | `test_event_publishing.py` #3 |
| Booking survives a failing publish | job row present | `test_event_publishing.py` #4 |
| `events.py` untouched | 0 lines changed | `git diff --stat` |
| No latency regression | suite wall time within ~10% of the 41s baseline | `pytest -q` timing |

### Abort conditions

- **Any existing booking test fails for a reason other than a trivially-updated assertion.**
  `book_job` is the product's most correctness-critical function; a non-obvious failure there means
  stop, not patch.
- **Event rows appear for merge re-books.** The idempotency guarantee has leaked into the event
  stream; the publish site is wrong.
- **The "publish raises → booking survives" test cannot be made to pass.** The whole safety argument
  of this plan rests on it. Without it, revert to PR 1 and reconsider whether publishing belongs
  inside `book_job` at all rather than in a post-commit hook at each caller.
- **Publishing requires moving `book_job`'s commit.** That would make this a change to the booking
  transaction, which is out of scope by an order of magnitude.
- **A subscriber gets registered during this PR.** Zero subscribers is what makes synchronous
  in-process dispatch safe today; adding one changes the risk model.

---

# PR 3 — Decision table

### What it is

A post-hoc audit record answering *"what did an AI employee decide, on what basis, and what
happened?"* — the explainability requirement in 000 (Definition of Done: *Explainable*) and 002
(Decision Invariants: *Observable, Explainable*).

### Model

```python
class Decision(SQLModel, table=True):
    """A post-hoc audit record of one AI-employee decision.

    NOT the lifecycle object 002 describes: this row is written AFTER the
    outcome is known, at the branch point where the code already decided what
    happened. Creating a Decision at Intent and mutating it through the 11
    stages is the Decision Runtime's job (see IMPLEMENTATION-PLAN-001 D2);
    this table is what that refactor will be verified against.

    Deliberately omits 002's `evidence`, `policy_version` and
    `authority_result`: nothing produces those values yet, and a column that
    is always NULL claims a capability the platform doesn't have. Each is one
    line in db._migrate_add_columns when its producer ships.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    business_id: int = Field(foreign_key="business.id", index=True)
    customer_id: Optional[int] = Field(default=None, foreign_key="customer.id")
    job_id: Optional[int] = Field(default=None, foreign_key="job.id")
    thread: str = ""            # customer_phone / xai-voice:{call_id}
    employee_role: str          # employees.REGISTRY key — who decided
    intent: str                 # book_job | escalate | confirm_slot | ...
    status: str                 # completed|rejected|failed|escalated|cancelled|expired
    proposal_json: str = "{}"   # the model's tool input, verbatim
    outcome: str = ""           # one short line, human-readable
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

`status` reuses 002's Completion vocabulary verbatim, which is already almost exactly
`RecoveryJob.current_status`. No new vocabulary is invented.

No `completed_at`: the row is written once, terminal, at a single instant. Two timestamps with the
same value would be a lie about what the record is. D2 adds it when the row genuinely spans time.

### Writer

A new `agent/decisions.py`, ~40 lines, one function, modelled directly on
`notifications.record_owner_notification`:

```python
def record(session, *, business_id, employee_role, intent, status, ...) -> Optional[Decision]:
    """Persist a decision record. Best-effort — never raises into the caller:
    the decision this describes has ALREADY happened and matters far more than
    its record. Loud on failure (print to stderr), because a lost decision row
    is invisible everywhere else. Call only AFTER the caller's primary work is
    committed — this commits the session.
    """
```

Same contract, same posture, same warnings as the module it mirrors. Nothing new to learn.

### Call sites in this PR — the booking path only

Three sites. All three are places the code **already branches** on the outcome:

| # | File | Site | intent / status |
|---|---|---|---|
| 1 | `service.py` | after the `book_job` loop, per newly-created job | `book_job` / `completed` |
| 2 | `xai_voice_adapter.py` | `_handle_function_call`, `log_job` branch | `book_job` / `completed` |
| 3 | `recovery_service.py` | `handle_recovery_reply`, `confirm_slot` branch | `confirm_slot` / `completed` |

**Deferred to PR 4** (same helper, no new mechanism): `alert_owner` escalation, Recovery
`record_response` decline, Recovery `_escalate`, review-reply outcome, referral capture. Five more
sites. Splitting keeps this PR reviewable and gets the money path instrumented first.

### The cascade — do not miss this

`app.delete_client` ([app.py:718-751](../../agent/app.py)) deletes children in explicit dependency
order because **SQLite enforces no foreign keys here** (`db.py` sets no `PRAGMA foreign_keys=ON`), so
a missed table orphans rows silently. `tests/test_delete_client.py` is an exact-cascade test that
asserts each table is empty afterwards.

`Decision` **must** be added to both, in the same PR. This exact class of bug already happened once —
the test's own comment records it: *"the cascade predated both of these tables… so the orphans they
left behind were silent."*

### Existing files that change

| File | Change |
|---|---|
| `agent/db_models.py` | New `Decision` model. ~25 lines. |
| `agent/decisions.py` | **New.** ~40 lines. |
| `agent/service.py` | One `decisions.record(...)` after the booking loop. ~4 lines. |
| `agent/xai_voice_adapter.py` | One call in the `log_job` branch. ~5 lines. |
| `agent/recovery_service.py` | One call in the `confirm_slot` branch. ~4 lines. |
| `agent/app.py` | `delete Decision` in `delete_client`, in dependency order. ~2 lines. |
| `agent/tests/test_delete_client.py` | Seed a `Decision`; assert it is gone. ~3 lines. |
| `agent/tests/test_decisions.py` | **New.** ~90 lines. |

### Existing tests

- `tests/test_delete_client.py` (5 tests) — **must be updated**. Exact-cascade assertions.
- `tests/test_idempotency.py` (17) — a merge re-book must write **no** Decision row.
- `tests/test_voice_loop_integration.py` (79) — voice `log_job` path.
- `tests/test_recovery_service.py` (37) — `confirm_slot` path.
- `tests/test_public_surface.py` (7) — re-run; it asserts what is publicly reachable.

### New tests (`tests/test_decisions.py`)

1. An SMS booking writes exactly one Decision: correct `business_id`, `employee_role="frontdesk"`,
   `intent="book_job"`, `status="completed"`, `job_id` set, proposal round-trips.
2. A voice booking writes one Decision with the `xai-voice:` thread.
3. A Recovery `confirm_slot` writes one with `employee_role="quote_chaser"`.
4. A merge re-book writes **zero**.
5. `decisions.record` failing (monkeypatched to raise inside) does not break the booking and does
   not lose the `Job` — the best-effort contract.
6. Decision rows are business-scoped; a query for business A returns nothing belonging to B.

### Migration strategy

**A new table needs no migration.** `db._init_db_locked` calls `SQLModel.metadata.create_all(engine)`
([db.py:89](../../agent/db.py)), which creates missing *tables* on both fresh and existing databases.
`_migrate_add_columns` exists only because `create_all` cannot add a *column* to an existing table —
not relevant here.

Concretely:
- Fresh database: created by `create_all`.
- Existing SQLite volume: created by `create_all` on next boot.
- Postgres: same, serialized behind the existing `pg_advisory_lock` in `init_db`.

**No backfill.** Past bookings have no recorded decision, and manufacturing one would be inventing
an audit trail — the exact thing 000 Law #8 ("History is immutable") exists to prevent. The table
starts empty and is honest about it.

### Rollback strategy

Two layers:

1. **Revert the PR.** The writes are additive and best-effort; removing them changes no behaviour.
   The `decision` table remains in the schema, empty and unread. This is safe to leave — SQLModel
   never drops tables, and an orphaned empty table costs nothing.
2. **If the table itself must go** (it should not need to): `DROP TABLE decision`, after the revert
   is deployed. Never before — a running instance still referencing the model would error on write.

**Do not roll back PR 3 without also reverting the `delete_client` change**, or the cascade will
reference a model that is no longer imported.

Return point for all three PRs: `git checkout architecture-baseline`.

### Risk assessment

| Risk | Severity | Mitigation |
|---|---|---|
| `decisions.record` breaks a booking | **High if it happened** | Best-effort contract copied verbatim from `record_owner_notification` — never raises, logs loudly, called only post-commit. Test #5 pins it. |
| **Missed `delete_client` cascade → silent orphans** | **Medium, and precedented** | Explicit PR line item + assertion added to the existing exact-cascade test. This has bitten this codebase before (audit F7). |
| `record` commits mid-transaction and flushes a caller's in-flight work | Medium | Same hazard `record_owner_notification` already carries and documents. All three call sites are post-commit; the docstring repeats the warning. |
| Decision rows drift from what actually happened | Medium | Three call sites only, each at an existing branch. PR 4's five sites are deferred precisely to keep this small enough to verify by reading. |
| Table grows unboundedly | Low | One row per booking. At 10 customers this is single-digit rows/day. Revisit at ~1M rows, not before. |
| Someone reads the table and expects 002's lifecycle semantics | Medium | The model docstring says what it is and is not, in the first paragraph. |

### PR size estimate

**~170 net lines, 8 files** (6 modified, 2 new). The largest of the three. If review feels heavy,
split the three call sites into their own follow-up and land the table + helper + cascade first —
that half is mechanical.

### Acceptance criteria

- [ ] `Decision` table is created on a fresh database and on an existing `roster.db` with no manual
      step, verified by booting against a copy of the existing volume.
- [ ] An SMS, a voice, and a Recovery booking each write exactly one Decision row.
- [ ] A detail-merge re-book writes zero Decision rows.
- [ ] `decisions.record` raising does not fail the booking and does not lose the `Job`.
- [ ] `delete_client` removes Decision rows; `test_delete_client.py` asserts it.
- [ ] The model docstring states it is a post-hoc audit record, not 002's lifecycle object, and
      names the three deliberately-omitted columns.
- [ ] No new dependency, no new config, no new env var.
- [ ] Full suite green: 807 + 6 new = 813 tests.

### Success metrics

| Metric | Target | How measured |
|---|---|---|
| Test suite | 813 passed, 0 failed | `pytest -q` |
| Decisions per booking (SMS / voice / Recovery) | exactly 1 each | `test_decisions.py` #1–#3 |
| Decisions per merge re-book | exactly 0 | `test_decisions.py` #4 |
| Booking survives a failing `record` | job row present | `test_decisions.py` #5 |
| Table creation on an existing volume | table present, no manual step | boot against a **copy** of the production `roster.db` |
| Cascade completeness | 0 orphan rows after delete | `test_delete_client.py` |
| Attribution | every row has a non-empty `employee_role` | DB check after the smoke test |
| New dependencies | 0 | `git diff agent/requirements*.txt` empty |

### Abort conditions

- **`create_all` does not create the table on a copy of the existing database.** The migration
  premise of this PR is wrong; it needs a real DDL migration and its own risk assessment. Test this
  against a copy **before** writing any call sites.
- **Any of the three call sites needs restructuring to accept a Decision write.** That is D2's work
  leaking into D1. Drop that call site from this PR rather than restructuring.
- **A decision cannot be attributed to a single `employee_role`.** A row that can't say who decided
  fails the explainability purpose the table exists for — better absent than misleading.
- **The `delete_client` cascade cannot be verified by test.** Silent orphans are the precedented
  failure here (audit F7); shipping without the assertion repeats a known bug.
- **`decisions.record` needs to raise into a caller to be correct.** The best-effort contract is
  non-negotiable on a money path; if the record genuinely needs transactional guarantees, that is a
  different design.
- **Diff exceeds ~250 lines.** Split the table + helper + cascade from the call sites and land them
  separately.

---

## Cross-cutting

### Combined risk posture

Every change in this plan is **additive and observational**. Nothing changes what the product does
for a customer: no prompt changes, no new decisions, no altered routing, no schema change to an
existing table, no behaviour gated on any of it.

The entire risk surface reduces to one sentence: **an audit side-effect must never damage the thing
it audits.** Three independent mitigations are applied at every write:

1. Ordering — always after the primary work has committed.
2. Isolation — exceptions swallowed and logged, never raised into the caller.
3. Tests — a "the recorder blows up, the booking survives" test in both PR 2 and PR 3.

### What this plan deliberately does not fix

Named so they are not mistaken for oversights. All are from the gap analysis and all are separate
plans:

- Hours / pricing / service area enforced only in prompts (the largest constitution violation).
- The `awaiting_slot` expiry bug in `recovery_service.tick`.
- No Authority Engine beyond deployment-level.
- Reviews' send-then-mark vs Recovery's claim-then-release retry inconsistency.

### Verification before each merge

```bash
cd agent && .venv/bin/python -m pytest -q
```

Baseline is **801 passed**. Expected after each PR: 802, 807, 813.

### Open decisions for the owner

1. **`employee_role` values.** The plan uses `employees.REGISTRY` keys (`frontdesk`,
   `quote_chaser`). Recovery's booking is attributed to `quote_chaser` because that is the name the
   owner already sees in the SMS (`notify_owner_of_booking(..., employee_name="Quote Chaser")`).
   Confirm that attribution is what you want in the audit trail.
2. **PR 4 scope.** Five remaining Decision call sites (escalation, decline, Recovery escalate,
   review reply, referral). Ship as one PR after PR 3, or fold into whichever feature next touches
   those handlers?
3. **001/002's false present-tense invariants.** This plan does not soften them. After PR 3, *"every
   business action flows through the Decision Runtime"* is still false. Mark those specific lines
   *not yet implemented*, or leave them until D2 lands?
