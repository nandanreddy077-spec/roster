# 002 – DECISION MODEL

**Version:** 0.2 (Draft)  
**Status:** Target Architecture — **not implemented**  
**Owner:** Founding Team

---

> ## ⚠ Implementation status
>
> **Nothing in this document is implemented.** There is no Decision Runtime, no `Decision` entity,
> and no shared lifecycle. Every statement below is target state.
>
> What exists today, as of the 2026-08-01 audit
> ([`GAP-ANALYSIS-2026-08-01.md`](GAP-ANALYSIS-2026-08-01.md)):
>
> | Stage | Status |
> |---|---|
> | 1 Intent | 🟡 Inferred by the model, never named or stored |
> | 2 Context Collection | ✅ `memory.build_customer_context` |
> | 3 Evidence Collection | ❌ No code |
> | 4 Decision Proposal | 🟡 Real for passthrough tools (`AgentEngine.respond` returns `pending_tool_call` unexecuted); **absent for `log_job`**, which is resolved inside the loop and booked unconditionally |
> | 5 Policy Evaluation | ❌ No code |
> | 6 Authority Evaluation | ❌ No code |
> | 7 Validation | 🟡 Ad-hoc per handler (e.g. Recovery's slot-index bounds check) |
> | 8 Execution | ✅ `bookings.book_job` / `record_escalation`, idempotent |
> | 9 Observation | ✅ Send results are checked, never assumed (`delivered` booleans) |
> | 10 Audit | 🟡 Split across `OwnerNotification`, `Message`, `CallTrace` — no decision record |
> | 11 Completion | 🟡 Real for Recovery (`RecoveryJob.current_status`); absent elsewhere |
>
> The lifecycle as a whole is re-implemented in four handlers rather than shared. A post-hoc
> `Decision` audit record is planned as D1 in
> [`IMPLEMENTATION-PLAN-001.md`](IMPLEMENTATION-PLAN-001.md); the runtime itself is D2.

---

# Purpose

This document defines how every meaningful business action is executed inside Roster.

A booking, cancellation, quote, follow-up, review request, or dispatch are all instances of a **Decision**.

Every decision follows the same lifecycle regardless of which AI employee initiated it.

---

# Decision Philosophy

AI is responsible for reasoning.

The platform is responsible for correctness.

AI proposes.

The platform verifies.

The platform authorizes.

The platform executes.

The platform records history.

---

# Universal Decision Lifecycle

```
Intent
    ↓
Context Collection
    ↓
Evidence Collection
    ↓
Decision Proposal
    ↓
Policy Evaluation
    ↓
Authority Evaluation
    ↓
Validation
    ↓
Execution
    ↓
Observation
    ↓
Audit
    ↓
Completion
```

No business operation may bypass this lifecycle.

> ❌ **Not true today.** No business operation currently goes through this lifecycle at all,
> because it does not exist as shared code.

---

# Stage Definitions

## 1. Intent

Determine what the customer wants.

Examples:

- Book appointment
- Cancel appointment
- Request estimate
- Reschedule visit

Intent is never execution.

---

## 2. Context Collection

Collect relevant context:

- Customer
- Business
- Conversation
- Property
- History
- Preferences

No decisions are made here.

---

## 3. Evidence Collection

Gather verified facts.

Examples:

- Technician availability
- Business hours
- Membership status
- Service area
- Existing appointment

Evidence must come from authoritative platform services.

---

## 4. Decision Proposal

AI analyzes evidence and proposes one or more actions.

Proposal is advisory only.

It has no authority to mutate business state.

---

## 5. Policy Evaluation

The Policy Engine evaluates:

- Hours
- Pricing
- Membership rules
- Service restrictions
- Emergency rules

Only deterministic rules are applied.

---

## 6. Authority Evaluation

Determine whether the proposed action is permitted.

Examples:

- AI may approve automatically.
- Human approval required.
- Reject action.

---

## 7. Validation

Perform final consistency checks.

Examples:

- Prevent duplicate bookings.
- Ensure technician still available.
- Verify required fields.

---

## 8. Execution

Execute approved actions.

Examples:

- Create appointment.
- Send SMS.
- Update workflow.
- Create event.

Execution should be transactional where practical.

---

## 9. Observation

Verify outcomes.

Examples:

- SMS delivered?
- Booking persisted?
- Calendar updated?

Execution success is not assumed.

---

## 10. Audit

Record:

- Decision ID
- Inputs
- Evidence
- Policy version
- Authority result
- Actions taken
- Outcome
- Timestamps

Audit history is immutable.

---

## 11. Completion

A decision ends in exactly one state:

- Completed
- Rejected
- Failed
- Escalated
- Cancelled
- Expired

No decision disappears silently.

---

# Decision Object

> ❌ **Not implemented.** No `Decision` table exists. D1 of
> [`IMPLEMENTATION-PLAN-001.md`](IMPLEMENTATION-PLAN-001.md) creates a **post-hoc audit record**
> with 9 of these 16 fields — deliberately omitting `evidence`, `policy_version` and
> `authority_result`, because nothing produces those values yet and an always-`NULL` column claims
> a capability the platform does not have.

Every decision should include:

```yaml
id:
intent:
status:
customer_id:
business_id:
conversation_id:
workflow_id:
proposal:
evidence:
policy_version:
authority_result:
actions:
events:
outcome:
created_at:
completed_at:
```

---

# Decision Invariants

> 🟡 **Two of six are reachable today.** After D1, decisions become *Observable* and
> *Explainable*. *Verified*, *Authorized*, *Replayable* and *Recoverable* require the Policy
> Engine, the Authority Engine and the Decision Runtime, none of which exist.

Every decision must be:

- Verified
- Authorized
- Observable
- Replayable
- Recoverable
- Explainable

If any invariant cannot be satisfied, the decision must not execute.

---

# Error Handling

Failures are explicit.

Supported outcomes:

- Retry
- Escalate
- Reject
- Wait
- Cancel

Never invent missing information to continue execution.

---

# Replay Philosophy

A historical decision should be explainable using:

- Original evidence
- Original policy version
- Original authority result
- Recorded events

Platform changes may produce different future outcomes, but historical decisions remain auditable.

---

# Command Pattern

Business actions are modeled as commands.

Examples:

- BookAppointment
- CancelAppointment
- RescheduleAppointment
- DispatchTechnician
- RequestReview

Every command executes through the Decision Runtime.

> ❌ **Not true today.** Business actions are not modelled as commands. `BookAppointment` exists
> as `bookings.book_job`; the rest of the examples above have no implementation.

---

# Responsibilities

## AI Runtime

- Understand intent
- Generate proposal
- Clarify ambiguity

## Decision Runtime

- Orchestrate lifecycle
- Coordinate services
- Execute decisions

## Platform Services

- Supply facts
- Enforce rules
- Persist state

---

# Anti-Patterns

Never:

- Execute directly from LLM output.
- Skip policy evaluation.
- Skip authority checks.
- Mutate workflow outside the Decision Runtime.
- Ignore failed execution outcomes.

---

# Success Criteria

A correct decision is:

- Safe
- Deterministic
- Auditable
- Recoverable
- Replaceable
- Independent of any specific AI model
