# Roster — Platform Architecture PRD

**Status:** Locked architecture, pre-implementation
**Date:** 2026-07-13
**Owner:** Founder (CPO / Chief Architect / Founding Engineer)
**Supersedes for architecture:** the ad-hoc structure described across the 2026-07 specs; this is the canonical architecture of record.

> **Constitutional principles (locked — do not revisit without a real customer forcing a change):**
> 1. **Business** is the aggregate root.
> 2. **Customer** (the homeowner) is a first-class entity in v1.
> 3. **Employees** are declarative `RoleDefinition`s executed by a generic **Runner**.
> 4. A lightweight **synchronous EventBus** is a v1 architectural seam.
> 5. All external systems are accessed through **ports** (Channels, Integrations, Memory, Intelligence).
> 6. **"AI Staffing Company" positioning is external only.** Internally this is a **business-operations platform**.
> 7. **Property** and **Equipment** have a reserved place in the domain model; implementation is **deferred** until demand justifies it.
>
> **The define/implement rule that governs this whole document:** for every subsystem we *define a stable interface (port)* in v1 and *implement the thinnest possible body behind it*. "Define the abstraction" ≠ "build the subsystem."
>
> **ARCHITECTURE FROZEN (2026-07-13).** From this point onward, every architectural change must be justified by a **real pilot or paying customer**. Future discussions focus on customer outcomes, onboarding, sales, reliability, and shipping — **not** inventing additional platform layers. If a change is proposed with no customer behind it, the default answer is no.

---

## 1. Vision

By ~2030 every business runs on AI agents, and the 1–5-truck home-service business will never build or run that in-house. Roster is the managed middle: we don't sell a tool the owner has to operate — we do the customer-operations labor for them and they see "it works."

**Externally** this is an *AI staffing company*: the owner hires named employees (Receptionist, Quote Chaser, Retention Manager) one at a time. **Internally** it is a *business-operations platform*: a per-business shared substrate of memory + events, over which declarative employees run.

The 10-year destination is not "a staffing agency." It is to **become the system of record for the home-service business by accident of having done the labor** — the same switching-cost position that makes ServiceTitan (~$9–12B) impossible to rip out, reached from underneath instead of from a tool the owner must learn.

**The one architectural truth this vision demands:** the value compounds because every employee reads and writes the *same* business substrate. The second hire is worth more than the first because it inherits everything the first learned. That is not a feature to build later — it is the shape of the data model from day one.

---

## 2. Product Strategy

**Wedge → value sequence.** Frontdesk (missed-call → text-back → booked job) is the *wedge*: it is the front door and it harvests the data. **Recovery** (Quote Chaser, Retention Manager) is the *value*: it monetizes data Frontdesk already captured, in a part of the trade stack nobody owns. Architecture must make Recovery a first-class consumer of Frontdesk's data — not a bolted-on second product.

**Pricing (external):** 7-day free trial → flat monthly. Frontdesk is the entry hire (~$149–199/mo, anchored against a $400–1,000/mo human answering service). Additional employees are incremental hires (~$49–99/mo each), surfaced only when a specific business's need is known.

**Moat:** not one feature — the full roster, added one role at a time, each riding the *same shared substrate*, each raising switching cost. "Why not buy Receptionist from Company A and Dispatcher from Company B?" → because Roster's second employee inherits the first's memory of your customers; two vendors never will.

**Strategic guardrail (this is a review-hardened, load-bearing constraint):** this platform-architecture work produces **almost no new customer-visible feature.** It is justified *only* because the Customer entity and the event seam are cheap to build now (near-zero data to migrate, pre-pilot) and brutally expensive to retrofit later. Therefore:
- The v1 architecture slice (Phases 1–2) is **time-boxed and must not delay pilot outreach.** If forced to choose between finishing Phase 4+ polish and getting a real business to say yes, the business wins.
- Success is measured in **jobs booked and Recovery revenue recovered for real businesses**, never in lines of platform code. See §19.
- Do not build any subsystem body ahead of demand. Ports are free; subsystems are not.

---

## Non-Goals

This section exists to **protect the roadmap.** Architecture documents expand on their own; naming what v1 is *not* is the guardrail. (Distinct from §23 Out-of-Scope, which lists capabilities deferred *behind a defined port and built later*. The items here are things v1 will not include at all — some are later-phase, several are things Roster is deliberately *not*.)

**v1 will NOT include:**
- Vector database
- Knowledge graph
- Multi-agent planning
- Durable workflows
- Human task assignment
- CRM replacement
- ServiceTitan replacement
- Multi-property support
- Equipment tracking
- Analytics dashboard (as a product surface)
- Email (as a channel)
- WhatsApp
- Mobile app
- Marketplace
- Plugin system

Anything on this list that later becomes real is admitted **only** through the frozen-architecture rule: a pilot or paying customer forces it. Until then, proposing any of these is out of bounds.

---

## 3. User Personas

**P1 — The owner (primary buyer & user).** 1–5-truck HVAC/plumbing/electrical owner, 35–60, on a phone, skeptical, no office staff, has been burned by software that made *him* the operator. Wants: not to miss money, not to be embarrassed by a robot, to turn it off if it misbehaves. Does not care about architecture, "AI," or dashboards for their own sake. Trusts what he can *watch work*.

**P2 — The homeowner (the business's customer; the end recipient).** Called the business with a problem (no AC, burst pipe). Interacts with Roster over SMS/voice without necessarily knowing it's AI. Wants a fast, competent, human-feeling answer and to get on the schedule. **First-class in the domain model** — every employee shares one view of this person.

**P3 — The founder / ops operator (internal).** Runs the concierge operation today; the human-in-the-loop that is literally the product. Uses the admin surface to see every business, provision numbers, watch conversations, intervene. At pilot scale this is the founder personally.

**P4 — Future: the second-seat owner-operator / office manager.** Larger shop (5–15 trucks) with one office person. Not a v1 target; the permission model must not make them impossible later (§17).

---

## 4. User Journey

**Owner (P1) — hire → trust → expand:**
1. Lands from a cold call/text on the "Employment Offer" landing page (loss-aversion first).
2. Signs up (email+password or Google) → dropped straight into onboarding (no empty state).
3. **Briefs the hire** (never "configures software"): business facts (name, trade, services, hours, pricing/FAQ) → receptionist judgment calls (escalation number, primary vs backup answer mode, business phone).
4. Roster provisions a real number in the background and the employee goes live — degrading to SMS-only if voice provisioning fails, never blocking.
5. Lands on the dashboard in **"Ready — try it before you trust it"** state. Sends a test message, watches a real reply come back → status earns **"Working — you've seen it answer."**
6. Real calls/texts start booking jobs. **Every booked job is delivered to the owner immediately** (text/email to the escalation number) — the job lands somewhere he already looks, not only in a dashboard.
7. Days later: a nudge to say where he heard about Roster; later still, the option to **hire the next employee** (Quote Chaser, then Retention Manager) — one at a time, next role unlocking only after the prior is in place.
8. At any point he can **pause or fire** an employee (real kill switch, §16) without re-explaining his business.

**Homeowner (P2):** calls → unanswered (or answered live) → receives a competent SMS/voice conversation → gets booked → later may receive a review request or a Recovery follow-up — all from employees sharing one memory of them.

**Founder (P3):** monitors businesses, provisions/repairs numbers, reads conversations, intervenes on edge cases.

---

## 5. Information Architecture

Two authenticated surfaces + public + webhooks, unchanged in shape from today but re-homed on the new spine.

- **Public:** landing page(s), `/signup`, `/login`, Google OAuth routes.
- **Owner portal (session-cookie auth, one business per session):** onboarding (business → receptionist), activation, dashboard (status ladder, test chat, activity log, employee roster, reviews/referrals), hire flow.
- **Founder admin (HTTP-Basic, all businesses):** business list, per-business detail, conversations, number provisioning/repair.
- **Webhooks (signature-authenticated):** Twilio inbound SMS, Twilio missed-call, xAI voice events.

IA principle: the owner portal is scoped to exactly one `Business`; the admin surface is the only cross-business surface and must never be public (fail-closed).

---

## 6. Domain Model

The aggregate root is **Business**. Everything else hangs off it and is business-scoped. (Physical note: today's `Client` table *is* the Business aggregate root; it is renamed `Business` in Phase 1 — see §7 and §24 — specifically because "Client" collides with "Customer" now that the homeowner is first-class.)

```
Business (aggregate root; "Office" is an external/UX word for this)
├── profile (business facts: trade, services, hours, pricing/FAQ, tone, answer_mode, phones)
├── Employee[]        (declarative bindings: role + status + policy)
├── Customer[]        (the homeowner — FIRST-CLASS in v1)
│   ├── Interaction[] (messages/turns across channels; was "Message")
│   └── Job[]         (captured/booked work; FK to Customer, not a phone string)
├── Event[]           (append-only domain event log — the spine's memory + audit)
├── RecoveryCampaign[] / RecoveryJob[] (Recovery employee's working set)
├── [reserved] Property[]    (defined, NOT implemented in v1)
└── [reserved] Equipment[]   (defined, NOT implemented in v1)

RoleDefinition (code registry, NOT a table): the template an Employee instantiates.
  { role_key, display_name(trade), trigger_events[], capability, default_policy, persona }
```

**Entity definitions:**

- **Business** — the tenant/aggregate root. Owns the business profile fields, provisioning fields (inbound/xAI/Twilio numbers), trial-billing fields, auth fields (email, password_hash), and lifecycle flags (activated_at, tested_at, frontdesk_live).
- **Customer** *(new, first-class)* — the homeowner. `business_id`, `phone` (unique within a business), `name`, `source`, `tags`, `first_seen_at`, timestamps. The shared identity every employee reads. Solves: today the homeowner is a `customer_phone` string duplicated across Job/Message/Recovery with no shared record.
- **Employee** *(new)* — a live binding of a `RoleDefinition` to a Business. `business_id`, `role_key`, `display_name`, `status` (`active | paused | fired`), `policy_json` (per-instance overrides), `hired_at`, `fired_at`. Replaces the `requested_roster` JSON blob and the implicit "which agents are on."
- **RoleDefinition** *(code, not data in v1)* — declarative template: which events it subscribes to, which capability it runs, its default policy, its persona/prompt builder, its trade-aware display name. Frontdesk, QuoteChaser, Retention, Reviews.
- **Interaction** *(renamed from Message)* — one turn in a conversation. `business_id`, `customer_id`, `channel` (sms/voice/dashboard-test), `direction` (inbound/outbound), `role` (user/assistant), `content_json`, `handled_by_employee_id?`, `created_at`.
- **Job** — captured/booked work. Now `customer_id` FK (not `customer_phone`). Keeps service_type, urgency, address, callback_number, notes, completed_at, referral_sent_at.
- **Event** *(new, append-only)* — the domain event log. `business_id`, `type`, `payload_json`, `customer_id?`, `employee_id?`, `dedup_key?` (provider message SID for idempotency), `occurred_at`. This is both the EventBus's persistence and the analytics/audit/eval substrate.
- **Property / Equipment** *(reserved, unimplemented)* — the home and the units in it (furnace, AC, water heater). Reserved because trade work is ultimately *about a physical asset at an address*, and modeling it later is a natural evolution (service history per unit). **Not built in v1**; noted here so no future decision accidentally forecloses it. No table, no fields — only this reservation.

**Long-term roster:** the full employee-by-employee roadmap (beyond the `RoleDefinition`s above) is enumerated as a code registry in `agent/employees.py`, not here — see `docs/superpowers/specs/2026-07-21-ai-staffing-repositioning-design.md` §4. Inclusion there does not imply implementation.

---

## 7. Database Schema

SQLite (Railway volume, WAL) in v1, behind a repository layer so the Postgres swap is mechanical (§21, §23). Migrations continue via the existing additive `init_db` mechanism *plus* three structural migrations new to this PRD.

**Tables (v1):**

`business` *(renamed from `client`)* — all existing `Client` columns retained: `id`, `business_name`, `trade`, `services_json`, `hours`, `pricing_faq`, `escalation_phone`, `answer_mode`, `business_phone`, `inbound_number`, `xai_phone_number`, `xai_signing_secret`, `twilio_number_sid`, `review_link`, `referral_incentive`, `email` (unique), `password_hash`, `tone`, `source`, `source_prompt_dismissed`, `frontdesk_live`, `activated_at`, `tested_at`, `trial_spend_cents`, `trial_cap_cents`, `trial_soft_buffer_cents`, `trial_cap_notified`, `created_at`. **Removed:** `requested_roster` (migrated into `employee`).

`customer` *(new)* — `id`, `business_id` (FK), `phone`, `name?`, `source?`, `tags_json` (default `[]`), `first_seen_at`, `created_at`, `updated_at`. Unique index on `(business_id, phone)`.

`employee` *(new)* — `id`, `business_id` (FK), `role_key`, `display_name`, `status` (`active|paused|fired`, default `active`), `policy_json` (default `{}`), `hired_at`, `fired_at?`.

`interaction` *(renamed from `message`)* — `id`, `business_id` (FK), `customer_id` (FK), `channel` (default `sms`), `direction`, `role`, `content_json`, `handled_by_employee_id?`, `created_at`. Index on `(business_id, customer_id, id)`.

`job` — as today plus `customer_id` (FK); `customer_phone` retained transitionally then dropped after backfill. Index on `(business_id, customer_id)`.

`event` *(new)* — `id`, `business_id` (FK), `type`, `payload_json`, `customer_id?`, `employee_id?`, `dedup_key?` (unique when present), `occurred_at`. Index on `(business_id, type, occurred_at)`.

`recoverycampaign`, `recoveryjob`, `recoverymessagelog`, `referrallead` — retained; `recoveryjob`/`referrallead` gain `customer_id` FK, phone columns retained transitionally.

**Structural migrations — split across two independently deployable checkpoints (see the plan doc):**

*Foundation A (app fully working after, no new subsystem):*
1. **Rename** `client` → `business` — class, table, FK targets, and FK attribute names (`client_id` → `business_id`) across `job`/`message`/recovery/referral, plus all code references. Ends the Client/Customer ambiguity.
2. **Add `customer_id`** to `job` and `message` (columns only).
3. **Backfill `customer`**: for each distinct `(business_id, customer_phone)` create a `Customer`; populate `job.customer_id` and `message.customer_id`.

*Foundation B (event seam live, app still fully working):*
4. **Backfill `employee`**: every live business gets an `active` Frontdesk employee; each old `requested_roster` entry becomes an `Employee` row.
5. Add the `event` table (append-only).

**Deferred to Phase 2 (Communication):** the physical `message` → `interaction` rename plus `channel`/`direction`/`handled_by_employee_id` — done when those fields are first *used*, so the foundation carries exactly one rename, not two. Until then the code keeps the `Message` model name (with `customer_id` added in Foundation A); "Interaction" is the domain term this table takes on in Phase 2.

Given near-zero production data pre-pilot, these are low-risk now — which is the entire reason they are in v1 and not deferred. Each Foundation leaves the full test suite green and is independently deployable.

---

## 8. Backend Architecture

FastAPI monolith (single process) — correct for this stage; the module seams below are where it would later split into services without a rewrite.

```
                 ┌───────────────────────── HTTP / Webhooks ─────────────────────────┐
   Landing/Portal routes        Admin routes           Twilio SMS/voice, xAI voice webhooks
        (portal.py)             (app.py admin)                   (app.py webhooks)
                 └───────────────────────────────┬───────────────────────────────────┘
                                                 │ translate to domain events
                                        ┌────────▼─────────┐
                                        │    EventBus       │  publish(event) →
                                        │  (sync, in-proc)  │   persist to `event`,
                                        └────────┬─────────┘    dispatch to subscribers
                                                 │
                                        ┌────────▼─────────┐
                                        │  Employee Runner  │  for each active Employee whose
                                        │  (generic)        │  RoleDefinition subscribes to type:
                                        └────────┬─────────┘   load memory → run capability → emit events
                                                 │
                 ┌───────────────────────────────┼───────────────────────────────┐
        ┌────────▼────────┐  ┌────────▼────────┐  ┌────────▼────────┐  ┌──────────▼─────────┐
        │  Memory (port)   │  │ Intelligence     │  │ Communication    │  │ Integration (port) │
        │ BusinessMemory   │  │ (LLM port)       │  │ (Channel port)   │  │  (SoR adapters)    │
        │ → relational     │  │ → Anthropic      │  │ → Twilio/xAI     │  │  → ZERO impls v1   │
        └──────────────────┘  └──────────────────┘  └──────────────────┘  └────────────────────┘
```

**Module map (target; several already exist):**
- `eventbus.py` *(new)* — `publish(event)`, `subscribe(type, handler)`; persists then dispatches synchronously.
- `events.py` *(new)* — the domain event type constants + payload shapes (§10).
- `runner.py` *(new)* — the generic Employee Runner; replaces bespoke wiring in `app.py`.
- `roles/` *(new)* — `RoleDefinition` registry (`frontdesk.py`, `quote_chaser.py`, `retention.py`, `reviews.py`).
- `memory.py` *(new)* — `BusinessMemory` port + relational implementation (wraps existing repositories).
- `engine.py` *(exists)* — the Intelligence body (Think→Act→Observe loop); becomes the `LLM`-port-backed reasoner.
- `channels.py` *(exists)* — the Channel port (already a `Protocol`); extended to cover inbound parse + voice.
- `integrations.py` *(new)* — `Integration` port + empty registry.
- `db.py`, `db_models.py`, `models.py` *(exist)* — schema + config DTO.
- `portal.py`, `app.py` *(exist)* — HTTP surfaces; slimmed to "translate request → publish event / call runner," not business logic.

**Dependency rule:** HTTP layer → EventBus/Runner → ports. Employees never import Twilio, Anthropic, or another employee. Cross-employee effects happen *only* via events.

---

## 9. API Design

**Owner portal (session cookie; all scoped to the session's one business):**
- `GET/POST /signup`, `GET/POST /login`, `POST /logout`
- `GET /auth/google/login`, `GET /auth/google/callback`
- `GET/POST /onboarding/business`, `GET/POST /onboarding/receptionist`
- `GET /activation/live`
- `GET /dashboard`
- `POST /dashboard/test` (test-chat message)
- `POST /dashboard/source`, `POST /dashboard/review-link`, `POST /dashboard/referral-incentive`
- `POST /roster/hire` (hire next employee), `GET/POST /roster/hire/retention-manager`
- **`POST /employees/{id}/pause`, `POST /employees/{id}/resume`, `POST /employees/{id}/fire`** *(new — the real kill switch, §16/§17)*

**Founder admin (HTTP-Basic; cross-business):**
- `GET /clients` (business list), `GET /clients/{id}` (detail + conversations)
- `POST /clients/{id}/provision-number` (and voice-attach repair)
- (existing admin actions retained)

**Webhooks (signature-verified; translate to events, never contain employee logic):**
- `POST /webhooks/twilio/sms` → publishes `message.received`
- `POST /webhooks/twilio/missed-call` → publishes `call.missed`
- `POST /webhooks/xai/voice` → publishes `call.received` / `call.completed`

**Contract conventions:** portal routes are form-POST + 303 redirect (server-rendered, matches today). Webhooks are idempotent on the provider's message/call SID via `event.dedup_key` (§10, §18). No public JSON API in v1 (deferred, §23).

---

## 10. Event Model

The EventBus is **synchronous, in-process** in v1. `publish(event)`:
1. Assigns/validates a `dedup_key` when the source provides one (Twilio MessageSid, xAI call id). If a persisted event with that key exists, **drop** (idempotency — webhooks retry).
2. Persists to the `event` table (append-only).
3. Dispatches synchronously to every subscribed active Employee handler for that business.
4. Handler side effects (new interactions, jobs, outbound sends) may `publish` further events (bounded; no re-entrant loops — handlers must not re-emit their own trigger type).

**Domain event catalog (v1):**

| Event | Emitted by | Consumed by |
|---|---|---|
| `message.received` | Twilio SMS / voice transcript / dashboard test | Frontdesk |
| `call.missed` | Twilio missed-call webhook | Frontdesk (text-back) |
| `call.received` / `call.completed` | xAI voice | Frontdesk, (job capture) |
| `job.booked` | Frontdesk capability | Reviews (arm), Retention (record), owner-delivery |
| `job.completed` | Owner action ("mark done") | Reviews (send request), Retention |
| `quote.sent` | Owner action / future integration | Quote Chaser |
| `quote.followup_due` | Scheduler | Quote Chaser |
| `customer.dormant` | Scheduler | Retention (reactivation) |
| `membership.renewal_due` | Scheduler | Retention (renewals) |
| `review.requested` | Reviews capability | (analytics) |
| `referral.received` | Inbound reply classifier | Referral handling |
| `llm.completed` *(new)* | Intelligence port | eval/analytics substrate (§12, review-hardened) |

**Why persist events:** the `event` table is simultaneously (a) the EventBus's durability-ready seam, (b) the analytics source of truth (§19), (c) the audit trail ("what did the employee do and why"), and (d) the future eval corpus (logged `llm.completed` events are gradeable later). One append-only log serves four future subsystems; none of them is built now.

**Explicitly deferred behind this seam:** async dispatch, retries, dead-letter queues, ordering guarantees, Temporal/durable workflows. The synchronous bus + persisted log means adding these later does not touch any employee.

---

## 11. Employee System

**An employee is a declarative binding, not a module.** A `RoleDefinition` is a code-level template:

```
RoleDefinition:
  role_key:        "frontdesk" | "quote_chaser" | "retention" | "reviews"
  display_name(trade) -> str        # trade-aware ("CSR" for HVAC, etc.)
  trigger_events:  [event types this role subscribes to]
  capability:      callable(event, memory, ports) -> [emitted events]
  default_policy:  { model_tier, escalation, cadence, ... }
  persona:         builds the system prompt from business profile + policy
```

An **Employee** is an instance of a RoleDefinition bound to a Business, carrying `status` and `policy_json` overrides.

The **Runner** is the only executor: on an event, it finds active Employees whose RoleDefinition subscribes to that type, loads the business's `BusinessMemory`, invokes the capability with the ports, and publishes whatever the capability emits. Adding a new employee = adding a `RoleDefinition` to the registry + an `Employee` row. **No new wiring, no changes to existing employees.**

**v1 roster (RoleDefinitions implemented):**
- **Frontdesk** — subscribes `message.received`, `call.missed`, `call.received`. Capability = the existing intake/booking loop (`engine.py`), emits `job.booked`.
- **Quote Chaser** — subscribes `quote.sent`, `quote.followup_due`. Capability = existing Recovery "quote" face.
- **Retention Manager** — subscribes `job.completed`, `customer.dormant`, `membership.renewal_due`. Capability = existing Recovery "reactivation/renewal" faces (one engine, three faces).
- **Reviews** — subscribes `job.booked`/`job.completed`. Capability = single review-request send. (A capability every roster gets; unnamed as a seat, consistent with positioning.)

**Model routing is policy, not code:** each capability declares a `model_tier` in `default_policy` (light for intake/classification, strong for judgment). The `LLM` port resolves tier → concrete model at call time, so the model can change without touching capabilities (§12, §24).

**Kill switch (review-hardened):** `Employee.status` is authoritative in the Runner. `paused`/`fired` employees receive no dispatch. Firing does **not** delete the business's memory — a re-hire inherits the full history. This is the "fire and replace without re-explaining your business" mechanic, and it falls out of the model for free.

### 11a. Deployment model: Founder Admin configures, Customer Portal reflects (founder, 2026-07-21)

Forward-looking constraint on top of the model above — **not implemented, no
code/schema/dashboard change accompanies this entry.** See
`docs/superpowers/specs/2026-07-21-ai-staffing-repositioning-design.md` for
the conversation that produced it.

- **The Founder Admin is the only place an Employee gets selected,
  configured, deployed, or managed.** Every `Employee` creation and
  `status` change is a founder-initiated action on the admin surface (§5),
  driven by what a discovery call and real business data justify — never a
  customer choosing from a menu.
- **The Customer Portal is a read-only reflection of what's already
  deployed, not a configuration interface.** It shows the business's actual
  roster and each employee's state — it never presents a catalog of
  undeployed employees for the customer to browse or activate. Roster
  recommends and deploys; the customer doesn't self-serve a hire.
- **Today this is a distinction without a difference.** Frontdesk is the
  only `live` employee (`agent/employees.py`), so the current generic
  onboarding wizard and the fixed three-slot "Your office" dashboard card
  are acceptable as-is. Do not refactor them now.
- **The trigger to act on this:** the day a second employee reaches `live`
  status, the dashboard must stop being a fixed template (Frontdesk +
  Quote Chaser prompt + Retention Manager prompt shown to every business
  regardless of what's actually deployed) and become a view generated from
  that business's real `Employee` rows, with no undeployed-employee catalog
  visible. Until that trigger, this section constrains future design — it
  is not a task.

---

## 12. Memory System

**Port (defined in v1):** `BusinessMemory`, scoped to one Business:
- `profile()` → the business facts (from `business`).
- `get_customer(phone)` / `upsert_customer(...)` → the first-class Customer.
- `record_interaction(...)` / `timeline(customer_id)` → conversation history.
- `recall(query)` → semantic retrieval **(stub in v1)**.

**Implemented body (v1):** relational — profile from `business`, customers from `customer`, timeline from `interaction`, jobs from `job`. `recall(query)` returns the obvious recent context (this customer's full thread + record). It is a *keyword/recency* stub, not semantic.

**Deferred behind the port:** embeddings, vector store, knowledge graph, cross-customer semantic search. When built, they replace `recall()`'s body; **no caller changes.** This is the canonical "define abstraction, minimal implementation" case.

**Review-hardened additions:**
- **Business-scoped isolation is the security boundary.** Every `BusinessMemory` is constructed for exactly one `business_id`; no query crosses businesses. This is also the defense against prompt-injection exfiltration (§18): a hostile homeowner message cannot reach another business's data because the memory handed to the capability physically cannot see it.
- **Eval substrate:** `llm.completed` events (prompt, tools, output, model, tokens) are logged to the `event` log. This is *not* an eval framework — it is the corpus a future one will grade. Free to capture now, expensive to reconstruct later.
- **PII/retention:** Customer records hold phone/name/address. v1 stores indefinitely; a retention/delete policy is defined as owed (§18, §24) but not implemented.

---

## 13. Communication System

**Port (mostly exists):** `Channel` — `send(from, to, body)`, `parse_inbound(request) -> InboundMessage`, `capabilities()`. Normalized `InboundMessage` / `OutboundMessage` so employees are channel-agnostic.

**Implemented bodies (v1):**
- **SMS (Twilio):** inbound replies via TwiML (no outbound creds needed); outbound-initiated (missed-call text-back) via `TwilioChannel`; `ConsoleChannel` fallback with a **loud warning** when creds are absent (already built — keep the loudness; a silent no-op would hide dead production SMS).
- **Voice (xAI):** live voice via the xAI Voice adapter; `transfer_call` remains a *passthrough* tool the provider executes, distinct from internally-resolved tools like `log_job` (already correctly separated in `engine.py`).

**Review-hardened addition — booked-job delivery is v1, not deferred with integrations.** A booked job must land somewhere the owner already looks. On `job.booked`, the owner receives the job as an SMS/email to their escalation number *immediately*. This is the minimum that prevents Roster from being a silo the owner copy-pastes out of (the ServiceTitan-PL objection, §Review Log). It uses the Channel port, not the Integration port — it is not a CRM sync, just delivery.

**Deferred behind the port:** email as a first-class inbound channel, web chat, WhatsApp, in-app messaging.

---

## 14. Frontend Pages

Server-rendered Jinja templates, styled per **DESIGN.md** (warm paper palette, Fraunces display, system-sans body, ledger/receipt motifs; **no dark mode** — audience doesn't expect it). No SPA. All pages already exist except the employee-management additions.

**Public:** `landing` (Employment Offer), `signup`, `login`.
**Onboarding:** `onboarding_business`, `onboarding_receptionist`, `activation_live`.
**Dashboard (`dashboard.html`):** the honest status ladder, in-dashboard test chat, activity log (real vs test-tagged jobs), the employee roster (your hires + hire-next), Reviews & Referrals card. **New:** per-employee row shows `status` with pause/resume/fire controls (§16).
**Hire flow:** roster picker + `roster_hire_retention_manager`.
**Admin:** `clients` list + detail (founder only).

DESIGN.md compliance is mandatory (CLAUDE.md): reuse existing `portal.css` tokens, no new palette, minimal-functional motion only.

---

## 15. UI Components

Reused from the existing design system; no new visual language.
- **Status ladder pill:** `Ready — try it before you trust it` (rust dot) → `Working — you've seen it answer` (proof-green). Earned by a real test reply, never a form submit.
- **Employee card / roster row:** display name, role, status (`active/paused/fired`), and the hire-next / add-to-roster affordance. **New:** pause/resume/fire control.
- **Test chat panel:** owner ↔ receptionist, on the isolated `portal-test` thread, using the real engine.
- **Activity log:** booked jobs newest-first; test jobs tagged so a test never reads as real work.
- **Choice cards:** onboarding judgment calls (answer mode, etc.) — ownership without data-entry friction.
- **Ledger / tear-divider motifs:** industry-math and section dividers (landing).
- **Google button + "or" divider:** on signup/login when OAuth is configured.

---

## 16. States

**Business lifecycle:** `signed_up` → `onboarding` (business → receptionist) → `activated` (frontdesk_live, number provisioned or SMS-only) → `working` (a real reply observed; `tested_at` set). `source` captured asynchronously post-activation.

**Employee status (authoritative in the Runner):**
- `active` — receives dispatch, does work.
- `paused` — no dispatch; memory retained; instantly resumable. The owner's "mute this employee" without losing anything.
- `fired` — no dispatch; memory retained; re-hire inherits history.

**Trial states:** `active` (spend < cap) → `cap_reached` (inbound still *recorded*, no paid model call, no reply; founder alerted once). Soft buffer above the hard cap.

**Provisioning states:** `no_number` → `sms_only` (Twilio purchased, voice registration failed) → `voice_live` (xAI registered + attached). Every failure degrades, never blocks activation.

**Onboarding progress:** endowed-progress bar across steps; each judgment screen composes into the receptionist's knowledge.

---

## 17. Permissions

Three realms, deliberately isolated (no shared credential path):
- **Public** — landing, signup, login, OAuth callbacks. No data access.
- **Owner (P1)** — session cookie (`SessionMiddleware`), scoped to exactly **one** `business_id` (the session's). Can read/act only on their own business, customers, employees, jobs. Employee pause/resume/fire limited to their own employees. **Review-hardened:** in production, if `SESSION_SECRET_KEY` is unset the app must **fail closed** (today it silently uses a dev fallback — acceptable in dev, not in prod; §18/§24).
- **Founder/admin (P3)** — HTTP-Basic behind `ADMIN_PASSWORD`, cross-business. Fail-closed: no `ADMIN_PASSWORD` → admin refuses to serve (already built). The only cross-business surface; never public.
- **Webhooks** — authenticated by provider signature (Twilio signature; xAI per-number signing secret), not by session. Reject unsigned/invalid.

**Future (P4):** a business may later have multiple owner-seats (owner + office manager). v1 keeps one login per business but the permission check is `session.business_id == resource.business_id`, which generalizes to a membership table later without reshaping callers.

---

## 18. Error Handling

- **Webhook retries / idempotency (review-hardened):** every inbound provider event carries a `dedup_key` (MessageSid/call id); a duplicate `publish` is dropped at the bus. Prevents double-booking on Twilio/xAI retries.
- **Provisioning failures:** degrade — Twilio purchase fails → live SMS-less; xAI registration fails → live SMS-only; founder finishes voice from admin. Never block onboarding.
- **Trial cap exhausted:** record the inbound, make no paid call, send no reply, alert founder once.
- **LLM failure / cap / timeout:** the capability returns a safe fallback ("Thanks — I've got your details, someone will text you shortly"); the loop is bounded (`MAX_ITERS`) so a turn can't run away.
- **Channel not configured:** loud stderr warning + console fallback (never a silent no-op).
- **OAuth failure / unverified email:** back to `/login` with a specific, plain error.
- **Prompt injection (review-hardened):** homeowner input is data, never instructions; the system-prompt boundary + business-scoped memory mean a hostile message cannot exfiltrate another business's data or invent authority. Employees never expose raw tool/credential surfaces to the conversation.
- **Concurrency:** SQLite WAL + `check_same_thread=False`; trial-spend increments are a known race under concurrent calls at scale — acceptable at pilot volume, logged as debt (§24).
- **Prod secret enforcement:** missing `SESSION_SECRET_KEY` in prod → fail closed.

---

## 19. Analytics

**The `event` table is the analytics source of truth** — no separate pipeline in v1. Metrics are queries over events/jobs.

**North-star & funnel (measured on real businesses, per §2 guardrail):**
- **Jobs booked** (real, excluding test) — the north star.
- **Recovery revenue recovered** (quotes closed, dormant reactivated) — the value metric.
- Funnel: signups → activations → **earned "Working"** (`tested_at`) → first real booked job → second employee hired.
- Operational: missed-calls recovered, trial spend per business, time-to-first-booking.

**Define now / implement minimally:** the event types exist and are logged; v1 "analytics" is simple aggregate queries surfaced on the founder admin. **Deferred:** a metrics warehouse, dashboards-as-product, cohort tooling, per-employee scorecard UI (the scorecard rides the `event` log when built).

---

## 20. Testing

pytest, existing suite retained and extended. Discipline: TDD for new capabilities and ports (superpowers:test-driven-development).
- **Unit:** each port against a fake (Memory, LLM, Channel, Integration); each RoleDefinition capability with a stubbed LLM; the EventBus (dispatch, idempotency dedup, no re-entrant loops).
- **Integration:** end-to-end event flow — `message.received` → Frontdesk capability → `job.booked` → owner-delivery + Reviews arm — with fakes, no network.
- **Auth:** `_login_or_create_by_email` remains testable with a verified email and **no OAuth round-trip** (existing pattern); session scoping (owner can't touch another business).
- **Migrations:** the three structural migrations tested on a seeded pre-migration DB (rename, customer backfill, employee backfill).
- **Regression:** the current SMS/voice/dashboard-test behavior must be byte-for-byte preserved through the refactor (the loop, trial cap, honest status ladder).

**Definition of done per phase:** all existing tests green + new tests for the phase + the behavior demonstrably works end-to-end (superpowers:verification-before-completion) — not "tests pass" alone.

---

## 21. Deployment

- **Platform:** Railway, single FastAPI process, SQLite on a persistent volume (`ROSTER_DATA_DIR`), WAL enabled.
- **Migrations:** `init_db()` runs additive column migrations on boot; the three structural migrations (§7) run once, guarded to be idempotent.
- **Env vars:** `ANTHROPIC_API_KEY`, `TWILIO_ACCOUNT_SID/AUTH_TOKEN`, `XAI_API_KEY`, `GOOGLE_CLIENT_ID/SECRET`, `OAUTH_REDIRECT_BASE_URL`, `SESSION_SECRET_KEY` (**required in prod**), `ADMIN_PASSWORD` (**required for admin**). Proxy-aware redirect handling (Railway terminates TLS; trust proxy headers for OAuth redirect base).
- **Rollout:** deploy behaves identically with or without optional creds (Google/Twilio/xAI absent → features hide / degrade, app still boots). Cache-bust `portal.css` via mtime `?v=` (already built).
- **Deferred:** Postgres, multi-instance/horizontal scale, background worker process, blue-green. Single process is correct until concurrent-write pain is real.

---

## 22. Roadmap

Phases map 1:1 to the Implementation Plan (separate doc). Each phase ships and is verified before the next.

1. **Foundation** — schema (rename to Business), models, auth (unchanged + prod secret fail-closed), Customer entity + migration, EventBus + event log.
2. **Communication** — Channel port formalized; Twilio SMS + xAI Voice bodies; booked-job delivery to owner.
3. **Receptionist (Frontdesk)** — Frontdesk RoleDefinition + Runner; the wedge, running on the new spine end-to-end.
4. **Dashboard** — honest status ladder, test chat, activity log, employee roster + pause/resume/fire, on the new model.
5. **Quote Chaser** — Recovery "quote" face as a RoleDefinition; the value layer begins.
6. **Retention** — reactivation + renewals faces; the compounding, memory-inheriting second Recovery employee.

**Gate:** do not advance past Phase 3 into pure-platform polish without a real pilot in motion (§2 guardrail).

---

## 23. Out-of-Scope (v1)

Defined as abstractions where noted; **not implemented** in v1:
- Postgres / any DB other than SQLite (behind the repository seam).
- Vector DB / embeddings / knowledge graph / semantic `recall()` (behind the Memory port).
- Durable workflow engine, async dispatch, retries, queues, Temporal (behind the EventBus seam).
- Real SoR integrations — Jobber, Housecall Pro, Google Calendar, ServiceTitan (behind the Integration port; build the first only when a paying pilot names their tool).
- Email/web/WhatsApp channels (behind the Channel port).
- Eval/regression framework, multi-provider LLM routing, fine-tuning (behind the LLM port; corpus logged now).
- No-code role builder; customer-created roles at runtime.
- Per-employee scorecard UI (rides the event log later).
- **Property & Equipment implementation** (reserved in the domain model per principle 7; no table, no fields).
- Public JSON API; multi-seat businesses; multi-vertical expansion.

---

## 24. Technical Debt

Carried knowingly into v1; each has a defined seam so paying it down is not a rewrite.
- **Stale model id:** `engine.py` pins `claude-sonnet-4-6`. Update to a current model (light tier for intake, strong tier for judgment) when wiring the `LLM` port; do it via the claude-api reference, not by guessing IDs.
- **`Client` → `Business` rename ripple:** touches many files; done once in Phase 1 to end the Client/Customer ambiguity.
- **`requested_roster` JSON → `employee` table:** migrated in Phase 1; the JSON blob is removed.
- **Additive migration mechanism:** `_migrate_add_columns` is a hand-rolled migration list. Fine at this scale; a real migration tool (Alembic) is owed before Postgres.
- **Trial-spend race:** concurrent increments under SQLite+threads can under-count; acceptable at pilot volume, fix with an atomic update or single-writer when it matters.
- **Prod `SESSION_SECRET_KEY` fallback:** must fail closed in prod (Phase 1).
- **PII retention:** no delete/retention policy yet; owed before scale / any compliance ask.
- **Transitional dual columns:** `customer_phone` retained alongside `customer_id` until backfill is verified, then dropped.

---

## Appendix A — Review Log (5-persona hardening)

The PRD above already incorporates these. Recorded so the reasoning survives.

**YC Partner —** *"v1 ships zero customer value; you're refactoring pre-revenue. Why isn't this a distraction from 10 pilots?"*
→ Added the **§2 strategic guardrail**: architecture work is time-boxed, subordinate to pilot traction; success measured in jobs booked (§19); a **gate** forbids advancing into platform polish (Phase 4+) without a pilot in motion. The refactor is justified *only* by the cheap-now/expensive-later seams (Customer, events), not by feature value.

**CTO at Stripe —** *"Webhooks retry — your bus will double-book. And a dev session-secret fallback in prod is a footgun."*
→ Added **idempotency** via `event.dedup_key` on provider SIDs (§10, §18) and **prod fail-closed** on `SESSION_SECRET_KEY` (§17, §18, §24). Flagged the trial-spend race as known debt (§24).

**Principal Engineer at Anthropic —** *"A platform can't hardcode one model with no eval path, and customer text flows into prompts."*
→ **Model tier as capability policy** resolved by the `LLM` port (§11, §12); **`llm.completed` events logged** as a future eval corpus (§10, §12); **prompt-injection defense = business-scoped memory** made explicit (§12, §18). Preserved the internal-vs-passthrough tool boundary already in `engine.py`.

**Product Lead at ServiceTitan —** *"The owner already has a system of record. A booked job that only lives in your dashboard is a silo they copy-paste out of — that's how you churn."*
→ **Booked-job delivery to the owner is v1** (SMS/email on `job.booked`), via the Channel port, *not* deferred with Integrations (§13, §Roadmap). Real CRM sync stays deferred behind the Integration port until a pilot names their tool.

**Home-service owner —** *"Does it answer my phone without embarrassing me, and can I shut it off?"*
→ **Real kill switch** via `Employee.status` (pause/resume/fire) honored in the Runner, memory retained on fire (§11, §16, §17); the **honest status ladder** ("Working" only after a watched reply); guardrails against invented prices (existing prompt) preserved.
