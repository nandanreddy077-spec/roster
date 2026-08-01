# Architecture Gap Analysis — current codebase vs. docs 000–003

**Date:** 2026-08-01
**Scope:** `agent/` (133 Python files, ~21,900 lines, 801 tests passing)
**Method:** read-only. No code was modified.
**Compared against:** `000-ROSTER_ARCHITECTURE_CONSTITUTION.md`, `001-PLATFORM_ARCHITECTURE.md`, `002-DECISION_MODEL.md`, `003-DOMAIN_MODEL.md`

---

## Executive summary

The codebase is **not** an unstructured monolith that needs a rewrite to reach the target
architecture. It is a working product with several target components already built to a high
standard under different names, and three components (Decision Runtime, Policy Engine,
Authority Engine) that do not exist at all.

The single largest gap is not missing infrastructure. It is that **business rules that 001 and
000 require to be deterministic are currently enforced only by asking the language model
nicely, inside a prompt string.**

Component readiness at a glance:

| Component | State | Headline |
|---|---|---|
| Communication Platform | **Conformant** | Port + 2 adapters, no business logic. Already the target shape. |
| Identity Platform | **Mostly built** | Business/Customer correct, incl. the hard case. Contact/Property/Equipment absent (and not needed). |
| Workflow Engine | **Built, per-employee** | Real state machine + claim/retry/compensation in Recovery. Not generalized. One expiry bug. |
| Observability | **Partial** | Excellent for voice (`CallTrace`), `print()` everywhere else. |
| Event Platform | **Built but dead** | Table, bus, dedup, 14 event types — and **zero publishers** in production code. |
| Decision Runtime | **Re-implemented ×4** | The lifecycle exists, copied across four handlers. No shared orchestrator, no Decision record. |
| Policy Engine | **Absent for the rules that matter** | Deterministic engines exist for two axes; hours/pricing/service-area live in prompts. |
| Authority Engine | **Coarse only** | Deployment-level yes/no is real and enforced structurally. No per-action authority. |
| Knowledge Platform | **Effectively absent** | One free-text column. A defined port with no adapter and no callers. |

**Two documented invariants are false today** and should either be marked
*not yet implemented* or fixed:

- 001: *"Every business action flows through the Decision Runtime"* — there is no Decision Runtime.
- 000 Law #4 / 001 anti-pattern #1: *"Policies live outside prompts"* / *"Never put business rules
  in prompts"* — `engine.py:259-262` and `engine.py:295-298` interpolate hours, pricing and
  service area directly into the system prompt, and `_service_area_note` (`engine.py:236`)
  instructs the model to enforce the service-area rule itself. No code checks it.

---

## 1. Decision Runtime

*Target: 002 in full; 001 Layer 3.*

### What already exists

- **The proposal/execution split is already a code contract.** `AgentEngine.respond`
  ([engine.py:326](../../agent/engine.py)) runs a bounded Think→Act→Observe loop and returns any
  non-`log_job` tool call as `pending_tool_call` **without executing it** — "only the caller can
  actually execute it, so the loop stops immediately." `alert_owner` genuinely is a proposal the
  platform then authorizes and executes ([xai_voice_adapter.py:272](../../agent/xai_voice_adapter.py)).
  This is 002's "AI proposes, the platform executes," already implemented.
- **Execution stage exists and is idempotent.** `bookings.book_job` and
  `bookings.record_escalation` ([bookings.py:25, :90](../../agent/bookings.py)) are the single write
  paths for the only two business actions in the product. Both return an explicit
  *did-this-actually-happen* signal (`created`, `should_notify`).
- **A near-complete lifecycle exists in one place.** `recovery_service.handle_recovery_reply`
  ([recovery_service.py:287](../../agent/recovery_service.py)) runs: proposal (`agent.respond` with
  tools) → validation (`0 <= idx < len(slots)`) → execution (`book_job`) → observation
  (`delivered = notify_owner_of_booking`) → audit (`record_owner_notification`) → completion
  (`current_status`). That is 8 of 002's 11 stages, in production, today.
- **A completion-state vocabulary exists.** `RecoveryJob.current_status` —
  `pending | awaiting_slot | booked | declined | no_response | escalated` — maps almost 1:1 onto
  002's Completion states.

### What partially exists

- The lifecycle is **re-implemented four times, not shared**: `service.handle_customer_message`,
  `recovery_service.handle_recovery_reply`, `review_service.handle_review_reply`,
  `referral_service.handle_referral_reply`, plus `xai_voice_adapter._handle_function_call`. Each
  does its own propose → validate → execute → notify → record. They agree by discipline, not by
  construction.
- Audit is spread across three partial records — `OwnerNotification` (what the owner was told,
  with `delivered`), `Message` (the conversation), `CallTrace` JSONL (voice stage timeline) —
  none of which is a decision record.
- **The SMS booking path has no proposal stage at all.** `log_job` is resolved *inside*
  `engine.respond`, and `service.handle_customer_message:114` books the result unconditionally.
  Nothing sits between the model's output and the database write.

### What is missing

- A `Decision` entity. Nothing in `db_models.py` corresponds to 002's Decision Object.
- Stages 3 (Evidence Collection), 5 (Policy Evaluation), 6 (Authority Evaluation) — no code.
- Replay. You cannot reconstruct *why* a given booking happened from stored data.
- An explicit failure vocabulary (Retry / Escalate / Reject / Wait / Cancel). All five behaviours
  exist somewhere; none is named or uniform.

### What can be reused

- `pending_tool_call` — this **is** the Decision Proposal contract. Do not invent a second one.
- `book_job` / `record_escalation` as the Execution stage.
- `RecoveryJob.current_status` as the Completion enum.
- `runner.dispatch_job_completed` / `runner.dispatch_tick` as the capability-dispatch seam.

### What should not be rewritten

- **`engine.py`'s loop and `merge_consecutive_roles`.** Anthropic's strict alternation requirement
  makes this subtle; 437 lines of tests cover it.
- **`bookings.py`.** The 24-hour dedup-window upsert and `record_escalation`'s `should_notify`
  semantics (retry after a *failed* page still fires; repeat after a *successful* one is deduped)
  are correctness-critical and covered by `test_idempotency.py`.
- **`locks.conversation_lock`.** The threading.Lock/`pg_advisory_lock` split by dialect is correct
  and unpleasant to re-derive.

### Migration strategy

**Build the record before the runtime.** Adding an orchestrator first means restructuring five
working handlers with nothing to verify against.

1. **D1 — `Decision` table + write it from the existing branch points.** Fields from 002's Decision
   Object, minus `policy_version` and `authority_result` (nothing produces those yet). Written at
   the points where the branch is *already* being taken in each of the four handlers. Purely
   additive; no behaviour change; delivers audit and explainability, which is what 000 and 002
   actually care about.
2. **D2 — extract `decide()` once two handlers demonstrably fit the same shape.** Recovery and
   Reviews are the closest pair. With D1's rows in place, the refactor has a verifiable invariant:
   the same Decision rows before and after.
3. **D3 — add the missing stages** (Evidence, Policy, Authority) into `decide()` once §2 and §3
   below give it something real to call.

---

## 2. Policy Engine

*Target: 001 §Policy Engine; 002 stage 5; 000 Law #4.*

### What already exists

- **The policy data exists**, on `Business`: `hours`, `pricing_faq`, `service_area`, `answer_mode`,
  `escalation_phone` ([db_models.py:16-22](../../agent/db_models.py)).
- **A correct Policy Engine pattern is already in production, twice.**
  `lead_qualifier_rules.py` + `lead_qualifier_engine.py`, and `dispatcher_rules.py` +
  `dispatcher_engine.py`. Pure functions, no DB, no LLM, and — critically — they emit
  **structured reason-code enums, never English prose**, stored on the output row so analytics can
  group by exactly which rule fired. This is precisely what 001 asks a Policy Engine to be.
- `trial_cap.can_respond` ([trial_cap.py:949](../../agent/trial_cap.py)) — a deterministic spend
  policy evaluated *before* the model is called. A real policy gate on a real money path.
- Operational policies as named constants: `BOOKING_DEDUP_WINDOW_HOURS`,
  `MAX_CALL_DURATION_SECONDS`, `MAX_CALL_TOKEN_BUDGET`.

### What partially exists

- Nothing is versioned. `Business.hours` is a mutable string column; changing it destroys the basis
  of every past decision. 001 requires policies to be versioned.

### What is missing

**This is the most significant gap in the analysis.**

- **`hours`, `pricing_faq` and `service_area` are enforced only inside the LLM prompt.**
  `engine.py:259-262` (SMS) and `engine.py:295-298` (voice) interpolate them as free text.
  `_service_area_note` ([engine.py:236](../../agent/engine.py)) generates the instruction
  *"If they're outside this area, say so honestly and don't book the job"* — a business rule
  delegated entirely to model compliance. Nothing in `book_job` checks a service area.
- Emergency policy is prose. `TRADE_TRIAGE_NOTES` ([engine.py:89-143](../../agent/engine.py)) is 10
  trades' worth of English triage guidance in the prompt. It is good copy; it is not a policy engine.
- Nothing evaluates anything between the model's `log_job` and the `book_job` write.

### What can be reused

- The `*_rules.py` / `*_engine.py` split, verbatim. A `policy_rules.py` / `policy_engine.py` pair
  costs almost nothing sitting next to the two that already exist.
- `JobQualification.reasoning` as the model for a policy result: comma-joined structured enums in a
  fixed order, never prose.

### What should not be rewritten

- **Lead Qualifier and Dispatcher.** They *are* the Policy Engine for the axes they cover. Fold them
  in under the name; do not replace them.

### Migration strategy

1. **P1 — make service area a real policy.** Highest trust-per-line in the entire plan.
   `policy_engine.check_service_area(business, address) -> allow | deny | unknown`, called from
   `bookings.book_job` — the one write path every booking already funnels through, so a single
   guard covers SMS, voice, and Recovery simultaneously. Keep the prompt copy for conversational
   honesty; the rule stops being advisory. `unknown` must not block a booking (the field is free
   text and frequently unset).
2. **P2 — hours as a predicate.** `is_within_hours(business, now)`. `_format_now` already proves the
   model receives the current time; this makes after-hours a real branch instead of a suggestion.
3. **P3 — versioning.** A `policy_version` (content hash, or an integer bumped on write) on
   `Business`, stamped onto the Decision row from D1. Only worth doing once Decision rows exist to
   stamp it onto.
4. **Pricing stays in the prompt** until a customer complains about a wrong quote. `pricing_faq` is
   genuinely free-form today; structuring it is a product decision, not an architecture one.

---

## 3. Authority Engine

*Target: 001 §Authority Engine; 002 stage 6; 000 Law #2, #6.*

### What already exists

- **Deployment-level authority is real and structurally enforced.** `runner.is_active`
  ([runner.py:308](../../agent/runner.py)) and `runner.deployed_businesses` ([runner.py:348](../../agent/runner.py))
  read the `Employee` row — which *is* the deployment record — and normalize legacy key spellings
  through `departments.canonical_role_key`.
- **`runner.dispatch_tick` ([runner.py:375](../../agent/runner.py)) is a genuine authority chokepoint.**
  Its docstring states the design explicitly: no tick employee queries across businesses itself, so
  "there is no reachable code path to an undeployed business's data to forget to guard." Authority
  by construction, not by convention. This is the pattern the rest of the component should follow.
- `trial_cap.can_respond` — authority over spend.
- `Employee.status` (`active | paused | fired`) and **`Employee.policy_json`, which is written by
  nothing.** An empty, already-migrated slot sized exactly for per-employee authority rules.
- Business-scoping is the security boundary and is enforced on every query;
  `expansion.record_interest` validates customer-supplied department keys at the boundary.

### What partially exists

- Authority is **deployment-level only**. A deployed Frontdesk may book anything, at any urgency, at
  any hour, for any amount.
- Human approval does not exist as a *gate*. Escalation exists (`alert_owner`, `_escalate`) but it
  is a **notification after the fact** — the Job is written before the owner is told, deliberately
  ("persist the lead FIRST so an emergency caller is never lost", `xai_voice_adapter.py:275-278`).
  That is the right call for an emergency; it is not authority.

### What is missing

- Any "requires human approval" decision state. Nothing pauses pending an owner's yes.
- Per-action permission rules.
- An authority result recorded anywhere.

### What can be reused

- **`Employee.policy_json`** — the intended home, on the table already, zero migration cost.
- `runner.dispatch_tick`'s one-chokepoint pattern.
- `current_status == "escalated"` as the nearest existing analogue to "awaiting a human".

### What should not be rewritten

- `runner.is_active` / `deployed_businesses` / `canonical_role_key`. The legacy-key normalization
  (`"retention"` vs `"retention_manager"`) runs over real production rows.

### Migration strategy

1. **A1 — `authority.can(employee, action, context) -> allow | require_approval | deny`,
   reading `Employee.policy_json`, with an empty policy defaulting to `allow`.** Deploying this
   changes no behaviour on day one, which is the point.
2. **A2 — call it from `book_job` and `record_escalation`.** Still allow-by-default. Record the
   result on the Decision row.
3. **A3 — do NOT build `require_approval` until a customer asks for it.** A pending-approval state
   needs a queue, a notification, a timeout, an expiry and an owner-facing UI. That is a subsystem,
   and today it has no user. Write the first real rule into `policy_json` only when an owner says
   something like *"don't let it book emergency work overnight."*

---

## 4. Workflow Engine

*Target: 001 §Workflow Engine; 003 (Appointment, Job, Workflow → Workflow Engine).*

**This is the strongest of the seven components.**

### What already exists

- **`RecoveryJob` is a real workflow instance**: `current_status` state machine, `last_sent_day`
  cursor, `offered_slots_json` working state, `booked_job_id` outcome link, `source_job_id`
  idempotency key.
- **`recovery_service.tick` ([recovery_service.py:180](../../agent/recovery_service.py)) is a correct
  at-least-once worker with compensation.** It claims the day before sending via a conditional
  `UPDATE ... WHERE last_sent_day = prior_day`, checks `rowcount`, and — on send failure —
  **releases the claim so the next tick retries** (lines 247-259). Hand-written, and right.
- `_next_due_day` ([recovery_service.py:161](../../agent/recovery_service.py)) jumps to the furthest
  missed threshold rather than replaying a backlog as a burst of texts.
- `scheduler.run_scheduler` — crash-tolerant interval loop; a failing tick is logged and never kills
  the loop. `recovery_tick.run` — an ordered, fully idempotent pipeline (qualify → dispatch → enroll
  → send → referral → review → follow-up), each step gated on its own timestamp column.
- Workflow state is correctly kept **off** the conversation: `completed_at`, `review_requested_at`,
  `review_followup_sent_at`, `referral_sent_at`, `owner_alerted_at` live on `Job`.

### What partially exists

- Workflow state is **per-employee, not generic**: `RecoveryJob` is Quote Chaser's machine, Reviews'
  state is timestamp columns on `Job`, Frontdesk has none.
- **Retry strategy is per-site and inconsistent.** Recovery claims-then-releases (never double-text,
  may skip). Reviews sends-then-marks ([review_service.py:57-71](../../agent/review_service.py)) — if
  the commit fails after a successful send, the next tick re-sends. Opposite trade-offs, neither
  declared. 001's Failure Philosophy requires every service to define its retry strategy.

### What is missing

- No generic `Workflow` entity — 003 lists it as a core entity; nothing implements it.
- No timeout/expiry. **Concrete bug:** `tick()` queries only `current_status == "pending"`
  ([recovery_service.py:186](../../agent/recovery_service.py)), and the `no_response` transition
  (line 207) lives inside that same loop. A `RecoveryJob` that reaches `awaiting_slot` — customer
  said "interested", was offered three slots, then went silent — **is never advanced, never expires,
  and never re-enters any tick.** Because `awaiting_slot` is in `ACTIVE_STATUSES`,
  `find_active_recovery_job` keeps routing that customer's future texts into the slot state machine
  indefinitely. This violates 002's "No decision disappears silently" and its `Expired` completion
  state.

### What can be reused

- The claim-then-release pattern — this is the retry primitive for every future workflow.
- `scheduler.run_scheduler` + `recovery_tick.run` as the execution substrate.
- `runner.dispatch_tick` as the per-business fan-out.

### What should not be rewritten

- **`recovery_service.tick`'s concurrency handling** and `_next_due_day`. Both were clearly earned.

### Migration strategy

1. **W1 — fix the `awaiting_slot` expiry hole.** One additional branch in `tick()`: an
   `awaiting_slot` job whose last message is older than a threshold moves to `no_response`
   (or a new `expired`). Smallest real defect in this analysis, and it maps directly onto a
   stated invariant.
2. **W2 — declare the retry rule.** One shared convention (claim-first, as Recovery does) applied to
   Reviews and Referral, or an explicit note saying why they differ.
3. **W3 — generalize nothing yet.** Two workflow shapes exist and both work. Extract a `Workflow`
   entity when a third employee needs a shape neither fits — not before.

---

## 5. Event Platform

*Target: 001 §Event Platform; 000 Law #5, #8; 003 (Event → Event Platform).*

### What already exists

- `db_models.Event` — `business_id`, `type`, `payload_json`, `customer_id`, `employee_id`,
  unique `dedup_key`, `occurred_at`. Append-only by construction (no update path exists).
- `events.py` — **14 named event types already defined**: `MESSAGE_RECEIVED`, `CALL_MISSED`,
  `JOB_BOOKED`, `JOB_COMPLETED`, `QUOTE_SENT`, `REVIEW_REQUESTED`, `REFERRAL_RECEIVED`, … These
  match 001's examples closely.
- `eventbus.EventBus` — publish with dedup (unique-key insert + `IntegrityError` fallback),
  synchronous in-process subscriber dispatch, injectable engine.

### What is missing

**Nothing publishes.** Across all non-test code there is not one call to `bus.publish`. The only
`Event(...)` construction outside tests is inside `eventbus.publish` itself. In production the Event
table is written by nothing — and `app.delete_client:738` deletes from it.

The code says so plainly:

- `db_models.py:358` — *"nothing in the live SMS/voice path publishes through eventbus.py today."*
- `notifications.py:7` — *"Deliberately a direct outbound send, not routed through the EventBus —
  there is exactly one subscriber to a booking today."*
- `runner.py:12-16` — refuses to use the `bus` singleton because it binds `db.engine` at import time.

Consequently: no replay, no projections, no consumers.

### What partially exists

- The *durable-record* half exists in three places that **are** used: `OwnerNotification` (owner
  alerts, with a `delivered` flag — the only place a failed send is visible at all), `Message`
  (conversation), `CallTrace` JSONL (voice event stream). Real append-only logs; just not the Event
  table.

### What can be reused

- All of it. `eventbus.py` + `events.py` + the `Event` table are ~60 lines, correct, tested and free.
  **The gap is publishers, not infrastructure.**
- The `dedup_key` convention already used by `WebhookDelivery` (`"twilio-sms:{sid}"`,
  `"xai-call:{id}"`).

### What should not be rewritten

- `eventbus.publish`'s dedup-by-insert — the same try/commit/rollback/re-select pattern as
  `repositories.get_or_create_customer` and `expansion.record_interest`. Consistent across the
  codebase.

### Migration strategy

1. **E0 — fix the import-time engine binding first (~5 lines).** `EventBus.__init__` resolving
   `db.engine` at import ([eventbus.py:12-13](../../agent/eventbus.py)) is the single reason
   `runner.py` refuses to use it and every test builds its own `EventBus(test_engine)`. Make
   `publish` take a `session`, matching every other service in this codebase. This one change
   unblocks the entire component.
2. **E1 — publish from the chokepoints that already exist.** `book_job` already returns
   `created: bool` → publish `JOB_BOOKED` only when `True`, `dedup_key=f"job:{job.id}"`. Same for
   `record_escalation`, `complete_job`, and Recovery's send. Additive, dedup-guarded, no behaviour
   change.
3. **E2 — leave `notifications.py` alone.** Its "one subscriber today" reasoning is correct. Emit the
   event *alongside* the direct send; route the send through the bus only when a second subscriber
   actually appears.

---

## 6. Identity Platform

*Target: 001 §Identity Platform; 003 (Business, Customer, Contact, Property, Equipment).*

### What already exists

- `Business` — the aggregate root. Business-scoping is enforced on every query in the codebase and
  is treated as the security boundary throughout.
- `Customer` — first-class, with `UniqueConstraint(business_id, phone)`.
  `repositories.get_or_create_customer` is the single creation path, with an `IntegrityError`
  re-select for concurrent inserts.
- **003's canonical rule #1 ("Identity exists independently of communication") is already satisfied
  in the hardest place.** `_persist_job` ([xai_voice_adapter.py:184](../../agent/xai_voice_adapter.py))
  keys the Customer on `caller_number` and *never* on `thread`, with the reasoning inline:
  `xai-voice:{call_id}` is unique per call, not per customer. A repeat voice caller therefore
  resolves to the same `Customer` row an earlier SMS conversation created.
- `memory.build_customer_context` — business-scoped identity recall with prompt-injection
  sanitization (`_clean_field` strips control characters, collapses newlines, hard-caps length).

### What partially exists

- `Customer` conflates identity with contact. `phone` lives on `Customer` and is half the unique key.
  003 says *"Customer owns relationships, not phone numbers"* and lists `Contact` separately; today
  Customer **is** the contact.
- `Business` conflates identity with configuration and billing: `business_name`/`trade` sit beside
  `xai_signing_secret`, `trial_spend_cents`, `pipeline_stage`, `requested_roster`. A ~30-field row.

### What is missing

- `Contact`, `Property`, `Equipment` — no tables. Addresses exist only as free-text `Job.address`.

### What can be reused

- `get_or_create_customer`, the `(business_id, phone)` unique constraint, and the
  caller_number-not-thread rule.

### What should not be rewritten

- **Customer identity resolution.** The voice loop depends on it and it is subtly correct.

### Migration strategy

**Do none of it yet.** Contact / Property / Equipment are YAGNI at zero customers — nothing in the
product asks for a second contact or a second address, and three empty tables would cost every
future query a join for no observable benefit. The platform-architecture record already treats these
as *reserved*; that remains the right status.

The one item worth eventually doing — splitting billing/provisioning columns off `Business` — touches
everything and delivers nothing a customer can see. Not now.

---

## 7. Knowledge Platform

*Target: 001 §Knowledge Platform (FAQs, company knowledge, manuals, retrieval, versioning,
provenance).*

### What already exists

- `Business.pricing_faq` — **one free-text column. That is the entire knowledge store.**
- `memory.build_customer_context` — retrieval of a kind: customer + last 3 jobs, business-scoped,
  injection-sanitized.
- `memory.BusinessMemory` — a port with `profile / get_customer / upsert_customer / timeline /
  recall`, whose docstring states `recall()` is *"a recency stub — semantic swap later, same
  signature, no caller changes."*

### What partially exists

- `BusinessMemory` is **a defined abstraction with no callers.** Nothing in the running app
  constructs it; only `test_customer_memory.py` does. The port exists; there is no adapter and no
  consumer.

### What is missing

- Everything else. No FAQ entity, no documents, no retrieval, no embeddings, no versioning, no
  provenance. A grep for `knowledge|retriev|embedding|vector|rag` across non-test code returns
  nothing relevant.

### What can be reused

- `BusinessMemory` as the seam, if and when knowledge arrives.
- **`memory._clean_field`.** Any retrieved knowledge entering a prompt needs exactly this
  sanitization, and it already exists and is tested.

### What should not be rewritten

- `_clean_field` and the business-scoping rule in `build_customer_context`. That is a security
  boundary against cross-tenant leakage and prompt injection.

### Migration strategy

**Lowest priority of all seven.** No customer has asked. One free-text `pricing_faq` field is
working. The honest move is to leave it until an owner reports *"it gave the wrong answer about X"* —
at which point you know what X is and can build for X specifically.

If it does get built: a `KnowledgeItem(business_id, text, source, version)` table read through
`BusinessMemory.recall`, keyword-matched. Not a vector database.

---

## Addendum — two components in 001 not in the brief

**Communication Platform — the most architecture-conformant part of the repo.** `channels.py` is an
`SMSChannel` Protocol with Twilio and Console adapters and a deliberately loud dev fallback;
`xai_voice_adapter.py` owns the voice WebSocket lifecycle; `provisioning.py` owns number purchase and
registration. Business logic genuinely does not live in any of them. 001's replaceability requirement
is met here already. **Change nothing.**

**Observability Platform — excellent for voice, absent elsewhere.** `CallTrace` gives per-call stage
tracing with millisecond offsets, JSONL persistence, a pre-auth quarantine file that cannot be
steered by attacker-supplied input, and size-based rotation. SMS and tick paths use `print()`.
`OwnerNotification.delivered` is the only place in the system where a failed send is visible.
Extending `CallTrace`'s discipline to the tick pipeline is worth doing — but after the Decision
record (D1), which is a better carrier for most of what you would want to trace.

---

## Recommended implementation order

Ranked by **risk minimized × reuse maximized**, not by document order.

| # | Step | Component | Why here | Risk |
|---|---|---|---|---|
| 0 | Fix `EventBus` import-time engine binding | Event | ~5 lines; unblocks a whole component; already a known defect the codebase routes around | Very low |
| 1 | Publish events from `book_job` / `record_escalation` / `complete_job` | Event | Infrastructure 100% built; additive, dedup-guarded; substrate for every other component's audit story | Very low |
| 2 | `Decision` table, written from the 4 existing branch points | Decision | Delivers audit + explainability — the two things 000/002 care most about — with zero restructuring | Low |
| 3 | Service area → deterministic check in `book_job` | Policy | Closes the clearest constitution violation, at the one write path everything funnels through | Low |
| 4 | `awaiting_slot` expiry in `tick()` | Workflow | A real bug, one branch, maps to a stated invariant | Low |
| 5 | Hours as a predicate | Policy | Same pattern as #3, now with a proven shape | Low |
| 6 | `authority.can(...)`, allow-by-default, called from `book_job` | Authority | Structure without behaviour change; `Employee.policy_json` already exists | Low |
| 7 | Extract `decide()` from two handlers | Decision | Only now is there a Decision table to verify the refactor against | Medium |
| 8 | `policy_version` stamped on Decision | Policy | Needs #2 and #3 first | Low |
| — | Contact / Property / Equipment | Identity | **Defer.** No customer, no query, pure cost | — |
| — | Knowledge Platform | Knowledge | **Defer.** No customer has asked | — |
| — | `require_approval` state | Authority | **Defer.** A queue + notification + timeout + UI is a subsystem with no user | — |

### Why this order

- **Steps 0–1 are almost free** and every later step's audit story depends on them.
- **Step 2 before step 7** is the load-bearing sequencing decision: build the record before the
  runtime, so the eventual refactor of five working handlers has a verifiable invariant.
- **Steps 3–5 are where the trust value is.** They convert documented-but-false invariants into true
  ones, and each is a guard at a chokepoint that already exists rather than a new layer.
- **Everything deferred is deferred for the same reason:** it has no customer, and the standing
  engineering directive ranks AI-employee capability above architectural completeness.

### Two decisions to make before starting

1. **The docs assert two invariants that are currently false** (no Decision Runtime; policies in
   prompts). Either mark 001/002 explicitly as *not yet implemented* for those specific lines, or
   land steps 2–3. Leaving them as unqualified present-tense claims means anyone grepping the code
   will conclude the architecture documents are unreliable.
2. **`ARCHITECTURE.md` at the repo root is a different document with a colliding name.** It is the
   frozen `/v2/dashboard*` rendering layering (registries → view models → templates → nav). It does
   not conflict with 000–003 in substance, but the names invite confusion. Consider renaming it to
   `DASHBOARD-ARCHITECTURE.md` or folding it into `docs/architecture/` as a numbered volume with a
   scope line.
