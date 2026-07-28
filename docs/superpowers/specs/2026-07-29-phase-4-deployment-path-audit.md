# Phase 4 Pre-Plan Audit — Every Deployment Path & the Invariants to Preserve

**Date:** 2026-07-29
**Status:** Audit only. No code written. Feeds the Phase 4 task plan, which is
not yet written.
**Scope:** every path that creates, backfills, updates, or removes `Employee`
rows; every `requested_roster` writer; and the invariants Phase 4 must hold
while replacing `requested_roster` with real `Employee` rows.

---

## 0. The headline

**`Employee` rows are effectively write-only today.** Nothing in any route,
template, or engine reads them to make a decision or render anything — the
only two production readers are internal bookkeeping (`portal.py:55`'s
existence check and `db.py:196`'s backfill check).

That single fact explains why every defect below has been harmless so far, and
why they all become visible at once in Phase 4/5: **Phase 4 makes `Employee`
rows load-bearing for the first time.**

---

## 1. Complete inventory

### Every `Employee` writer (production code — 3 total)

| # | Location | When | Creates |
|---|---|---|---|
| W1 | `db.py:198` (`_backfill_employees`) | **App boot only** | `frontdesk`, for any business with `frontdesk_live=True` |
| W2 | `db.py:205` (`_backfill_employees`) | **App boot only** | One per `requested_roster` entry, via `role_key_for()` |
| W3 | `portal.py:58` (`_hire_employee`) | Customer clicks "Hire" | The hired role — **the only non-boot writer that exists** |

W3 is deleted in Phase 7. **After that, if Phase 4 adds nothing, the only way
an `Employee` row is ever created is an application restart.**

### Every `Employee` reader (production code — 2 total)

| Location | Purpose |
|---|---|
| `portal.py:55` | "Does this role already exist?" inside `_hire_employee` |
| `db.py:196` | "What does this business already have?" inside the backfill |

Neither renders anything. No template, route, or engine consults `Employee`.

### Every `Employee` deleter (1)

`app.py:504` — `delete_client`'s cascade. Correct today; must gain
`DepartmentInterest` and `OwnerNotification` in Phase 4 (see F7).

### Every `requested_roster` writer (3)

| # | Location | Writes | Format |
|---|---|---|---|
| R1 | `portal.py:427` (`roster_hire`) | Customer self-serve hire | **Display name** — `"Quote Chaser"` |
| R2 | `portal.py:462` (`roster_hire_retention_manager`) | Customer self-serve hire | **Display name** — `"Retention Manager"` |
| R3 | `app.py:471` (`deploy_employee`) | Founder deploy | **Raw key** — `"quote_chaser"` |

Two formats in one column, as `runner.py:46` already flags in its own
`ponytail:` comment. R1/R2 die in Phase 7; R3 is replaced in Phase 4.

### Nobody ever updates `Employee.status`

There is no pause/resume/fire route anywhere. `status` is `"active"` on every
row that has ever existed. `active_departments_for()`'s `!= "fired"` filter is
therefore correct but has never met real data — Phase 1 tests it with fakes
only.

---

## 2. Findings

### F1 — TWO paths set "deployed" without creating an `Employee` row ⚠️ *(one more than previously known)*

The Phase 3 audit found `app.py`'s `deploy_employee`. There is a **second**:

| Path | Sets | Creates `Employee`? |
|---|---|---|
| `activation.py:44` (`activate_frontdesk`) | `frontdesk_live = True` | ❌ **No** |
| `app.py:459` (`deploy_employee`) | appends `requested_roster` | ❌ **No** |
| `portal.py:58` (`_hire_employee`) | — | ✅ Yes |

Both no-row paths rely on `_backfill_employees` at the **next application
boot**. `activate_frontdesk` matters even after Phase 7, because it survives
as part of founder-led provisioning — it is simply no longer reached from a
self-serve form.

**Consequence:** a business provisioned today shows **zero departments** on
the Phase 5 dashboard until the process restarts, because
`active_departments_for()` reads `Employee` rows. Phase 4 must fix **both**
paths, not just the one already recorded.

### F2 — `create_all()` will NOT add a unique index to the existing `employee` table ⚠️ *(verified empirically)*

Phase 3's partial index worked because `departmentinterest` was a **brand-new
table** — `create_all` builds a table and its indexes together. For a table
that already exists, `create_all(checkfirst=True)` skips it entirely,
indexes included.

Verified directly rather than assumed:

```
after v1 (no index declared) : []
after v2 (index declared)    : []
VERDICT: index NOT added — explicit DDL required
```

**Consequence:** if Phase 4 wants a uniqueness guarantee on
`employee(business_id, role_key)` — and F3 says it should — it needs **real
migration DDL**, not a model change. `_migrate_add_columns` only issues
`ALTER TABLE … ADD COLUMN`, so this is a genuinely new kind of migration for
this codebase (`CREATE UNIQUE INDEX IF NOT EXISTS`), and it must run **after**
de-duplicating any rows that already violate it.

This is the single largest piece of migration work the audit found.

### F3 — Duplicate `Employee` rows are possible today ⚠️

There is **no unique constraint** on `employee(business_id, role_key)`.
`_hire_employee` does a SELECT-then-INSERT with nothing enforcing the check,
and FastAPI runs sync handlers in a threadpool even at `--workers 1` — so a
double-clicked "Hire" can create two rows for the same role. Harmless today
(nothing reads them); in Phase 5 it renders a department with the same
employee listed twice.

The pattern to copy already exists in this codebase: `Customer` has
`UniqueConstraint("business_id", "phone")` and `repositories.py` does
try/catch/re-select around it.

### F4 — Deploying a `planned` employee would make the dashboard lie ⚠️

`employees.REGISTRY` marks most roles `planned` — *"vision only. No engine, no
route, no UI."* Nothing stops Phase 4 from creating an `Employee` row for
`dispatcher` or `collections`. If it did, `active_departments_for()` would
report Operations or Finance as **staffed**, and the customer's dashboard
would show a department that cannot do anything.

Deployable today (`live` or `internal`) is a short list:

| Department | Deployable employees | Deployable? |
|---|---|---|
| Customer Service | `frontdesk` (live), `reviews` (internal) | ✅ |
| Sales | `quote_chaser` (internal) | ✅ |
| Customer Success | `retention_manager` (internal) | ✅ |
| Operations | — | ❌ none |
| Finance | — | ❌ none |
| Marketing | — | ❌ none |
| Leadership | — | not hireable by design |

**Consequence:** Phase 4 needs a `deployable_employees_for(department_key)`
helper (registry status `live`/`internal` only) and the ops console must
refuse — visibly and honestly — to deploy a department with none. Note this
does **not** contradict Phase 1's "every hireable department has ≥1 employee"
test: that asserts registry *membership*, which is about what Roster commits
to building, not what can be provisioned this week.

### F5 — Two sources of truth for "is Frontdesk deployed" ⚠️

`Business.frontdesk_live` (a boolean) and an `Employee` row with
`role_key="frontdesk"` both encode the same fact. `runner.is_active()` reads
the former; `active_departments_for()` reads the latter. After Phase 4 they
can disagree — e.g. an employee fired via a future control while
`frontdesk_live` stays `True`.

Phase 4 should pick one as authoritative (the `Employee` row, per the
platform PRD's employee model) and make the other derived or clearly
documented as legacy. Not necessarily resolved in Phase 4, but it must be a
conscious decision rather than an accident.

### F6 — `runner.is_active()` reads `requested_roster` and will break silently

`runner.py:41-56` is the one piece of *engine* code that consults
deployment state. If Phase 4 stops writing `requested_roster`, `is_active`
silently starts returning `False` for everything except `frontdesk`.

It is currently harmless — `JOB_COMPLETED_ROLES` is empty, so
`dispatch_job_completed` is a no-op — which is exactly why this would go
unnoticed until the first job-completion employee ships. Phase 4 must
re-point `is_active` at `Employee` rows in the same change that stops writing
`requested_roster`.

### F7 — `delete_client`'s cascade is now incomplete

`app.py:496-506` deletes children in dependency order but predates both new
tables. `DepartmentInterest` (Phase 3) and `OwnerNotification` (Phase 2) are
**not** in the cascade, so deleting a business now orphans rows in both.
SQLite doesn't enforce FKs here (`db.py` sets no `PRAGMA foreign_keys=ON`), so
this fails silently rather than erroring.

Small fix, but it is a real data-integrity regression introduced by the last
two phases and belongs in Phase 4.

### F8 — A test pins the legacy `retention` key

`test_employee_model.py:44` asserts:

```python
assert "retention" in keys and "retention_manager" not in keys
```

So `"retention"` is the canonical **stored** value, and Phase 1's alias in
`department_for_role` exists precisely to bridge it. Phase 4 must either keep
that alias permanently or migrate the stored data *and* update this test —
never change `role_key_for()` alone, which would leave old rows unresolvable.

---

## 3. Transaction boundaries, ordering, and partial failure

**Notification ordering.** Blueprint §4 lists "a department going live" as a
notification type; Phase 2 explicitly deferred it for want of a producer, and
Phase 4 is that producer. Phase 2's `record_owner_notification` docstring
requires it be called **only after the caller's primary work has committed** —
it commits the session, so an in-flight transaction would be flushed with it.

For deployment that means a strict order:

```
1. create Employee row(s)      →  commit
2. record the notification     →  commits only its own row
```

Reversed, a customer could receive "Sales is now staffed" while the dashboard
still shows nothing — the exact dashboard/database disagreement to avoid.

**Partial deployment.** Deploying a department means creating *N* rows (e.g.
Customer Service = `frontdesk` + `reviews`). A crash between them leaves the
department partially staffed. Because `active_departments_for()` treats a
department as active with **≥1** non-fired employee, the department would
appear fully live while missing an employee.

The right answer is not a transaction spanning everything — it is
**idempotent, re-runnable deployment**: re-deploying fills only what's
missing, exactly as `_backfill_employees` already does with its `have` set.
Recovery then requires no special path, just running the same action again.

**Idempotency of the notification.** If deployment is re-run to complete a
partial failure, it must not send a second "department is live" alert.
Simplest correct rule: notify only when the deployment actually created at
least one row that didn't exist.

---

## 4. The invariants Phase 4 must preserve

Written down before code, one regression test each.

| # | Invariant | Why it matters | Currently |
|---|---|---|---|
| **I1** | A business with `frontdesk_live=True` has exactly one `frontdesk` `Employee` row, **created immediately** — not at next boot | F1; Phase 5's dashboard reads these rows | ❌ Violated by `activate_frontdesk` |
| **I2** | Deploying the same department twice creates exactly one row per role | Double-click / retry safety | ⚠️ App-level check only |
| **I3** | No duplicate `(business_id, role_key)` rows can exist, even under concurrency | F3 | ❌ No constraint |
| **I4** | Every deployed `role_key` resolves to a department via `department_for_role()` | Otherwise an employee vanishes from every department view | ✅ Guarded by Phase 1's `ROLE_KEYS` test — extend to deployment |
| **I5** | Only `live`/`internal` registry employees are deployable; never `planned` | F4 — a staffed department that can't work | ❌ Nothing enforces it |
| **I6** | `runner.is_active()` returns the same answers before and after the migration | F6 — silent engine regression | ❌ Will break |
| **I7** | Deleting a business removes every child row, including `DepartmentInterest` and `OwnerNotification` | F7 — silent orphans | ❌ Incomplete |
| **I8** | `_backfill_employees` stays idempotent and never resurrects a deliberately removed employee | It runs on **every** boot | ⚠️ Idempotent, but would re-create anything still in `requested_roster` |
| **I9** | A "department is live" notification is recorded only **after** the `Employee` rows commit | §3 — dashboard/notification agreement | n/a (no producer yet) |
| **I10** | A half-finished deployment is completed by re-running the same action | §3 — failure recovery without a special path | n/a |
| **I11** | Recording interest deploys nothing; actioning interest deploys nothing | Phase 3's two guards must survive being wired together | ✅ Guarded — keep green |
| **I12** | Deploying for business A never touches business B | Business isolation is the security boundary (PRD §12) | ✅ Implicitly — needs a test |
| **I13** | The stored key for Retention Manager stays `"retention"` unless the data is migrated in the same change | F8 | ✅ Pinned by an existing test |
| **I14** | `frontdesk_live` and the `frontdesk` `Employee` row never disagree | F5 — two sources of truth | ❌ Can diverge |

---

## 5. Migration work this audit adds to Phase 4

Beyond the already-recorded "deploy route must create `Employee` rows":

1. **`activate_frontdesk` must create the `frontdesk` `Employee` row** (F1) — a second path with the same defect.
2. **A real index migration** (F2/F3): de-duplicate existing `(business_id, role_key)` rows, then `CREATE UNIQUE INDEX IF NOT EXISTS` via new DDL — `_migrate_add_columns` cannot do this, and `create_all` will not.
3. **`deployable_employees_for()` + an honest ops-console refusal** for departments with no deployable employees (F4).
4. **Re-point `runner.is_active()` at `Employee` rows** in the same change that stops writing `requested_roster` (F6).
5. **Extend `delete_client`'s cascade** to `DepartmentInterest` and `OwnerNotification` (F7).
6. **Decide `frontdesk_live`'s status** — authoritative, derived, or legacy (F5/I14).

Items 2 and 4 are the risky ones: 2 touches stored data, and 4 changes engine
behavior. Both deserve their own task and their own commit.

---

## 6. Recommendation on sequencing

Phase 4 as currently scoped bundles a **migration bug fix** (F1, F2, F3, F6,
F7 — correctness work on existing behavior) with **new product surface** (the
department-grouped ops console, the pipeline stage, expansion requests).

Those have different risk profiles and different review needs. The founder has
already framed the deploy-route defect as "a migration bug fix, not a product
enhancement" — the audit suggests extending that framing to the whole
correctness cluster and splitting Phase 4 in two:

- **Phase 4a — Deployment correctness.** One deployment path that creates rows
  immediately and idempotently, the unique index, `is_active` re-pointed, the
  cascade fixed. No new UI. Independently deployable and independently
  reviewable, and it makes I1–I8 true before any screen depends on them.
- **Phase 4b — The ops console.** Department grouping, pipeline stage,
  expansion requests surfaced, deploy-by-department. Builds on a foundation
  that is already correct.

This keeps the "small, independently mergeable" discipline that has held for
three phases, and it means the riskiest work (data migration, engine
behavior) lands in a change with no UI in it to distract a reviewer.

**Recommendation, not a decision** — Phase 4 stays as one phase if the founder
prefers.
