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

**Files:** `agent/departments.py`, `agent/app.py`, `agent/tests/test_departments.py`

Move `_department_rows`'s computation into `departments.department_status_for(employees)`,
returning per-department `state` (`staffed` | `partial` | `empty` | `unavailable`)
with **no labels**. `app.py` maps state → founder label; Phase 5's portal maps
state → customer label. Pure refactor: `test_ops_console.py` must stay green
**unmodified**, which is the proof the founder surface's behavior is unchanged.

**Tests:** state computation for each of the four cases; a test asserting
`app.py` no longer defines its own version; existing ops-console tests green.

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

**Files:** `agent/templates/portal_base.html` (new), `agent/portal.py`, tests

Five nav items exactly — **Overview / Departments / The Briefing /
Notifications / Settings** (blueprint §5). No "Grow Your Workforce" item:
expansion is contextual content, never navigation. Mobile bottom tab bar.
Routes registered under `/v2/` so the live dashboard is untouched.

**Tests:** all five items render; no sixth; expansion is not a nav item; the
live `/dashboard` is byte-identical to before (a regression guard on the
whole phase).

**Expected:** `451 passing`.

---

## Task 4: Overview and Departments

**Files:** `agent/templates/dashboard_v2/`, `agent/portal.py`, tests

Overview: cross-department outcomes strip (active departments only), digest,
contextual expansion prompt. Departments: the grid, active cards from the
shared helper, inactive cards **educational by design** (blueprint §7 —
problem / outcome / why owners add it, straight from the registry copy Phase 1
already wrote), each with "Ask us about [Department]".

**Tests:** a business with nothing deployed shows the honest empty state, not
a fake roster; a partially staffed department shows as **active** (C7); every
inactive card renders all three educational fields; Leadership never appears
as inactive-and-hireable.

**Expected:** `460 passing`.

---

## Task 5: Department detail — outcomes first, employees last

**Files:** `agent/templates/dashboard_v2/department.html`, `agent/portal.py`, tests

The blueprint's fixed hierarchy: **header → outcomes strip → activity feed →
your team**. One shared template for all six hireable departments.

**Tests:** outcome numbers precede employee names in the rendered DOM order
(the hierarchy is testable, not just intended); a department the business
doesn't have 404s rather than rendering empty; employees appear as secondary
detail with no AI-internal metrics anywhere.

**Expected:** `467 passing`.

---

## Task 6: Ask-us-about → `record_interest`

**Files:** `agent/portal.py`, tests

`POST /v2/dashboard/departments/{key}/interest` → Phase 3's `record_interest`,
PRG, 400 on an unknown or non-hireable key (Phase 3 already raises
`ValueError`; the route turns it into a message, never a 500). Idempotent by
Phase 3's partial index.

**Tests:** the CTA records interest; a double-submit creates one row; an
already-staffed department offers no CTA; asking is scoped to the session's
own business.

**Expected:** `473 passing`.

---

## Task 7: The Briefing and Notifications

**Files:** `agent/templates/dashboard_v2/`, `agent/portal.py`, tests

Notifications reads Phase 2's `recent_notifications()` — newest first,
business-scoped. The Briefing ships **rules-based** (as approved at execution-
plan review): cross-department narrative from real rows, plus a growth
recommendation only when the data supports one. **Nothing auto-records
interest** (blueprint §8 / Phase 3 H6).

**Tests:** notifications render newest-first and never cross businesses;
rendering the Briefing creates **no** `DepartmentInterest` row; the Briefing's
empty state is honest before data exists.

**Expected:** `481 passing`.

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
