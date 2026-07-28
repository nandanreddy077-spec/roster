# Departments Repositioning — Architecture Review & Migration Plan

**Date:** 2026-07-28
**Status:** Analysis only. No code, schema, or template changes made.
**Requested by:** Founder — product vision has changed: Roster moves from an
employee-first, self-serve model to a department-first, founder-provisioned
model.

---

## 0. Read this first — a direct conflict with a decision made 7 days ago

Before the audit: the new vision is not landing on a blank slate. It reverses
an **explicit, founder-approved decision from 2026-07-21**
(`docs/superpowers/specs/2026-07-21-ai-staffing-repositioning-design.md`,
"Status: Approved by founder"):

> "Per founder correction: departments may be reorganized later; **the
> employees Roster commits to are what matters.** The registry is
> employee-first, with `department` as a descriptive tag, **not a structural
> entity**."

That spec was itself a response to an earlier pasted document ("Roster
Platform Refactor — From AI Receptionist to AI Staffing Company") that
proposed almost exactly the Office → Departments → Employees model requested
now — and it was deliberately narrowed down to "employee registry, not a
department org chart" (§4 of that spec, its section heading literally says
this).

Separately, `docs/superpowers/specs/2026-07-13-roster-platform-architecture-prd.md`
declares the platform **architecture frozen**: *"every architectural change
must be justified by a real pilot or paying customer... If a change is
proposed with no customer behind it, the default answer is no."*
`CUSTOMER.md` confirms **0 paying customers, 0 active pilots** as of this
writing.

This isn't a reason to refuse the new direction — it's the founder's call to
make, and founders change direction on real information. It's flagged
because:

1. Two other governing docs (`ROADMAP.md`'s build freeze, the PRD's frozen-architecture
   clause) will read as contradicting this move to any fresh session that
   opens them — they need updating **as part of** this migration, not after.
2. It means departments-as-structure was already considered and explicitly
   rejected once. §7 addresses what's different now vs. 2026-07-21, so the
   reversal is a deliberate one, not an oversight.
3. The marketing layer has been quietly drifting toward department-first
   language for a week (`/roster` page, `index-v2.html`'s "Fourteen more
   roles, seven departments") while the product layer stayed employee-first.
   This migration is as much *resolving an internal inconsistency that
   already exists* as it is a new pivot.

---

## 1. Current architecture — summary

**Stack:** Single-process FastAPI monolith (`agent/`), SQLModel + SQLite
(Railway volume), server-rendered Jinja templates, no SPA/JS framework.

**Aggregate root:** `Business` (table literally still named `business`,
model class `Business` — the PRD's planned `Client`→`Business` rename already
happened). Everything hangs off `business_id`.

**Data model that exists today** (`agent/db_models.py`):
`Business`, `Customer`, `Message`, `Job`, `Employee` (flat: `business_id,
role_key, display_name, status, policy_json, hired_at, fired_at` — **no
`department` column**), `Event`, `WebhookDelivery`, `RecoveryCampaign` /
`RecoveryJob` / `RecoveryMessageLog`, `ReferralLead`, `AccessRequest`.

**What actually runs today (the live path):** a customer texts/calls →
Twilio/xAI webhook → `service.handle_customer_message()` → `engine.py`'s
`AgentEngine` (a bounded Claude tool-use loop) → `log_job` tool → `Job` row →
SMS to the owner (`notifications.py`). This path **does not** go through
`eventbus.py` or `runner.py` at all.

**What's scaffolded but not wired:** `eventbus.py` (`EventBus.publish`),
`events.py` (event-type constants), `runner.py` (`RoleDefinition` /
`dispatch_job_completed`) exist as seams from the 2026-07-13 PRD but are
inert — `runner.py`'s own docstring says it is "kept deliberately EMPTY of
registered employees," and `JOB_COMPLETED_ROLES = []`. Nothing calls
`eventbus.bus.publish()` in the live request path.

**The "shared company memory" substrate:** `memory.py`'s `BusinessMemory`
port and `repositories.py`'s `get_or_create_customer` are real and load-bearing
today — `Customer`/`Job`/`Message` are already scoped by `business_id`, not
by employee, so **any** capability that queries through this layer already
sees the same business-wide record. This is the one piece of the new vision
that is already structurally true, not aspirational.

**Product surface today (`agent/portal.py`, `agent/app.py`):**
- Public: landing (`index-v2.html`), `/roster` (a static department/org-chart
  page — see §2.5), `/signup`, `/login`, `/request-access` (the actual live
  funnel).
- Owner portal (session cookie, one `Business`): `/onboarding/business` →
  `/onboarding/receptionist` → `activate_frontdesk()` → `/dashboard`.
  Dashboard shows a **fixed three-slot "Your office" card**: Frontdesk (live),
  then exactly one "Discuss Your Next AI Hire" prompt (Quote Chaser, then
  Retention Manager), driven by `roles.py`'s `next_hire()` sequencing.
  `/roster/hire` and `/roster/hire/retention-manager` **still execute
  self-serve** — they create an `Employee` row immediately on the customer's
  click (`portal.py:_hire_employee`); only the button *copy* changed to
  "Discuss Your Next AI Hire" on 2026-07-21, the route behavior didn't.
- Founder admin (HTTP Basic, `/clients*`): business list, per-business detail,
  manual number provisioning, `/clients/{id}/employees/deploy` (the one place
  that matches the PRD's "Founder Admin configures" principle for real).

**Registries that exist purely as documentation, wired into nothing:**
`agent/employees.py` — a 17-entry `EmployeeDefinition` list across 7
departments (Customer Service, Sales, Operations, Finance, Customer Success,
Marketing, Intelligence — note: **Intelligence**, not "Leadership," see §5)
tagged `live`/`internal`/`planned`. Its own docstring: *"Nothing in the
running app reads this module yet."*

**Marketing already has a full department org-chart** (`agent/landing/roster.html`,
route `/roster`) with 7 departments matching the user's list almost exactly
(it uses "Leadership" too, interestingly, unlike `employees.py`'s
"Intelligence") — but this is a static, unlinked-from-backend HTML page built
explicitly for "belief-building for the curious/investors," per
`DESIGN.md`'s 2026-07-22 decision log entry. It renders 20 employee names
across 7 departments, **none of which exist as `Employee` rows for any real
business** — it's illustrative copy, not a reflection of deployed state.

---

## 2. Every place the current product assumes the old vision

### 2.1 "AI receptionist" framing
- `docs/superpowers/specs/2026-07-01-ai-receptionist-design.md` — the
  original spec, titled "AI Receptionist (Vapi voice agent) — Design." Historical
  document; superseded in practice (Vapi was later replaced by the xAI voice
  adapter) but never marked superseded in its own header.
- **Conflict:** none live today — `SALES.md`, `ROSTER.md`, and the landing
  copy already reject "AI receptionist" language ("Not 'an AI receptionist,'
  not 'call-answering software.'" — `SALES.md`). This is the one old-vision
  assumption that's **already fully resolved** at the positioning layer. Only
  the historical spec file itself still carries the name.

### 2.2 Employee-first structural model (the core conflict)
- `agent/db_models.py`'s `Employee` table has no department field — an
  employee is a flat `(business_id, role_key, status)` tuple. Department
  literally cannot be queried from the database today.
- `agent/roles.py` — `ROSTER_HIRE_ORDER`, `next_hire()`, `coming_later_after()`
  implement **strict one-employee-at-a-time sequencing** (Receptionist →
  Quote Chaser → Retention Manager). There is no concept of "hire the Sales
  department" — hiring is defined entirely in terms of individual named roles.
- `agent/templates/dashboard.html` — per the DESIGN.md 2026-07-21 log entry,
  shows a **fixed three-slot** office card naming Frontdesk/Quote
  Chaser/Retention Manager individually, not grouped by department.
- `agent/templates/roster_hire_retention_manager.html` — a dedicated hire
  flow for one specific named employee.
- **Conflict:** the new vision says "customers hire departments... employees
  are an implementation detail... the customer interacts with departments."
  Every customer-facing surface today does the opposite: it names individual
  employees as the unit of sale, trust, and progression, and has no
  department-level grouping anywhere in the data model or UI.

### 2.3 Self-serve onboarding / software-configuration surface
- `agent/portal.py` — the entire `/signup` → `/onboarding/business` →
  `/onboarding/receptionist` → `activate_frontdesk()` flow lets a customer
  configure and activate their own AI employee with zero founder involvement,
  including live Twilio number purchase (`activation.py`).
- `agent/templates/onboarding_business.html`, `onboarding_receptionist.html`
  — literal configuration forms (business name, trade, services, hours,
  pricing/FAQ, escalation phone, answer mode).
- `agent/portal.py`'s `/roster/hire` and `/roster/hire/retention-manager` —
  as noted in §1, these still instantly create an `Employee` row on a
  customer's own click. This is software self-configuration in substance,
  even though the button now reads "Discuss Your Next AI Hire."
- **Important nuance already in the docs:** `/signup` is **not linked
  anywhere on the live site** as of the 2026-07-16/07-21 pivots — the
  standing default funnel is already `/request-access` → founder follow-up →
  founder manually walks the owner through `/signup` or `/clients/new`. So
  the *routing/discovery* of self-serve signup is already dead; the
  *self-serve code path itself* is still fully live and reachable by anyone
  with the URL (a returning customer who bookmarked `/signup`, or the founder
  using it deliberately during a discovery call).
- **Conflict:** the new vision's steps 1–3 ("business contacts Roster → our
  team sets everything up → customer receives dashboard access") describe
  something closer to the *current de facto default funnel* than to the
  *code path* — the request-access funnel already matches the vision at the
  routing layer; the self-serve forms/routes are the part that's structurally
  misaligned and still executable.

### 2.4 Software-configuration mental model in copy
- `activation_live.html`, `dashboard.html`'s status ladder ("Ready — try it
  before you trust it" → "Working — you've seen it answer") are deliberately
  written to *avoid* sounding like software configuration (this was a genuine
  2026-07-11 design fix, see `DESIGN.md`). This one is **already aligned**
  with "feels like hiring, not configuring" — worth preserving, not
  discarding, in any redesign.

### 2.5 Frontdesk-first workflow
- `roles.py`'s `ROSTER_HIRE_ORDER` hardcodes Frontdesk as the mandatory first
  hire for every business, no exceptions.
- **Direct textual conflict:** `SALES.md`'s own "Customer Onboarding
  Principle" (added 2026-07-21) already says the opposite in prose: *"Roster
  does not assume every customer starts with the same AI employee... We
  identify the customer's biggest operational bottleneck... and recommend the
  AI employee most likely to solve that problem."* The code has never
  implemented this — `next_hire()` is unconditional — the principle exists
  today only as a sentence in a markdown file, contradicted by
  `roles.py`'s actual logic.
- **Conflict with new vision:** a department-first model plus genuine
  discovery-driven hiring (already-written intent) both push toward
  "whichever department is deployed for this business, is what the dashboard
  shows" — not a fixed Frontdesk-first ladder. This is a case where the new
  vision is *finishing* an already-declared but never-implemented principle,
  not inventing a new one.

---

## 3. Categorisation

Legend: **KEEP** (already aligned) · **MODIFY** (sound but built on
old-vision assumptions — narrow, targeted change) · **REPLACE** (concept is
right, current implementation's assumptions don't survive — rewrite the
body, may keep the file) · **DELETE** (no longer valuable).

### 3.1 Backend core — infrastructure, ports, engine

| File | Class | Why |
|---|---|---|
| `db.py` | KEEP | DB engine/session setup, migrations mechanism — vision-agnostic. |
| `eventbus.py` | KEEP | Sync EventBus is exactly the seam a multi-department, shared-memory system needs. Currently unwired to the live path — that's an opportunity, not a defect. |
| `events.py` | KEEP | Event-type constants; add department-relevant event types as needed (additive). |
| `memory.py` (`BusinessMemory`) | KEEP | Already business-scoped, not employee-scoped — this **is** "shared company memory across departments," already built. The one piece of the new vision that's furthest along. |
| `repositories.py` | KEEP | `get_or_create_customer` — reusable regardless of department structure. |
| `engine.py` (`AgentEngine`) | KEEP | The Think→Act→Observe loop, tool-use pattern, and prompt-injection defenses are department-agnostic — any employee, in any department, is a capability that calls this same engine. |
| `service.py` (`handle_customer_message`) | MODIFY | Correct for Frontdesk today. Needs to become one *capability* invoked by a Runner/department dispatch rather than the hardcoded, only entry point — but the SMS/voice conversation logic itself doesn't change. |
| `runner.py` | REPLACE (expand) | Right shape (`RoleDefinition` + dispatch), wrong scope (`JOB_COMPLETED_ROLES` only, deliberately empty). This is where department-aware dispatch belongs — the seam is correct, the body needs to grow from "one hook" to "the real router." |
| `roles.py` | REPLACE | `ROSTER_HIRE_ORDER`'s hardcoded Frontdesk-first, one-at-a-time sequence is the single most direct piece of code contradicting the new vision. `receptionist_display_name`'s trade-naming lookup is salvageable and department-agnostic. |
| `employees.py` | MODIFY | The registry shape (`key, department, status, display_name`) is *already* department-aware — it just needs `department` promoted from "descriptive tag" to a real grouping key the app reads, plus reconciling with `roster.html`'s "Leadership" vs. this file's "Intelligence" naming mismatch. |
| `db_models.py` (`Employee`) | MODIFY | Add nothing structural yet if departments stay a code registry (recommended, §6) — but if the founder wants department stored per-hire (e.g. for reporting), this needs a `department` column or a derived property from `role_key`. |
| `activation.py` | KEEP | Number provisioning / graceful degradation logic is orthogonal to who's "hiring" — reusable for any employee needing a phone number. |
| `provisioning.py`, `calendar_provider.py`, `bookings.py`, `notifications.py`, `trial_cap.py`, `locks.py`, `auth.py`, `google_auth.py` | KEEP | Plumbing/integration layer — genuinely vision-agnostic. |
| `xai_voice_adapter.py`, `channels.py`, `call_trace.py` | KEEP | Channel/transport layer — a department-first Frontdesk still needs to answer real phone calls the same way. |
| `recovery_engine.py`, `recovery_service.py`, `recovery_tick.py`, `referral_engine.py`, `referral_service.py` | KEEP (re-badge only) | These already implement Quote Chaser / Retention Manager as engines. Under the new vision they become "capabilities inside the Sales / Customer Success departments" — no logic change, just where they're surfaced from. |
| `seed.py`, `simulate.py`, `conftest.py`, `tests/*` | KEEP | Test/dev infra; update fixtures as the product layer changes, not before. |
| `models.py` (`ClientConfig`) | KEEP | Small DTO, vision-agnostic. |

### 3.2 Product / HTTP layer

| File | Class | Why |
|---|---|---|
| `app.py` | MODIFY | Webhook handling, auth, admin routes are sound. The `/roster/hire`-equivalent admin route (`/clients/{id}/employees/deploy`) is **already the correct department-era pattern** ("Founder-admin action: the only place an employee gets deployed" — its own docstring). Needs a department-aware view, not new philosophy. |
| `portal.py` | REPLACE (the onboarding/hire section only) | `/signup`, `/onboarding/business`, `/onboarding/receptionist`, `/roster/hire*` are the self-serve, employee-first hire flow the new vision explicitly rules out. Auth (`/login`, `/logout`, Google OAuth), the dashboard *read* view, and `/dashboard/test` are sound and reusable. |
| `agent/employees.py`'s registry | MODIFY | See 3.1 — becomes the department taxonomy source of truth once wired in. |

### 3.3 Templates

| File | Class | Why |
|---|---|---|
| `signup.html`, `onboarding_business.html`, `onboarding_receptionist.html` | REPLACE with a request/contact form, or DELETE if `/request-access` fully subsumes them | These *are* the self-serve config UI the new vision removes. |
| `activation_live.html` | MODIFY | "Your employee is live" reveal reframes to "your department is staffed" — copy/IA change, not logic. |
| `dashboard.html` | REPLACE (structure) | Needs to render departments as the primary card, employees as (at most) secondary detail. This is the biggest single UI rewrite implied by the new vision. Per the PRD's own §11a trigger clause: *"The day a second employee reaches `live` status, the dashboard must stop being a fixed template... and become a view generated from that business's real Employee rows"* — that trigger has effectively been pulled by this vision change, not by a second `live` employee, but the target state described is the same one needed here. |
| `roster_hire_retention_manager.html` | DELETE (or fold into a founder-only deploy form) | A dedicated single-employee hire page has no place if customers don't hire individual employees. |
| `login.html` | KEEP | Already had its self-serve CTA removed 2026-07-21. |
| `clients.html`, `client_detail.html`, `new_client.html` | MODIFY | Founder-admin surface — becomes where departments/employees actually get deployed; needs a department-grouped view of what's live per business, not new plumbing. |
| `recovery_new.html`, `recovery_detail.html` | KEEP | Founder-facing campaign tools; department framing doesn't change how a campaign is built. |
| `base.html`, `request_thanks.html` | KEEP | Layout/shell, unaffected. |

### 3.4 Landing / marketing (`agent/landing/`, `concepts/`)

| File | Class | Why |
|---|---|---|
| `roster.html` (`/roster` page) | MODIFY | Already the closest thing in the whole codebase to the new vision's department model — reconcile its department list/names with whatever becomes the code registry's canonical taxonomy (see §5's Leadership/Intelligence mismatch), then it's largely correct as-is. |
| `index-v2.html`, `styles-v2.css` (`/preview`, unlaunched) | KEEP | Already leans department-forward ("Fourteen more roles, seven departments," "You don't buy Roster. You staff it."). No conflict — if anything, this file is ahead of the live site. |
| `index.html`, `styles.css` (live `/`) | MODIFY | Older narrative arc (5 pain-named employee cards); update once the product-layer decision is final, not before — per the SPRINT feature freeze, landing changes should wait for outreach priorities regardless of this migration. |
| `concepts/workforce/index.html` | KEEP (or DELETE as unused) | Explicitly labeled "Design concept — an alternate direction, not the live site," not routed anywhere. Interesting prior art (already uses "workforce"/department-ish framing) but has zero technical debt either way — a no-op either to keep or delete. |

### 3.5 Governing docs (not code, but load-bearing — a fresh session reads these as instructions)

| File | Class | Why |
|---|---|---|
| `ROADMAP.md` | MODIFY | Its M2/M3/M4 build-freeze gates and "Phase B — Trust" plan are written entirely in employee-singular terms ("hire the next employee... one at a time"). Needs a new entry acknowledging the department pivot and *why* the freeze is being lifted for this specific work (mirroring how the 2026-07-21 spec justified its own exception to the sprint freeze). |
| `docs/superpowers/specs/2026-07-13-roster-platform-architecture-prd.md` | MODIFY | Its "frozen architecture" clause needs a dated exception entry (same pattern as its own §11a addendum) rather than being silently ignored. The domain model (§6) needs a `Department` concept added formally, per whatever this review's §6 below recommends. |
| `docs/superpowers/specs/2026-07-21-ai-staffing-repositioning-design.md` | KEEP (superseded, not deleted) | Historical record of the prior decision this migration reverses — valuable precisely because it documents the reasoning being overturned. Add a header note ("Superseded by 2026-07-28 departments migration") rather than editing its body. |
| `SALES.md` | MODIFY | Its "Customer Onboarding Principle" already anticipates discovery-led, non-Frontdesk-first hiring — extend it to speak in department terms explicitly. |
| `CUSTOMER.md` | KEEP | Living log — add the department pivot as a dated entry, don't restructure. |
| `DESIGN.md` | MODIFY | Add a decision-log entry once dashboard IA actually changes (per `CLAUDE.md`'s standing rule: read DESIGN.md before any visual/UI decision, log every change there). No visual work should start without this. |
| `agent/README.md` (if present) / other specs (`2026-07-02`, `2026-07-03` × 3, `2026-07-05`, `2026-07-10`, `2026-07-27`) | KEEP | Historical design records — accurate for their moment, no action needed; they're the audit trail this very document relies on. |

### 3.6 Out of scope for this technical review

`outreach/*`, `marketing/acquisition-system.md`, `pitch-deck/*`,
`SPRINT-10-CUSTOMERS.md` — these are go-to-market assets (CRM, cold-outreach
scripts, investor decks), not architecture. They reference "AI employees"
loosely and don't encode a structural employee-vs-department assumption
either way. No classification needed; touch them only if messaging needs to
change after the product decision is final.

---

## 4. Migration plan, in dependency order

Each phase should leave the test suite green and the app deployable — same
discipline the existing PRD already commits to (§20, §22 of the 2026-07-13
PRD). No phase should be started before the prior one is merged.

**Phase 0 — Documentation alignment (no code)**
1. Add dated entries to `ROADMAP.md` and the platform PRD acknowledging this
   pivot and why the architecture freeze doesn't block it (founder directive,
   this document as the record).
2. Resolve the `employees.py` ("Intelligence") vs. `roster.html`
   ("Leadership") department-name mismatch — pick one canonical department
   list of 7 (the user's message names: Customer Service, Sales, Operations,
   Finance, Customer Success, Marketing, Leadership).
3. Decide the one open UX question this review surfaces (§6.1): how much do
   employee names surface inside a department view? Recommend before coding.

**Phase 1 — Department as a code registry (additive, zero behavior change)**
4. Promote `employees.py`'s `department` field from a comment-only "tag" to
   an actual `Department` registry (code, not a table — mirrors the existing
   `RoleDefinition`/`EmployeeDefinition` pattern rather than introducing a new
   kind of model): department key, display name, tagline, ordered employee
   list. Pure addition; nothing imports it yet.
5. Add a derived helper — "which departments have at least one active
   `Employee` for this business" — computed from existing `Employee.role_key`
   → `employees.py` lookup. No schema change.

**Phase 2 — Founder-admin surface becomes department-native**
6. `/clients/{id}` (`client_detail.html`) groups existing employees by
   department and exposes deploy/pause/fire at the department level where it
   makes sense, employee level where it doesn't (a department can have
   partial staffing).
7. This is where `runner.py` grows from its current single empty hook into
   the real per-department capability dispatcher — still founder-triggered,
   not event-driven yet, so no risk to the live SMS/voice path.

**Phase 3 — Customer-facing onboarding rewrite**
8. Replace `/signup` + `/onboarding/*` self-serve forms with a
   request/contact flow that matches the new steps 1–3 (this is a smaller
   lift than it sounds — `/request-access` → `/thanks` already exists and is
   already the advertised default; this phase mostly means *retiring* the
   parallel self-serve path rather than building a new one).
9. Decide `/signup`'s fate: keep as a founder tool (used during a discovery
   call to spin up the account, per current practice) or delete once
   `/clients/new` fully covers that need.

**Phase 4 — Dashboard rebuild (the biggest visual change; gate on DESIGN.md)**
10. Read/update `DESIGN.md` first (`CLAUDE.md` hard requirement).
11. Rebuild `dashboard.html` around department cards (status, activity,
    outcome metrics per department) with employee detail as secondary
    disclosure, not the primary unit.
12. Retire `roster_hire_retention_manager.html` and the `/roster/hire*`
    self-serve routes, or convert them into founder-only deploy actions
    consistent with `/clients/{id}/employees/deploy`.

**Phase 5 — Reconcile marketing**
13. Align live `/` (or ship `/preview`'s `index-v2.html`, which is already
    closer to this vision) and `/roster` with whatever canonical department
    list Phase 0 settled on.

**Explicitly not touched by this migration:** `engine.py`'s Think→Act→Observe
loop, the Twilio/xAI channel integrations, trial-cap logic, the recovery/
referral engines' internal logic, and the `BusinessMemory` port. All of these
are correct under either vision.

---

## 5. Naming mismatch to resolve (small but concrete)

`agent/employees.py`'s registry uses department key `"intelligence"`
("Business Analyst," "Operations Manager"). `agent/landing/roster.html`'s
live `/roster` page uses a department called **"Leadership"** for a
near-identical set ("Business Analyst," "AI Office Manager," "CEO
Assistant"). The user's new-vision message also says **"Leadership."** Pick
"Leadership" as canonical (matches both the live marketing page and the new
instructions) and update `employees.py`'s one occurrence of `"intelligence"`
accordingly — trivial, but worth doing in Phase 0 rather than shipping a
second inconsistency on top of the first.

---

## 6. Backend vs. product layer — what stays, what's redesigned

**Stays unchanged (the "business-operations platform" core, per PRD
principle 6):** aggregate root (`Business`), first-class `Customer`, the
`AgentEngine` reasoning loop, channel adapters (Twilio/xAI), the
`BusinessMemory` port, trial-cap/booking/notification plumbing, the
EventBus/RoleDefinition seam shape (`eventbus.py`, `runner.py`'s pattern).
This is precisely because the new vision's "departments share company memory
and work as one organisation" requirement is a description of what this
layer already does — it was designed business-scoped, not employee-scoped,
from the 2026-07-13 PRD onward.

**Needs redesign (the "AI staffing company" external product layer):**
everything that currently treats an individual named employee as the unit a
customer sees, hires, or tracks — `roles.py`'s hire sequencing, `portal.py`'s
self-serve onboarding/hire routes, and `dashboard.html`'s fixed three-slot
card. None of this redesign requires touching the data layer beyond adding a
code-level `Department` registry (Phase 1) — it's a routing and template
problem, not a database problem, which is the main reason this migration is
tractable without a schema migration.

---

## 7. Open question before coding (recommend deciding in Phase 0)

**How hidden are employees, really?** The new vision states "employees are
an implementation detail." Taken literally, this conflicts with a genuinely
validated piece of the existing product psychology
(`DESIGN.md` 2026-07-11 entry): the honest status ladder ("Ready — try it
before you trust it" → "Working — you've seen it answer") and the
in-dashboard test chat are built around the owner watching **one specific
named hire** work and earning trust in *it*. A fully department-opaque
dashboard ("Sales department: working") would remove the single mechanic
that's already proven to build trust with zero customers today.

**Recommendation:** treat "employees are an implementation detail" as *"the
customer's primary mental model and hiring decision is the department"* —
not as *"employee identity is hidden."* Keep employee names visible as
progressive-disclosure detail inside a department card (so "hire the Sales
department" is the action, and "meet Quote Chaser, your Sales hire" is the
proof), rather than erasing named employees from the UI entirely. This
preserves the trust mechanic while satisfying the department-first
structure. Flagging this explicitly rather than deciding it silently, since
it's a real product-psychology tradeoff, not a pure architecture question.

---

## 8. Proposed new high-level architecture (pre-code, for discussion)

```
Business (unchanged aggregate root)
├── Customer[] (unchanged — first-class, shared across everything below)
├── Employee[] (unchanged table — business_id, role_key, status, policy_json)
├── Job[] / Message[] / Event[] (unchanged)
│
├── [NEW, code registry — not a table] Department
│     { key, display_name, tagline, member_role_keys: [...] }
│     Source of truth: agent/employees.py, promoted from tag to structure.
│     A business's "departments" are DERIVED: group its Employee rows by
│     each role_key's Department, at query time — no new join table, no
│     migration, consistent with how RoleDefinition already works.
│
├── Runner (existing seam, grown from one hook to the real dispatcher)
│     Departments don't get new dispatch logic — they're a VIEW over the
│     same Employee/RoleDefinition/EventBus substrate that already exists.
│     A department "does work" exactly when one of its member employees'
│     capability fires via the Runner.
│
└── BusinessMemory (unchanged port)
      Already the "shared company memory" — no change needed; it was never
      employee-scoped to begin with.
```

**Product-layer flow (new):**
```
Business contacts Roster (request-access form — already exists, already the
  advertised default)
        │
Founder discovery call → founder deploys the department(s)/employee(s)
  that fit (via /clients/{id}/employees/deploy — already exists, already
  founder-only, per PRD §11a)
        │
Customer gets dashboard access → dashboard renders DEPARTMENTS the business
  actually has staffed (derived grouping above), each showing outcomes +
  (secondary) which named employee is doing the work
        │
Customer can request the next department (mirrors today's "Discuss Your
  Next AI Hire," reframed as "Discuss Your Next Department") → founder
  reviews and deploys, same as today
```

The headline result: **the department layer is almost entirely a
presentation/grouping concept over infrastructure that already exists** —
`Business`, `Customer`, `Employee`, `BusinessMemory`, `Runner`, `EventBus`
don't need to change shape. What changes is (a) a new code registry grouping
existing roles into departments, (b) the founder-admin view grouping by that
registry, (c) the customer dashboard rebuilt around that same grouping
instead of a fixed three-slot employee list, and (d) retiring the two
self-serve onboarding/hire code paths that let a customer configure or hire
without the founder in the loop. No schema migration is required unless the
founder wants department stored per-hire for reporting rather than derived
at query time.

---

## 9. Summary for a fast read

- The backend (`Business`/`Customer`/`Employee`/`Event`, the engine, channels,
  memory) was already built business-scoped and department-agnostic — it
  needs almost no change.
- The product layer (`portal.py`'s self-serve onboarding/hire routes,
  `roles.py`'s hire sequencing, `dashboard.html`'s fixed employee slots) is
  where every old-vision assumption actually lives, and where the real work
  is.
- A full department *taxonomy* already exists today, live, in marketing copy
  (`/roster`) — just disconnected from the product. This migration is as
  much about connecting existing pieces as building new ones.
- This reverses a founder decision from exactly one week ago and requires
  updating `ROADMAP.md`'s and the PRD's freeze language so the repo stays
  internally consistent — recommended as Phase 0, before any code.
- One real product-psychology tradeoff needs a conscious decision, not a
  silent default: how much individual employee identity survives inside a
  department-first dashboard (§7).
