# Departments Migration Execution Plan

> **For agentic workers:** this is a **phase-level engineering roadmap**, not
> a task-level implementation plan. It defines scope, files, risk, and
> completion criteria per phase so nothing is ambiguous — but per the
> founder's explicit instruction, it contains **no implementation code**.
> Before any phase below is started, run that phase through
> **superpowers:writing-plans** again on its own to produce the bite-sized,
> TDD, code-inclusive task list — then execute via
> **superpowers:subagent-driven-development** or **superpowers:executing-plans**.
> Steps use checkbox (`- [ ]`) syntax only at the phase-gate level here.

**Goal:** Migrate the existing Roster application from its employee-first,
self-serve product to the approved department-first, contact-us product
defined in `docs/superpowers/specs/2026-07-28-departments-product-blueprint-design.md`
(the **canonical source of truth** — where this plan and that document
disagree, this plan is wrong and gets fixed, per the founder's standing
instruction).

**Architecture:** No rewrite. The backend substrate (`Business`, `Customer`,
`Employee`, `Event`, `AgentEngine`, `BusinessMemory`, channel adapters) was
already built business-scoped, not employee-scoped, and needs no structural
change — confirmed in `docs/superpowers/specs/2026-07-28-departments-architecture-migration-review.md`.
"Department" is introduced as a **code registry** (mirroring the existing
`RoleDefinition`/`EmployeeDefinition` pattern), not a database table. Every
phase ships additively wherever possible (new files/columns/routes
alongside old ones), with two deliberate cutover points (Phases 6 and 7)
where old behavior is retired — never in the same commit it's replaced.

**Tech Stack:** Unchanged — Python 3 / FastAPI / SQLModel / SQLite (Railway
volume) / Jinja2 templates, pytest. No new dependency is required by any
phase in this plan.

## Global Constraints (apply to every phase implicitly)

- The application must remain deployable after every phase (founder
  requirement #1).
- Every phase must be independently testable and mergeable — no phase
  depends on a same-day follow-up phase to leave the app in a working state
  (founder requirement #2).
- No large rewrites, no long-lived branches — prefer additive parallel
  paths over in-place rewrites wherever the pattern allows it (founder
  requirement #3; this repo already has a proven precedent — the landing
  page's `index-v2.html` + `/preview` route built alongside the live site).
- Preserve working infrastructure wherever possible — `engine.py`,
  `service.py`, `memory.py`, channel adapters, recovery/referral engines are
  **out of scope for the entire plan** (founder requirement #4).
- Replace product assumptions (employee-first, self-serve), never sound
  architecture (founder requirement #5).
- Where the current implementation conflicts with the blueprint, the
  implementation changes — the blueprint is not renegotiated mid-migration
  (founder requirement #6).
- **One onboarding flow, no exceptions** (founder, 2026-07-28): every
  customer begins at Contact Us and is provisioned by the Roster team
  through the internal ops platform. **No second path may exist — including
  a founder-only or internal-only one.** Every engineering decision
  optimizes for the final product, never for preserving a legacy workflow.
- All database migrations are additive only, via the existing
  `_migrate_add_columns` mechanism in `db.py` — no destructive migration
  anywhere in this plan.
- Any phase touching visual/template design must follow `DESIGN.md`, per
  `CLAUDE.md`'s standing project rule.
- Full pytest suite must stay green at the end of every phase — this is
  the acceptance bar for "independently mergeable," not merely "this
  phase's new tests pass."

---

## Phase Summary Table

| Phase | Objective | Complexity | Depends on | Can run parallel with |
|---|---|---|---|---|
| 0 | Documentation alignment | Low | — | 1, 2, 3 |
| 1 | Department code registry | Low | — | 0, 2 |
| 2 | Owner notification log | Low | — | 0, 1, 3 |
| 3 | Expansion-interest capture | Low | 1 | 2 |
| 4 | Ops console: department grouping + pipeline stage | Medium | 1, 3 | 5 |
| 5 | New customer dashboard (built unlinked) | High | 1, 2, 3 | 4 |
| 6 | Cutover: dashboard live, self-serve retired | Medium | 4, 5 | — |
| 7 | Remove legacy code | Low | 6 | — |
| 8 | Documentation closeout | Low | 7 | — |

---

## Dependency Graph

```
Phase 0 (docs) ─────────────────────────────────────────────────────► (independent, anytime)

Phase 1 (registry) ──┬──► Phase 3 (interest) ──┬──► Phase 4 (ops console) ──┐
                      │                          │                          │
Phase 2 (notif log) ──┴──────────────────────────┴──► Phase 5 (dashboard) ──┤
                                                                             ▼
                                                                        Phase 6 (cutover)
                                                                             │
                                                                             ▼
                                                                        Phase 7 (delete legacy)
                                                                             │
                                                                             ▼
                                                                        Phase 8 (docs closeout)
```

**Parallel tracks available to an engineering team:**
- Track A: Phase 1 → Phase 3 (registry, then the table that validates against it)
- Track B: Phase 2 (independent from the moment work starts)
- Once Phases 1–3 are all merged: Phase 4 and Phase 5 can be built by two
  different engineers simultaneously — they touch disjoint files (`app.py`
  + `client_detail.html` vs. `portal.py` + new `dashboard_v2/` templates)
  and serve different consumers (founder vs. customer).
- Phases 6, 7, 8 are strictly sequential — each is a cutover/cleanup step
  that must observe the prior one's outcome before proceeding.

---

## Phase 0 — Documentation Alignment

**Objective:** Make `ROADMAP.md`, the platform PRD, and `SALES.md`
internally consistent with the now-approved blueprint, so no fresh session
(human or agent) reads the standing "architecture frozen" / "employee-first"
guardrails as still in force.

**Why this order:** Every later phase's PR will be reviewed against these
documents. Shipping eight phases of department-first code while the
project's own governing docs still say "one employee at a time" creates
exactly the internal contradiction the earlier architecture review warned
about. It's ordered first for hygiene, not because anything technical
depends on it — it can just as easily run in parallel with Phase 1 or 2 if
the team prefers to start code immediately.

**Files affected:**
- Modify: `ROADMAP.md`
- Modify: `docs/superpowers/specs/2026-07-13-roster-platform-architecture-prd.md`
- Modify: `SALES.md`
- Modify (header note only): `docs/superpowers/specs/2026-07-21-ai-staffing-repositioning-design.md`

**What changes:** Dated log entries recording the department-first pivot and
pointing at the blueprint and this plan as the new sources of truth. The
PRD gains a short addendum (same pattern as its existing §11a and §6a
additions) naming the blueprint as the customer-facing IA source of truth
going forward.

**What stays unchanged:** The PRD's actual domain-model and backend-
architecture sections (§6–§13) — still accurate, confirmed by the
architecture review.

**Dependencies:** None.

**Risks:** None — documentation only.

**Required tests:** None (no code changes).

**Completion criteria:** No governing doc in the repo contradicts the
blueprint or this plan. A fresh read of `ROADMAP.md` would not cause a new
session to refuse or question department-first work.

**Complexity:** Low.

---

## Phase 1 — Department Code Registry

**Objective:** Introduce `Department` as a real, importable code registry —
the canonical 7 departments (Customer Service, Sales, Operations, Finance,
Customer Success, Marketing, Leadership), each carrying the display copy
`§7` of the blueprint requires (mission tagline, problem/outcome/why-adopt
copy), plus a helper that derives which departments are active for a given
business from its existing `Employee` rows.

**Why this order:** Every later phase — the ops console, the new dashboard,
the interest-capture table — reads department groupings from this registry.
Nothing else in the plan can be built correctly before this exists.

**Files affected:**
- Modify: `agent/employees.py` — promote `department` from a bare string tag
  to a reference into the new registry; rename the `"intelligence"`
  department key to `"leadership"` (resolves the naming mismatch the
  architecture review flagged between this file and the live `/roster`
  page, which already uses "Leadership"); mark exactly one department
  (`leadership`) as not independently hireable.
- Create: `agent/departments.py` — the `Department` registry (key, display
  name, one-line mission tagline, problem/outcome/why-adopt copy, member
  role keys, `hireable: bool`), plus `active_departments_for(employees:
  list[Employee]) -> list[Department]` and `department_for_role(role_key:
  str) -> Department`.
- Create: `agent/tests/test_departments.py`.

**What changes:** `employees.py`'s one `"intelligence"` occurrence becomes
`"leadership"`. Nothing else in the file's public shape changes — existing
`test_employees.py` assertions (exactly one `live` entry, unique keys) are
unaffected by a department-key rename.

**What stays unchanged:** `agent/db_models.py` (no schema change —
department stays a code concept, not a column, per the blueprint's §1
definition and the architecture review's explicit recommendation);
`roles.py`, `app.py`, `portal.py`, every webhook, every template — nothing
imports the new module yet, so nothing else can regress.

**Dependencies:** None.

**Risks:** Very low. Pure addition plus one internal rename in a file
nothing outside itself currently reads for that value.

**Required tests (`test_departments.py`):**
- Exactly 7 departments exist in the registry.
- Exactly one department (`leadership`) has `hireable == False`; all others
  are `True`.
- Every non-Leadership department has at least one member role key that
  exists in `employees.py`'s `REGISTRY`.
- Every department has non-empty mission, problem, outcome, and why-adopt
  copy (a blank field here is a real content bug, since Phase 5's inactive
  department cards render these fields directly).
- `department_for_role()` returns the correct department for every existing
  `EmployeeDefinition.key`, including `frontdesk`, `quote_chaser`, and
  `retention_manager`.
- Existing `test_employees.py` still passes unmodified.

**Completion criteria:** `pytest agent/tests/test_departments.py
agent/tests/test_employees.py` green; full existing suite green; a manual
diff confirms zero behavioral change anywhere outside the two touched files.

**Complexity:** Low.

---

## Phase 2 — Owner Notification Log

**Objective:** Give the future dashboard's Notifications page (blueprint §4,
§8) a durable, queryable record of owner alerts. Today, every owner alert
(booking confirmation, `alert_owner` escalation) is SMS-only and disappears
into a text thread the moment it's sent — there is no in-app record.

**Why this order:** Independent of Phase 1 (touches `notifications.py` and
a new table, not the department registry), so it can run in parallel with
it. It must land before Phase 5, which needs this data source to build the
Notifications page against.

**Why a new table instead of the existing `Event` table:** `Event` already
exists as a business-scoped append-only log, and reusing it was considered.
It's rejected for this specific need because nothing in the live SMS/voice
path currently calls `eventbus.bus.publish()` — wiring the `EventBus` into
the live request path is a materially larger, riskier change than a
Notifications tab requires, and writing directly into the `Event` table
without going through the bus that owns it would blur a boundary the
architecture review explicitly wants kept clean. A small dedicated table is
the lower-risk choice; wiring the EventBus into the live path remains
available as future work, independent of this migration.

> **Corrected 2026-07-29** after a pre-implementation audit of every owner-alert
> path. Five findings changed this phase's scope; full detail in the task plan,
> `docs/superpowers/plans/2026-07-29-phase-2-owner-notification-log.md`.

**Files affected:**
- Modify: `agent/db_models.py` — add `OwnerNotification` table: `id,
  business_id (FK), kind (str), message (str), **delivered (bool)**,
  created_at (datetime), read_at (datetime, nullable)`. The `delivered` flag
  is new to this correction: a failed owner SMS is currently swallowed by a
  bare `except` and is invisible everywhere, so "we tried and it failed" has
  to be distinguishable from "it went out."
- Modify: `agent/notifications.py` — add `record_owner_notification(session,
  ...)`, `recent_notifications(session, business_id)`, `is_test_thread()`,
  and extract `build_escalation_message()`. **The existing
  `notify_owner_of_*` senders keep their exact signatures and return
  values** — they must stay pure and database-free (see risks).
- Modify: **four** call sites, not one — `service.py:118` (SMS booking),
  `xai_voice_adapter.py:145` (voice booking), `:183` (the `alert_owner`
  tool), and `:247` (**the mid-call crash handler**, which was missed in the
  original scoping and is the alert most worth a durable record).
- Create: `agent/tests/test_owner_notification_log.py`. The existing
  `test_owner_notification.py` is **not** extended — its six tests run with
  no database at all, and must keep doing so.

**What changes:** Every existing owner-alert moment also writes a durable
row carrying whether the SMS actually went out. No new alert types.

**What stays unchanged:** SMS sending logic, every `notify_owner_of_*`
signature and return value, `engine.py`'s `alert_owner` tool contract, all
webhook behavior, and `db.py` (a new *table* needs no migration —
`create_all` handles it; only new *columns* need `_migrate_add_columns`).

**Dependencies:** None.

**Risks:** Low overall, with one sharp edge and one structural constraint:
- ⚠️ **`notify_owner_of_escalation`'s return value is load-bearing for a
  live caller.** It becomes `"owner_alerted"` vs `"alert_failed"` in the
  model's tool result, and `engine.py` instructs the voice agent to tell the
  caller plainly when the alert failed. A logging failure that flipped that
  bool would make the AI tell someone with a gas leak that the owner wasn't
  reached when they were. Logging is strictly downstream and cannot affect
  it; there is an explicit regression test.
- **Three of the four call sites run in a worker thread**
  (`asyncio.to_thread`). A `Session` is not thread-safe, so the log write
  stays on the calling thread with the session already in scope — it is never
  passed across the `to_thread` boundary.
- A notification-row write failure must never raise into, or roll back, the
  booking/escalation that already committed — the same best-effort posture
  `app.py`'s `complete_job` already uses for review sends.

**Required tests:** one per alert site (SMS booking, voice booking,
escalation tool, dropped call); a failed owner SMS still records an
undelivered row; dashboard-test bookings record nothing; a detail-merge
doesn't double-log; a broken log cannot change the escalation return value;
`recent_notifications` is newest-first and never returns another business's
rows; and all six pre-existing SMS tests stay green unmodified.

**Completion criteria:** Full suite green at 357 (from Phase 1's 340), zero
pre-existing tests modified, `git diff main -- agent/db.py` empty.

**Complexity:** Low.

---

## Phase 3 — Expansion-Interest Capture

**Objective:** Give every "Ask us about [Department]" moment in the
blueprint (§7 inactive cards, §8 Briefing recommendations, §4 Overview
prompts) a real place to record that interest, feeding Phase 4's ops
console "ongoing management" stage.

**Why this order:** Depends on Phase 1 (validates the department key
against the registry before writing it). Independent of Phase 2, so it can
run alongside it once Phase 1 is merged.

**Files affected:**
- Modify: `agent/db_models.py` — add `DepartmentInterest` table: `id,
  business_id (FK), department_key (str), created_at (datetime),
  actioned_at (datetime, nullable)`.
- Create: `agent/expansion.py` — `record_interest(session, business,
  department_key) -> DepartmentInterest`, validating `department_key`
  against Phase 1's registry and raising on an unknown key; idempotent per
  `(business_id, department_key)` while `actioned_at` is null (a repeat
  click before the first request is handled doesn't create a duplicate).
- Create: `agent/tests/test_expansion.py`.

**What changes:** Nothing existing — new table, new module, nothing wired
into any live route yet (Phase 5 wires the customer-facing write path;
Phase 4 wires the founder-facing read path).

**What stays unchanged:** Everything else.

**Dependencies:** Phase 1.

**Risks:** Low. The one edge case worth a named test: a customer clicking
the same department's CTA twice before Roster has acted on the first
request must not create two open rows.

**Required tests (`test_expansion.py`):**
- Recording interest in a valid, hireable department succeeds.
- Recording interest in `leadership` (not hireable) or an unknown key
  raises — protects the ops console from garbage data.
- A second call for the same business+department before `actioned_at` is
  set returns/reuses the existing row rather than duplicating.
- A call after `actioned_at` is set creates a new row (a business that
  already expanded once can ask again later).

**Completion criteria:** Full suite green.

**Complexity:** Low.

---

## Phase 4 — Ops Console: Department Grouping + Pipeline Stage

**Objective:** Build the internal tooling half of the blueprint's §10a —
group each business's employees by department in the founder-admin view,
and add the lead → discovery → provisioning → QA → go-live → ongoing-
management pipeline stage that the blueprint's Contact Us → Internal
Onboarding journey (§3) depends on.

**Why this order:** Depends on Phase 1 (grouping helper) and Phase 3 (list
of open `DepartmentInterest` rows to surface on the business detail page).
Must land before Phase 6 (cutover) because retiring self-serve onboarding
without a working founder-side provisioning path first would leave no way
to onboard anyone. Can be built in parallel with Phase 5 — disjoint files,
disjoint audience (founder vs. customer).

**Files affected:**
- Modify: `agent/db_models.py` — add `Business.pipeline_stage` column
  (`str`, default `"lead"`; values: `lead | discovery | provisioning | qa |
  live | managed`). Existing seeded/test businesses with `frontdesk_live ==
  True` are backfilled to `"live"` on migration so nothing existing appears
  stuck at `"lead"`.
- Modify: `agent/app.py` —
  - `/clients` and `/clients/{id}` now group employees by department (via
    Phase 1's `active_departments_for`) and list open `DepartmentInterest`
    rows for that business.
  - `/clients/{id}/employees/deploy` gains an additional accepted input,
    `department_key` — deploying by department activates every role in it
    that isn't already active. The existing `role_key`-based call remains
    valid and unchanged (backward-compatible, additive parameter — this is
    the plan's one deliberate temporary compatibility layer at the ops
    layer; see the consolidated list below).
  - New route: `POST /clients/{id}/pipeline-stage` — advances a business's
    stage; rejects invalid backward transitions unless explicitly
    overridden.
  - `/clients/new` becomes the **single business-creation path in the entire
    product** (per the one-onboarding-flow constraint — `/signup` is retired
    outright in Phase 7 with no founder-only replacement). It must therefore
    capture everything the retired self-serve wizard captured, plus the
    department(s) the discovery call recommended. This is a hard
    prerequisite for Phase 6: the moment self-serve closes, this is the only
    door left.
  - ⚠️ **`/clients/{id}/employees/deploy` must create `Employee` rows
    directly** *(found during the Phase 3 audit, 2026-07-29)*. It currently
    only appends to `requested_roster` and stops — unlike `portal.py`'s
    `_hire_employee`, it never inserts an `Employee`. Rows appear solely via
    `db.py:_backfill_employees` on the next boot, so a founder-deployed
    business would show **zero departments** on its dashboard until the
    process restarts, because Phase 1's `active_departments_for()` reads
    `Employee` rows. Phase 5's dashboard depends on this fix.
  - Surface open expansion requests via Phase 3's
    `expansion.open_interests_for()`, and close them with
    `expansion.mark_actioned()` once handled.
- Modify: `agent/templates/client_detail.html` — department-grouped
  employee display; pipeline-stage control; open `DepartmentInterest` list.
- Modify: `agent/templates/new_client.html` — department selection at
  creation; parity with everything the old onboarding wizard collected.
- Modify: `agent/templates/clients.html` — list view groups/filters by
  pipeline stage.
- Create: `agent/tests/test_pipeline_stage.py`.

**What changes:** Founder-admin templates; the deploy route's accepted
input shape (additively).

**What stays unchanged:** Every webhook, `portal.py`, every customer-facing
route, the HTTP-Basic founder-auth model.

**Dependencies:** Phases 1, 3.

**Risks:** Medium — first phase to touch a route real founder workflow
depends on today (`/clients/{id}/employees/deploy`). Mitigated by keeping
the existing call shape valid rather than replacing it, and by zero real
paying customers today (`CUSTOMER.md`) lowering the cost of a mistake here
to "founder inconvenience," not "customer outage."

**Required tests:**
- The department-grouping helper renders every active employee under the
  correct department for a seeded multi-department test business.
- Deploying by `department_key` activates every role belonging to that
  department that wasn't already active.
- Deploying by legacy `role_key` still works exactly as before (regression
  test against existing `test_employee_deploy.py` behavior).
- Pipeline-stage transitions reject an invalid skip/backward move (e.g.
  `live → lead`) without an explicit override flag; accept the documented
  forward sequence.
- `/clients/new` creates a business carrying every field the retired
  self-serve wizard collected (business name, trade, services, hours,
  pricing/FAQ, escalation phone, answer mode) — a field-by-field parity
  test, since after Phase 7 there is no other way to create a business and a
  silently-dropped field becomes an unprovisionable customer.
- Existing `test_provisioning.py`, `test_employee_deploy.py` pass
  unmodified.

**Completion criteria:** Full suite green; a founder can manually **create**
a business via `/clients/new` and walk it through every pipeline stage in
the admin UI end to end, with a department deployed, without touching
`/signup` or `/onboarding/*` at any point.

**Complexity:** Medium.

---

## Phase 5 — New Customer Dashboard, Built Unlinked

**Objective:** Build the full new customer IA from the blueprint's §4 —
Overview, Departments (with the §7 educational inactive-department cards),
department detail pages (§6's outcomes → activity → team hierarchy), the
working-name "Briefing" page (§4a/§9), Notifications, Settings — as new
templates and routes, without touching the live `/dashboard` route. This
repeats the exact pattern this repo already used successfully for the
landing page (`index-v2.html` + `/preview`, built alongside `index.html` +
`/`, cut over later via a one-line route change).

**Why this order:** Depends on Phase 1 (registry + copy for inactive
cards), Phase 2 (Notifications data source), and Phase 3 (the CTA needs
somewhere to write). Does not depend on Phase 4 — it can be built and fully
tested against seeded data independently of whether the founder-admin UI
has shipped, since both consume the same Phase 1 registry directly. This is
the plan's largest phase by volume of new surface area, and its risk is
contained entirely by being unreachable from any live URL until Phase 6.

**Files affected:**
- Create: `agent/templates/dashboard_v2/overview.html`,
  `departments.html`, `department_detail.html`, `briefing.html`,
  `notifications.html`, `settings.html`.
- Modify: `agent/portal.py` — add parallel routes: `GET /v2/dashboard`, `GET
  /v2/dashboard/departments`, `GET /v2/dashboard/departments/{key}`, `GET
  /v2/dashboard/briefing`, `GET /v2/dashboard/notifications`, `GET
  /v2/dashboard/settings`, `POST /v2/dashboard/departments/{key}/interest`
  (calls Phase 3's `record_interest`). All read the same session/business as
  today's `/dashboard` — the existing route and its handlers are untouched.
- Create: `agent/briefing.py` — pure read/aggregation module: cross-
  department outcome totals for a business, and the rules-based
  growth-nudge check described in blueprint §8 (e.g. "N estimates sent, 0
  followed up in 30 days"). **No LLM call in this phase** — a
  Claude-narrated Briefing is a future enhancement layered on top of this
  module later, not required to satisfy the blueprint's committed v1
  behavior, and adding one now would be scope creep against founder
  requirement #3 (avoid large rewrites).
- Create: `agent/tests/test_dashboard_v2.py`, `agent/tests/test_briefing.py`.

**What changes:** Nothing live-facing. Entirely new, parallel routes.

**What stays unchanged:** `/dashboard`, `/onboarding/*`, `/roster/hire*`,
and every existing template — all continue serving exactly as they do
today, for the full duration of this phase.

**Dependencies:** Phases 1, 2, 3.

**Risks:** Low in isolation (nothing live depends on this yet), but it is
the largest phase in the plan by template/route count — mitigated by
building and fully testing it unlinked before any cutover risk (Phase 6) is
taken, and by DESIGN.md gating the visual craft pass so IA and visual
design aren't conflated into one giant review.

**Required tests:**
- Overview renders correct cross-department outcome totals for a seeded
  multi-department business.
- Departments page renders active departments with real outcome data, and
  renders every inactive department with full educational copy (problem,
  outcome, why-adopt) from Phase 1's registry — never a bare "coming soon."
- Department detail page renders sections in the literal order outcomes
  strip → activity feed → team roster (this is the test that guards the
  blueprint's §2 permanent design principle from regressing).
- Notifications page reads and correctly orders Phase 2's
  `OwnerNotification` rows.
- `test_briefing.py`: the growth-nudge rule fires on its documented trigger
  condition and explicitly does **not** fire on a business with zero
  eligible data (a false-positive-guard test, not just a happy-path test).
- The interest-capture POST route calls Phase 3's `record_interest` and
  handles its idempotency/validation behavior correctly at the HTTP layer
  (400 on invalid department, 200/redirect on success).

**Completion criteria:** Full suite green; every `/v2/dashboard*` route
manually verified correct against a seeded test business, with `/dashboard`
continuing to serve the old UI unchanged throughout the entire phase.

**Complexity:** High.

---

## Phase 6 — Cutover: Dashboard Live, Self-Serve Retired

**Objective:** Point the live customer experience at the new IA and retire
the self-serve onboarding/hire code paths the blueprint rules out.

**Why this order:** Requires both Phase 4 (a real founder-side provisioning
path must already exist before self-serve is removed, or there is a gap
with no way to onboard anyone) and Phase 5 (fully verified) to be merged.
This is deliberately its own phase, separate from Phase 7's deletions, so a
cutover problem can be fixed by reverting routing alone without touching
the (larger, harder-to-review) deletion diff.

**Files affected:**
- Modify: `agent/portal.py` — `/dashboard` and its sub-paths now render
  what `/v2/dashboard*` rendered (either by re-pointing the route
  registration or, more simply, deleting the `/v2` prefix now that there is
  only one dashboard — either mechanism is acceptable, engineer's choice at
  task-planning time). `/signup`, `/onboarding/business`,
  `/onboarding/receptionist` become redirects to the public Contact Us flow
  rather than rendering their forms — **kept as redirects this phase, not
  deleted**, so a stale bookmark or an old cold-email link degrades
  gracefully instead of 404ing.
- Modify: `agent/portal.py` — **`_login_or_create_by_email()` becomes
  login-only.** Today it silently creates a `Business` for any verified
  Google email that has no account (`portal.py:142-164`). That is a second
  self-serve account-creation path hiding inside the OAuth flow, and it
  violates the one-onboarding-flow constraint just as much as `/signup`
  does. After this phase an unknown Google email is sent to Contact Us, not
  handed an account. **Closing `/signup` without closing this leaves the
  front door open.**
- Modify: `agent/tests/test_google_auth.py`, `agent/tests/test_portal_login.py`
  — assert an unknown verified Google email creates **no** `Business` and
  lands on Contact Us; assert a known email still logs in normally.
- Modify: `agent/app.py` — `/request-access` gains one new optional form
  field (the blueprint §3 "tell us what's going on" free-text prompt); one
  new nullable column on `AccessRequest` (`pain_point`).
- Modify: `agent/templates/request_thanks.html` — copy update if needed to
  reflect the new field.
- Modify: `agent/tests/test_portal_dashboard.py`,
  `test_portal_onboarding.py` — assert the new redirect behavior for
  onboarding routes; assert `/dashboard` now serves the new IA.

**What changes:** Which template renders at `/dashboard`; onboarding routes
become redirects; `AccessRequest` gains one column.

**What stays unchanged:** Login/logout/auth, every webhook, the entire
engine/service/channel layer, `activate_frontdesk()` (still runs during
founder-led provisioning from Phase 4's flow — it's simply never triggered
by a self-serve form again).

**Dependencies:** Phase 4 and Phase 5, both fully merged and green.

**Risks:** **Highest in this plan** — the one phase that changes what a
real (future) customer sees. Mitigated by: zero paying customers today per
`CUSTOMER.md`, so there is no live session to break; old routes degrade to
a redirect rather than vanishing outright; and Phase 5 was already fully
verified in isolation before this phase touches anything reachable from a
public URL.

**Required tests:**
- Full existing dashboard/onboarding suite updated and green against the
  new behavior — not skipped, since this is precisely where a regression
  would hide.
- One end-to-end test: an `AccessRequest` is submitted → a founder advances
  it through Phase 4's pipeline stages → a department is deployed → the
  resulting business logs in → `/dashboard` shows the new IA reflecting
  that department correctly.

**Completion criteria:** Manually verified end to end on the deployed
environment (not tests alone): a fresh test business goes from a Contact Us
submission to seeing its department live on `/dashboard`, and the old
`/signup`/`/onboarding/*` URLs redirect rather than error.

**Complexity:** Medium — the hard design and build work already happened
in Phase 5; this phase is primarily routing and verification.

---

## Phase 7 — Remove Legacy Code

**Objective:** Delete what Phase 6 made unreachable, so the codebase
reflects the blueprint with no dead parallel paths left to confuse the next
engineer.

**Why this order:** Deliberately separated from Phase 6 by at least one
observed deploy cycle — cutover and deletion must never be the same
commit, so a cutover problem can be fixed by reverting Phase 6 alone,
without needing to also resurrect deleted files.

**Files affected (delete):**
- `agent/templates/onboarding_business.html`
- `agent/templates/onboarding_receptionist.html`
- `agent/templates/roster_hire_retention_manager.html`
- `agent/templates/signup.html` — retired outright, **no founder-only
  survival** (founder decision, 2026-07-28; see the decision record at the
  end of this phase)
- `agent/templates/dashboard.html` (old version; `dashboard_v2/` is renamed
  to the canonical `dashboard/` in this same phase, now that only one
  exists)
- `agent/roles.py` — remove `ROSTER_HIRE_ORDER`, `next_hire()`,
  `coming_later_after()`, `ROSTER_DESCRIPTIONS`. `receptionist_display_name`
  and `ROLE_KEYS`/`role_key_for` are retained (still used for trade-aware
  naming and role-key resolution) — moved into `departments.py` or kept in
  a trimmed `roles.py`, engineer's call at task-planning time.
- `agent/portal.py` — remove the `/roster/hire`, `/roster/hire/retention-
  manager` routes and their handlers, `_hire_employee()`, the `/signup`
  routes (`signup_form`, `signup_submit`), and the Phase 6 transitional
  onboarding redirects once they have served their purpose. Login/logout and
  the (now login-only) Google OAuth routes stay.

**What changes:** Deletions only. No new behavior is introduced in this
phase.

**What stays unchanged:** Everything not explicitly listed above — in
particular `engine.py`, `service.py`, channel adapters, recovery/referral
engines, `BusinessMemory` remain untouched by the entire migration, exactly
as both the blueprint and the architecture review concluded they should.

**Dependencies:** Phase 6, observed stable through at least one deploy
cycle before this phase starts.

**Risks:** Low, contingent on the sequencing above. The main failure mode
this guards against — deleting a route something external still depends on
— is covered by Phase 6 already having converted those routes to redirects
first rather than hard-deleting them immediately.

**Required tests:**
- Full suite green with obsolete tests/fixtures also removed or rewritten
  (e.g. any onboarding-wizard-specific cases in `test_portal_signup.py`
  either deleted or rewritten against the Contact Us flow).
- A repo-wide search for `ROSTER_HIRE_ORDER`, `/roster/hire`,
  `onboarding_receptionist` returns no hits outside historical spec/plan
  documents (this document and the migration review are expected,
  intentional hits).

**Completion criteria:** Full suite green; the search above is clean; the
app boots and serves normally with no 404s on any route that was live at
the end of Phase 6.

**Complexity:** Low.

**Decision record (founder, 2026-07-28 — this phase's former open
question, now closed):** `/signup` is **fully retired**. It does not
survive as a founder tool. Every customer begins at Contact Us and is
created and provisioned by the Roster team through the internal ops
platform (`/clients/new`); **there is exactly one onboarding flow, and no
internal exception to it.** The reasoning, recorded so it isn't reopened: a
second path that exists "only for the team" still has to be maintained,
tested, and reasoned about forever, and it quietly becomes the path of
least resistance under time pressure — which is how one flow silently
becomes two. Engineering optimizes for the final product, not for
preserving a legacy workflow.

Two consequences that fall out of this decision and are handled elsewhere
in the plan rather than here:
- Phase 4 must bring `/clients/new` to full parity with the retired wizard
  before Phase 6 closes the self-serve door.
- Phase 6 must make Google OAuth login-only, since it currently creates a
  `Business` for any unknown verified email — a second signup path hiding
  inside the login flow.

---

## Phase 8 — Documentation Closeout

**Objective:** Bring `ROADMAP.md`, the PRD, and `DESIGN.md` up to date with
what actually shipped, as distinct from what Phase 0 recorded as intent.

**Why this order:** Describes what was built, not what was planned — must
follow Phase 7, the last phase that changes any code.

**Files affected:**
- Modify: `ROADMAP.md`
- Modify: `docs/superpowers/specs/2026-07-13-roster-platform-architecture-prd.md`
- Modify: `DESIGN.md` — decision-log entry, per `CLAUDE.md`'s standing rule,
  covering the dashboard's shipped visual craft.

**What changes:** Dated log entries recording what shipped.

**What stays unchanged:** N/A.

**Dependencies:** Phase 7.

**Risks:** None.

**Required tests:** None.

**Completion criteria:** No doc in the repo still describes the old
employee-first dashboard or self-serve onboarding as current behavior
(historical spec files are exempt — they're records, not instructions).

**Complexity:** Low.

---

## Consolidated Reference

### Legacy code removed at the end (Phase 7)
- `agent/templates/onboarding_business.html`, `onboarding_receptionist.html`
- `agent/templates/roster_hire_retention_manager.html`
- `agent/templates/signup.html`
- `agent/templates/dashboard.html` (old version)
- `roles.py`: `ROSTER_HIRE_ORDER`, `next_hire()`, `coming_later_after()`, `ROSTER_DESCRIPTIONS`
- `portal.py`: `/roster/hire`, `/roster/hire/retention-manager`,
  `_hire_employee()`, `/signup` routes, and Google OAuth's
  create-on-unknown-email behavior (the function survives as login-only)

### Temporary compatibility layers needed during migration
- Phase 6's onboarding-route redirects (`/signup`, `/onboarding/*`) — live
  through Phase 6, removed in Phase 7.
- `/clients/{id}/employees/deploy`'s dual input (`role_key` legacy +
  `department_key` new) — live from Phase 4 through Phase 7, since it's
  founder-only tooling with no external caller to worry about beyond the
  admin UI itself.
- Dual dashboard routes (`/dashboard` + `/v2/dashboard`) — live from Phase
  5 through Phase 6, the core "build alongside, don't touch live" mechanism
  of this entire plan.

### Database migrations (all additive, via the existing mechanism in `db.py`)
| Change | Phase | Notes |
|---|---|---|
| New table `OwnerNotification` | 2 | `business_id, kind, message, created_at, read_at` |
| New table `DepartmentInterest` | 3 | `business_id, department_key, created_at, actioned_at` |
| New column `Business.pipeline_stage` | 4 | Default `"lead"`; backfilled to `"live"` for existing `frontdesk_live` businesses |
| New column `AccessRequest.pain_point` | 6 | Nullable |

No column or table is ever dropped by this plan. `Employee` gets no new
column — department stays a code concept (Phase 1), not schema.

### Routes/pages that disappear (end state, after Phase 7)
`GET/POST /signup`, `GET/POST /onboarding/business`, `GET/POST
/onboarding/receptionist`, `POST /roster/hire`, `GET/POST
/roster/hire/retention-manager`. Google OAuth's routes survive but stop
creating accounts. After Phase 7 there is exactly one way a business comes
into existence: a Roster team member creating it in the ops platform.

### New routes/pages introduced
`GET /dashboard/departments`, `GET /dashboard/departments/{key}`, `GET
/dashboard/briefing`, `GET /dashboard/notifications`, `GET
/dashboard/settings` (temporarily under a `/v2` prefix during Phases 5–6),
`POST /dashboard/departments/{key}/interest`, `POST
/clients/{id}/pipeline-stage`. `/request-access` is extended, not new.

### Components that can be reused (untouched, entire migration)
`engine.py`, `service.py`, `memory.py` (`BusinessMemory`), `repositories.py`,
`channels.py`, `xai_voice_adapter.py`, `call_trace.py`,
`recovery_engine.py`/`recovery_service.py`, `referral_engine.py`/
`referral_service.py`, `auth.py`, `google_auth.py`, `activate_frontdesk()`,
`portal.css`/`DESIGN.md` tokens, and `roster.html`'s existing department
tagline copy (reused verbatim as the Phase 1 registry's source text).

### Components that should be replaced
`dashboard.html`'s fixed three-slot "Your office" card; `roles.py`'s
hardcoded hire sequencing; the self-serve onboarding wizard
(`onboarding_business.html`, `onboarding_receptionist.html`);
`roster_hire_retention_manager.html`.

---

## Self-Review

- **Spec coverage:** every blueprint section (§1–§13) maps to at least one
  phase above — core abstraction and design principle (Phases 1, 5),
  customer journey (Phases 4, 5, 6), dashboard IA and navigation (Phase 5),
  department pages active/inactive (Phase 5), daily workflow (Phases 2, 5),
  collaboration (unchanged substrate — no phase needed, confirmed already
  built), internal ops (Phase 4), billing architecture (no phase required —
  §11 is explicitly architecture-only with no v1 UI/backend commitment
  beyond the contextual CTAs already covered by Phase 3/5).
- **Placeholder scan:** no TBD/TODO remains; the one open item (Phase 7's
  `/signup` question) is a named, bounded decision with both outcomes
  described, not an unresolved gap.
- **Consistency check:** `agent/departments.py`'s function names
  (`active_departments_for`, `department_for_role`) and the new tables'
  field names (`OwnerNotification`, `DepartmentInterest.department_key`,
  `Business.pipeline_stage`) are used identically everywhere they recur
  across Phases 1–6.
