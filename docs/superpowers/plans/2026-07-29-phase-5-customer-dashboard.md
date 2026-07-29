# Phase 5 — Customer Dashboard: Task-Level Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the department-first customer dashboard from blueprint §4–§9 —
Overview, Departments, department detail, The Briefing, Notifications,
Settings — **unlinked**, alongside the live one, so nothing cuts over until
Phase 6.

**Architecture:** The customer surface becomes a **second consumer of the same
deployment helpers the founder console uses**, never a second implementation.
Built at `/v2/dashboard*` beside the existing `/dashboard`, following the
repo's proven precedent (`index-v2.html` + `/preview` built beside the live
landing page).

**Tech Stack:** Python 3, FastAPI, SQLModel, Jinja2, pytest. No new dependency.

**Parent plan:** `docs/superpowers/plans/2026-07-28-departments-migration-execution-plan.md` (Phase 5)
**Product source of truth:** `docs/superpowers/specs/2026-07-28-departments-product-blueprint-design.md`

## Global Constraints

- Run tests from `agent/`: `cd agent && .venv/bin/python -m pytest tests/ -q`.
- Baseline to preserve: **433 passing** (end of Phase 4b).
- **The live `/dashboard` keeps working, untouched, for the whole phase.** Phase 6 repoints it. A diff that changes the existing dashboard's behavior means the phase has drifted.
- **THE INVARIANT (founder, 2026-07-29, now in the blueprint):** every customer-visible deployment state derives from `Employee` rows **through the shared deployment helpers**, never from duplicated template logic. No `requested_roster` read, no `tested_at`-as-deployment, no hardcoded badge.
- **One implementation of department state, used by both surfaces.** If `portal.py` grows its own copy of `_department_rows`, the phase has failed its main architectural goal.
- `DESIGN.md` governs every visual decision (`CLAUDE.md` project rule): warm paper palette, existing `portal.css` tokens, no new palette, minimal-functional motion only.
- Commit after every task; each task leaves the full suite green on its own.

---

## Pre-Implementation Audit

### C1 — ⚠️ The customer dashboard and founder console now disagree *(live on `main` today)*

Phase 4b's department deploy path creates `Employee` rows and **does not** touch
`requested_roster`. The customer dashboard reads `requested_roster`
(`portal.py:328` → `dashboard.html:130,140,151,157`). So right now:

| Founder deploys Sales via… | Founder console shows | Customer dashboard shows |
|---|---|---|
| `department_key=sales` | Sales · **Staffed** | Quote Chaser · *"Opportunity detected"* |
| `role_key=quote_chaser` | Sales · **Staffed** | Quote Chaser · *"We'll be in touch"* |

Two surfaces, one business, different answers — and which one you get depends
on **which button the founder pressed**. This is exactly the failure the new
invariant forbids.

**Not fixed by patching Phase 4b.** The tempting fix — also append to
`requested_roster` — writes harder to a field Phase 7 deletes. The correct
fix is this phase: the customer dashboard reads `Employee` rows. Accepted
risk in the meantime: zero customers, and `/dashboard` is replaced wholesale
here.

### C2 — Deployment state is currently derived from three unrelated fields

`dashboard.html`'s "Your office" card is a **fixed three-slot template** whose
states come from:

| Slot | State derived from | Is that deployment? |
|---|---|---|
| Receptionist | `client.tested_at` | ❌ whether the owner sent a test message |
| Quote Chaser | `'Quote Chaser' in requested_roster` | ❌ what was asked for |
| Retention Manager | `'Retention Manager' in requested_roster` | ❌ what was asked for |

None reads `Employee`. A business whose Frontdesk was never deployed still
renders "Ready". Phase 5 replaces all three with the shared helper.

### C3 — The shared view model must move before it is copied *(shapes Task 1)*

`_department_rows()` and `_employees_by_business()` live in `app.py`
(founder-only). Phase 5 needs the identical computation. Copying them into
`portal.py` would violate the invariant on day one.

**Resolution:** move the computation into `departments.py` (which already owns
`active_departments_for`, `deployable_employees_for`, `canonical_role_key`) and
have **both** surfaces call it. The *labels* differ by audience and stay at the
surface — the founder sees "Not staffed", the customer sees "Not yet part of
your workforce" — but the **state** is computed once.

### C4 — Derived metrics are inconsistent between surfaces

| Metric | Customer | Founder |
|---|---|---|
| Jobs booked | `real_job_count`, excludes test threads | no count at all |
| Job list | test-tagged, test rows visibly marked | **all jobs, no test/real distinction** |

The founder currently sees test jobs as real work. Blueprint §6 puts an
outcomes strip on every department page, so this metric is about to be
rendered in more places — it needs one definition. Task 2 gives it one.

### C5 — `_TEST_THREADS` is imported privately where a public helper exists

`portal.py:25` imports the private `notifications._TEST_THREADS`; Phase 2 added
the public `is_test_thread()`. Trivial, but it is the same "two ways to answer
one question" pattern the invariant targets. Fixed as part of Task 2.

### C6 — Empty states are good in places, absent in others

Already honest: Activity has a **two-tier** empty state (no jobs at all vs.
only test jobs), and "Your number" says "Connecting your line" rather than
inventing one. Both are worth carrying forward verbatim.

Missing: every new surface (Overview, Departments, The Briefing, Notifications)
needs one, per blueprint §4's table. Also `dashboard.html:47`'s "What she's
learned" renders its header even when every row beneath it is empty — a
section title with nothing under it.

### C7 — "Partially staffed" has no customer representation *(needs a decision)*

Phases 4a/4b introduced `partial`. The customer has no such concept today.

**Recommendation, taken by this plan:** a partially staffed department shows to
the customer as **active** — it *is* doing work — and the employee list
underneath simply shows who is working. The customer is never shown
"partial", because a half-staffed department is Roster's operational problem,
not something to worry the owner with. The founder console keeps showing
`partial`, because completing it is their job. **Same state computation, different
audience labels** — exactly the C3 split.

### C8 — There is no customer navigation at all, and no shared layout

`dashboard.html` is a single scrolling page with a logo and a logout button. It
also **does not extend `base.html`** — it is a standalone `<html>` document,
unlike every founder template. Blueprint §5 requires a five-item sidebar plus a
mobile bottom bar, so Phase 5 introduces both a customer base layout and
navigation for the first time. This is the largest new-construction task.

### C9 — Employee display names are already inconsistent in the data *(needs a decision)*

Three different names exist for the same employee:

| Source | Name |
|---|---|
| `roles.receptionist_display_name(trade)` | "CSR" / "Receptionist" / "Office Manager" |
| `deployment.py` (Phase 4a) | "Frontdesk" (from the registry) |
| `db._backfill_employees` | "Receptionist" |

So `Employee.display_name` on real rows depends on when and how the row was
created.

**Recommendation, taken by this plan:** the customer sees the **registry**
display name (`Frontdesk`), because employees are now secondary detail inside
a department and the department carries the customer-facing framing. The
trade-adaptive naming was built when the employee *was* the product; it no
longer is. `roles.py` dies in Phase 7 regardless. Flagged because it is a
visible copy change, not a silent one.

### C10 — The old dashboard's self-serve hire must not be carried forward

`dashboard.html:143` still posts to `/roster/hire`. Phase 6 retires it, Phase 7
deletes it. The new dashboard must ship with **"Ask us about [Department]"**
routing to Phase 3's `record_interest` instead — never a form that deploys.

---

## Task 1: Move the shared view model out of `app.py`

**Files:** `agent/departments.py`, `agent/app.py`, `agent/templates/client_detail.html`, `agent/tests/test_departments.py`

Move `_department_rows`'s computation into
`departments.department_status_for(employees) -> list[DepartmentStatus]`.

**`DepartmentStatus` is presentation-neutral — facts only** (founder,
2026-07-29). Exactly these fields, and no others:

| Field | Type |
|---|---|
| `department` | `Department` |
| `deployable` | `list[EmployeeDefinition]` |
| `staffed` | `list[EmployeeDefinition]` |
| `deployed_count` | `int` |
| `deployable_count` | `int` |
| `state` | `"staffed" \| "partial" \| "empty" \| "unavailable"` |

**No wording of any kind lives here.** The founder surface maps state →
"Not staffed"; the customer surface maps state → "Not yet part of your
workforce". Same facts, independent presentation — which is what makes the
invariant *shared computation, independent presentation* rather than shared
computation plus a shared voice. A wording field in this object would leak
one audience's tone into the other's screen the first time either changed.

Pure refactor: `test_ops_console.py` must stay green **unmodified** — that is
the proof the founder surface's behavior is unchanged.

**Tests:** state and counts for each of the four cases; **a permanent guard
asserting `DepartmentStatus` carries no presentation field**; `app.py` no
longer defines its own version; existing ops-console tests green.

**Expected:** `438 passing`.

---

## Task 2: One definition of the outcome metrics

**Files:** `agent/metrics.py` (new), `agent/portal.py`, `agent/app.py`, tests

`metrics.booked_jobs(session, business_id)` and
`metrics.department_outcomes(session, business_id, department_key)` — the
numbers blueprint §6 puts at the top of every department page, defined once.
Test jobs excluded via the public `is_test_thread()` (C5), and the founder's
job list gains the same test tagging the customer's already has (C4).

**Tests:** test jobs excluded from counts on both surfaces; the founder list
marks test jobs; both surfaces report the same number for the same business.

**Expected:** `445 passing`.

---

## Task 3: The customer base layout and navigation

### Design principle (founder, 2026-07-29) — permanent

> **Navigation represents stable customer concepts, never implementation
> structure.**

The nav names things an owner already thinks about — their whole operation,
their departments, what happened, what needs them, their account. It never
names Roster's internals: no "Employees", no "Jobs", no "Campaigns", no
"Recovery", no "Agents". Those are how the work is built, not how the owner
thinks about their business — and a nav built from implementation structure
must be renamed every time the implementation changes, which is exactly what
this migration is undoing. Enforced by a test, not by convention.

It is also why **expansion is not a nav item**: "Grow Your Workforce" is a
storefront concept, not something an owner does daily. Expansion appears
contextually (blueprint §5) — an Overview recommendation, an inactive
department card, a Briefing nudge.

**Files:** `agent/templates/portal_base.html` (new), `agent/templates/dashboard_v2/overview.html` (new), `agent/portal.py`, `agent/static/portal.css`, tests

**Order of work (founder-specified):** build `portal_base.html` first →
migrate exactly **one** page onto it → verify responsive behaviour in a real
browser → only then create the remaining pages in Tasks 4-7.

Five nav items exactly - **Overview / Departments / {briefing_label} /
Notifications / Settings** (blueprint SS5), every visible label centralized in
`portal.NAV_ITEMS` so no template hardcodes one. Mobile bottom tab bar. Routes
under `/v2/` so the live dashboard is untouched.

`DESIGN.md` compliance: existing `portal.css` tokens only, no new palette,
dashboard type scale (H1 28 / H2 18 / body 14 / small 12-13), 8px base unit,
radius sm 6 / md 10 / lg 12, hover-and-focus motion only at 150-200ms, no dark
mode.

**Access decision:** `/v2/*` requires a session but does **not** require
`frontdesk_live`. The old dashboard bounced un-activated businesses into
onboarding; the new one shows honest empty states instead, per blueprint SS4's
empty-state table - and after Phase 6 there is no onboarding wizard to bounce
them to.

**Tests:** all five items render in order; no sixth; no implementation-
structure word appears in the nav (the design principle, as a test); labels
come from the centralized constant; the Briefing's label is `BRIEFING_LABEL`;
`/v2/*` requires login; the active item is marked; the mobile bar exists; the
live `/dashboard` still renders unchanged.

**Expected:** `~468 passing`.

---

## Task 4: Overview and Departments

### Invariant (founder, 2026-07-29) — permanent

> **Every customer-visible metric must have a future drill-down path.**

A number on the dashboard represents **concrete underlying records the owner
could be shown** — never an opaque summary. "12 jobs booked" is 12 rows they
could one day tap into; "an efficiency score of 84" is not a number, it is an
assertion.

Two consequences, both enforced rather than remembered:

- `metrics.METRIC_RECORDS` declares, for every metric key, which records it
  counts. A metric cannot ship without that declaration — a test fails if any
  key returned by `department_outcomes` is missing from the map.
- It rules out composite/derived scores by construction: if a number cannot
  name the rows behind it, it cannot be declared, so it cannot ship.

This is the metric-layer twin of the state-derivation invariant: facts trace
to records, presentation stays in templates.

**Files:** `agent/metrics.py`, `agent/portal.py`, `agent/templates/dashboard_v2/`, tests

**Scope note — no dead controls.** Inactive department cards ship fully
educational here (problem / outcome / why owners add it, straight from the
Phase 1 registry copy) but **without the "Ask us about" button**: its route
lands in Task 6. Shipping a button that does nothing would be worse than
shipping the education alone one task early.

**Customer-facing state mapping** (the founder console keeps its own):
`staffed` and `partial` both read as **Working** — a partially staffed
department *is* doing work, and completing it is Roster's operational problem,
not the owner's worry (audit C7). `empty` and `unavailable` both read as
**Not yet part of your workforce** — the customer does not need to know which
of those two reasons applies.

**Leadership never appears as a department card.** It is not hireable and is
included automatically; it lives in the nav as the executive view.

**Tests:** Overview shows outcomes only for active departments and an honest
empty state otherwise; the Departments grid lists the six hireable departments
and never Leadership; a partially staffed department reads as Working; every
inactive card renders all three educational fields; no AI-internal metric
appears anywhere; every metric key declares its drill-down records; metric
labels are centralized, not hardcoded in templates.

**Expected:** `~480 passing`.

---

## IA REVISION (founder, 2026-07-29) — workspaces, not report cards

**The change.** The customer should feel like they are *opening departments
inside their office*, not reading a report. The hierarchy becomes:

```
Overview  →  Department workspace  →  Employee  →  Activity
```

not `Overview → metric cards`. Overview becomes a **gateway** into workspaces
rather than a container for every metric. Each department answers its own
question, so each one feels different rather than being the same card with
different numbers in it.

**What this does NOT change — keep exactly as built:** `DepartmentStatus`
(presentation-neutral facts), `metrics.py` + `METRIC_RECORDS` (the drill-down
invariant), `CUSTOMER_STATE_LABELS` / `METRIC_LABELS` (centralized wording),
`portal_base.html` + the nav shell. Tasks 1–3 stand. Only Task 4's two
templates get reworked, and only into entry points.

### Pre-revision audit: what per-employee data actually exists

The example in the brief implies per-employee numbers. Checking what records
exist behind each one, because the drill-down invariant means a number ships
only if it can name its rows:

| Example number | Records behind it | Ships? |
|---|---|---|
| Frontdesk — "6 jobs booked" | `job` rows, test-excluded | ✅ real |
| Frontdesk — "18 calls answered" | distinct `xai-voice:{call_id}` threads in `message` (the voice adapter does persist turns — `xai_voice_adapter.py:314`) | ✅ real |
| Quote Chaser — estimates chased / won back | `recoveryjob` via `face == 'quote'` | ✅ real |
| Retention — customers reached / returned | `recoveryjob` via reactivation + membership | ✅ real |
| Reviews — **"5 review requests sent"** | **none** — `app.py:553` sends the SMS and records nothing | ❌ **not today** |
| Reviews — **"2 reviews received"** | **none** — Roster never learns whether a review was left | ❌ **not without a Google/Yelp integration** |

**⚠️ No record in the database carries an employee attribution.** The only
`employee_id` column anywhere is on `event`, and nothing publishes events in
the live path (Phase 4 audit F6). So per-employee metrics are necessarily
**by convention** — "these records belong to this role" — which is fine, but
the convention has to be *declared*, exactly like `METRIC_RECORDS`, or it
becomes the untraceable-number problem one level down.

**Two decisions this forces (founder input wanted — see §Decisions below):**

1. **Review requests**: add `Job.review_requested_at`, set where the SMS is
   already sent. One additive column, and the metric becomes a real record
   the owner could drill into. **Recommended.**
2. **Reviews received**: genuinely unknowable without an integration. **Do not
   render it.** Showing it would be the first fabricated number on the
   dashboard, and it would break both the drill-down invariant and Roster's
   standing "real-data-only metrics" rule.

---

## Task 5: Department identity + per-employee facts (shared helpers, no UI)

**Files:** `agent/departments.py`, `agent/metrics.py`, `agent/db_models.py`, `agent/db.py`, `agent/app.py`, tests

- Add `question` to `Department` — the one thing that department answers, so
  each workspace opens differently: *"Are customers being looked after?"* /
  *"Are we recovering revenue?"* / *"Are today's jobs running smoothly?"* /
  *"Are invoices being collected?"* / *"Are customers renewing?"* / *"Are we
  generating new business?"* Registry copy, same category as `mission`.
- Add `metrics.employee_outcomes(session, business_id, role_key)` plus
  `EMPLOYEE_METRIC_RECORDS` — the per-employee convention, declared. Every key
  it can return must appear in `METRIC_RECORDS` too.
- Add `Job.review_requested_at` (additive column + set at the existing send
  site) so "review requests sent" counts real rows.
- Support a `since` window so a workspace can say "today" honestly.
- **Deliberately absent:** any "reviews received" metric.

**Tests:** every department declares a question; `employee_outcomes` keys all
declare records; Frontdesk's job count matches `booked_jobs`; a role with no
attributable records returns `{}` not zeros; review requests count only jobs
where the send actually happened; `since` filters correctly.

---

## Task 6: The Department Workspace

**Files:** `agent/templates/dashboard_v2/department.html`, `agent/portal.py`, `agent/static/portal.css`, tests

Route `GET /v2/dashboard/departments/{key}`. Structure, top to bottom:

1. **Department name + question** — what this workspace answers.
2. **Health** — the shared `DepartmentStatus` state in customer wording.
3. **Department outcomes** — the existing `department_outcomes`.
4. **Every employee in the department**, each with: what they do, their
   status, their own outcomes, and their recent activity — **clickable**
   through to Task 7.
5. **Recent activity / timeline** for the department as a whole, from real
   rows (`job`, `ownernotification`), newest first.

**Tests:** the question renders; employees appear with their own numbers;
employee cards link to the drill-down; a department the business doesn't have
404s; an employee with no records shows activity-empty rather than fake
zeros; no AI-internal term anywhere; outcomes precede employee detail in DOM
order (blueprint §2's hierarchy is testable).

---

## Task 7: Employee drill-down

**Files:** `agent/templates/dashboard_v2/employee.html`, `agent/portal.py`, tests

Route `GET /v2/dashboard/departments/{key}/employees/{role_key}`. What this
employee does, its status, its outcomes, and its work history — the "Activity"
leaf of the hierarchy.

**Tests:** renders for a deployed employee; 404 for one not deployed for this
business; 404 for a role that isn't in the named department; business-scoped
(another business's employee is never reachable); history is newest-first.

---

## Task 8: Overview becomes a gateway (reworks Task 4's templates)

**Files:** `agent/templates/dashboard_v2/overview.html`, `dashboard_v2/departments.html`, `agent/portal.py`, tests

Overview stops being a metric wall. It becomes the **door into each
workspace**: one tile per active department showing its name, the question it
answers, its health, and at most **one** headline number — then a way in. The
detail lives in the workspace, not here.

The Departments grid keeps its educational inactive cards (Task 4, unchanged)
but its active cards become entry points rather than metric displays.

**Tests:** every active department on Overview links to its workspace; Overview
renders at most one number per department; the empty state survives; inactive
education is unchanged.

---

## Task 9: Ask-us-about → `record_interest`

Unchanged from the original plan — `POST /v2/dashboard/departments/{key}/interest`
→ Phase 3's `record_interest`, PRG, 400 on unknown/non-hireable, idempotent by
the partial index. Now attached to the inactive cards Task 4 already ships.

---

## Task 10: The Briefing and Notifications

Unchanged from the original plan. Notifications reads Phase 2's
`recent_notifications()`; the Briefing ships rules-based, narrating across
workspaces, and records no interest on render.

---

## Decisions needed before Task 5 starts

1. **`Job.review_requested_at`** — add it (recommended), or leave Reviews
   without numbers until an integration exists?
2. **"Reviews received"** — confirmed dropped? It cannot be sourced today, and
   inventing it would be the dashboard's first fabricated number.

---

## Phase 5 Acceptance Criteria

1. **Full suite green at 481**, up from 433.
2. **The invariant holds** — grep proves no customer template or `portal.py` path reads `requested_roster`, `tested_at`-as-deployment, or hardcodes a badge; all deployment state flows through `departments.department_status_for`.
3. **One implementation** — `_department_rows` exists in exactly one module, imported by both surfaces.
4. **The live `/dashboard` is unchanged** and still passes every pre-existing portal test unmodified.
5. **Both surfaces agree** — an end-to-end test deploys a department via the founder route and asserts the customer dashboard immediately shows it staffed (the direct regression test for C1).
6. **Every new surface has an honest empty state.**
7. **No self-serve deploy** anywhere in the new dashboard.
8. **Seven commits**, one per task, each green independently.

**Deliberately NOT in this phase:** no cutover (Phase 6 repoints `/dashboard`),
no deletion of the old dashboard or `/roster/hire` (Phase 7), no
customer-facing controls beyond "Ask us about" (Phase 4a's I14 deferral
stands — customer controls stay intentionally minimal until real feedback),
and no premium Briefing capabilities.
