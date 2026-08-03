# 001 – PLATFORM ARCHITECTURE

**Version:** 0.2 (Draft)  
**Status:** Target Architecture — **not fully implemented**  
**Owner:** Founding Team

---

> ## ⚠ Implementation status
>
> **This document describes the target architecture, not the current codebase.** Statements below
> are written in the present tense throughout ("Owns…", "Handles…", "must always be true"). Read
> them as *what will be true*, except where a section carries its own status marker.
>
> Audited 2026-08-01 against `agent/` — see
> [`GAP-ANALYSIS-2026-08-01.md`](GAP-ANALYSIS-2026-08-01.md) for the evidence behind every entry.
>
> | Layer / service | Status | Note |
> |---|---|---|
> | Layer 1 Applications | ✅ Built | Frontdesk, Quote Chaser, Reviews, Referral, Lead Qualifier, Dispatcher |
> | Layer 2 Business Operations | 🟡 Partial | Booking and escalation exist as shared operations; the rest are per-employee |
> | **Layer 3 Decision Runtime** | ❌ **Not implemented** | The lifecycle is re-implemented in four handlers; no orchestrator exists |
> | Identity Platform | ✅ Mostly built | `Business`/`Customer` correct; `Contact`/`Property`/`Equipment` absent (deliberately deferred) |
> | Knowledge Platform | ❌ Not implemented | One free-text `pricing_faq` column; `BusinessMemory` is a port with no adapter |
> | **Policy Engine** | ❌ **Not implemented** | Deterministic engines exist for two axes; hours/pricing/service-area are enforced only inside LLM prompts |
> | **Authority Engine** | 🟡 Deployment-level only | `runner.is_active`/`dispatch_tick` gate *whether an employee runs*; no per-action authority |
> | Workflow Engine | 🟡 Built per-employee | Real state machine + claim/retry/compensation in Recovery; not generalised; no `Workflow` entity |
> | Event Platform | 🟡 Built, no publishers | Table, bus, dedup and 14 event types exist; **nothing publishes** in production code |
> | Communication Platform | ✅ Built | Port + adapters, no business logic. The most conformant component |
> | Observability Platform | 🟡 Partial | `CallTrace` is thorough for voice; `print()` elsewhere |
>
> Legend: ✅ built · 🟡 partial · ❌ not implemented

---

# Purpose

This document defines the long-term architecture of the Roster platform.

The Constitution explains **how we think**.

This document explains **how we build**.

It defines the permanent architectural layers, platform services, ownership boundaries, and interaction model. Every new feature, AI employee, and integration must fit into this architecture.

---

# Design Goals

The platform is designed to optimize for:

1. Trust over automation
2. Deterministic business execution
3. Replaceable AI models
4. Explainable decisions
5. Recoverable workflows
6. Scalable architecture
7. Strong ownership boundaries

---

# Platform Philosophy

**The Platform is the product.**

AI Employees (Frontdesk, Reviews, Chaser, Renewals, etc.) are applications built on top of the platform.

Business logic belongs to the platform—not to individual AI employees.

---

# Layered Architecture

```
Applications
    ↓
Business Operations
    ↓
Decision Runtime
    ↓
Platform Services
    ↓
Infrastructure
```

Each layer has a single responsibility and communicates only through well-defined contracts.

---

# Layer 1 – Applications

Examples:

- Frontdesk
- Reviews
- Chaser
- Renewals
- Dispatcher

Responsibilities:

- Customer interaction
- Collect information
- Invoke business operations
- Present results

Must NEVER own:

- Pricing rules
- Workflow state
- Business policies
- Permissions

---

# Layer 2 – Business Operations

Business Operations represent reusable capabilities.

Examples:

- Book Appointment
- Cancel Appointment
- Reschedule Appointment
- Dispatch Technician
- Request Review
- Membership Renewal

Each operation should be reusable by any AI employee.

---

# Layer 3 – Decision Runtime

> ❌ **Not implemented.** No Decision Runtime exists. The lifecycle described here is currently
> re-implemented in four independent handlers (`service.handle_customer_message`,
> `recovery_service.handle_recovery_reply`, `review_service.handle_review_reply`,
> `referral_service.handle_referral_reply`) plus `xai_voice_adapter._handle_function_call`. They
> agree by discipline, not by construction. Planned as D2 in
> [`IMPLEMENTATION-PLAN-001.md`](IMPLEMENTATION-PLAN-001.md).

The Decision Runtime is the heart of the platform.

Responsibilities:

- Interpret intent
- Gather evidence
- Request AI reasoning
- Validate facts
- Evaluate policies
- Evaluate authority
- Execute approved actions
- Record outcomes

No important business action bypasses the Decision Runtime.

---

# Layer 4 – Platform Services

## Identity Platform

> ✅ **Mostly built.** `Business` and `Customer` are correct, including the hard case:
> identity resolves on the caller's phone number, never on a conversation thread
> (`xai_voice_adapter._persist_job`). `Contact`, `Property` and `Equipment` do not exist and are
> deliberately deferred — no customer needs them yet.

Owns:

- Businesses
- Customers
- Contacts
- Properties
- Equipment

Never owns:

- Conversations
- Workflow state

---

## Knowledge Platform

> ❌ **Not implemented.** The entire knowledge store is one free-text `Business.pricing_faq`
> column. `memory.BusinessMemory` is a defined port with no adapter and no production callers.
> No FAQ entity, no documents, no retrieval, no versioning, no provenance.

Owns:

- FAQs
- Company knowledge
- Manuals
- Retrieval
- Versioning
- Provenance

Never owns:

- Policies
- Customer identity

---

## Policy Engine

> ❌ **Not implemented, and currently violated.** `hours`, `pricing_faq` and `service_area` are
> enforced *only* by interpolating them into the LLM system prompt (`engine.py:259-262` for SMS,
> `:295-298` for voice). `engine._service_area_note` generates the instruction *"If they're outside
> this area … don't book the job"* — no code checks it. Nothing is versioned.
>
> Deterministic rule engines of the right shape do exist for two other axes
> (`lead_qualifier_engine.py`, `dispatcher_engine.py`) and are the pattern to extend.

Owns deterministic business rules:

- Hours
- Pricing
- Memberships
- Service areas
- Emergency policies

Policies are versioned and never embedded in prompts.

---

## Authority Engine

> 🟡 **Deployment-level only.** `runner.is_active` and `runner.dispatch_tick` decide *whether an
> employee runs for a business at all*, enforced structurally at a single chokepoint. There is no
> per-action authority: a deployed Frontdesk may book anything, at any urgency, at any hour.
> `Employee.policy_json` exists on the table and is written by nothing. "Human approval required"
> is not a reachable state.

Determines whether a proposed action is permitted.

Examples:

- Can AI book?
- Can AI cancel?
- Is human approval required?

---

## Workflow Engine

> 🟡 **Built per-employee, not generalised.** `RecoveryJob` is a real workflow instance with a
> state machine, and `recovery_service.tick` implements claim-before-send with a compensating
> release on failure. Reviews' workflow state is timestamp columns on `Job`; Frontdesk has none.
> No generic `Workflow` entity exists. Known gap: a `RecoveryJob` in `awaiting_slot` never expires.

Owns:

- Workflow state
- Retries
- Recovery
- Long-running processes

Workflow state never belongs to conversations.

---

## Event Platform

> 🟡 **Built, but nothing publishes.** The `Event` table, `EventBus` with dedup, and 14 named
> event types all exist and are tested. Across all non-test code there is **not one call to
> `bus.publish`** — the blocker is an import-time engine binding in `eventbus.py`. Being fixed as
> PR 1 + PR 2 of [`IMPLEMENTATION-PLAN-001.md`](IMPLEMENTATION-PLAN-001.md).

Every significant action generates an immutable event.

Examples:

- BookingRequested
- BookingConfirmed
- AppointmentCancelled
- ReviewRequested

Events are append-only.

---

## Communication Platform

> ✅ **Built and conformant.** `channels.py` is a Protocol with Twilio and Console adapters;
> `xai_voice_adapter.py` owns the voice session; `provisioning.py` owns numbers. Business logic
> genuinely does not live in any of them.

Handles:

- Voice
- SMS
- Email
- Future channels

Communication delivers messages but does not make business decisions.

---

## Observability Platform

> 🟡 **Partial.** `CallTrace` gives per-call stage tracing with millisecond offsets, JSONL
> persistence and rotation — for voice only. SMS and tick paths use `print()`.
> `OwnerNotification.delivered` is the only place a failed send is visible. No replay.

Provides:

- Tracing
- Audit logs
- Replay
- Metrics
- Incident investigation

Every important decision must be explainable.

---

# Service Ownership

| Service | Owns | Never Owns |
|---------|------|------------|
| Identity | Business entities | Workflow state |
| Knowledge | Information retrieval | Policies |
| Policy | Business rules | Identity |
| Authority | Permissions | AI reasoning |
| Workflow | Process state | Customer identity |
| Events | History | Current state |
| Communication | Message delivery | Business logic |
| Observability | Diagnostics | Business behavior |

---

# Canonical Data Flow

```
Customer
    ↓
Communication
    ↓
AI Employee
    ↓
Decision Runtime
    ↓
Platform Services
    ↓
Business Operation
    ↓
Event Platform
    ↓
Customer Response
```

---

# Platform Invariants

> 🟡 **Aspirational.** Four of the seven hold today. **"AI never mutates business state directly"**
> holds for `alert_owner` (returned as a proposal) but not for `log_job`, which `engine.respond`
> resolves internally and `service.py` books unconditionally. **"Policies are deterministic"** and
> **"Every business action flows through the Decision Runtime"** are both false — see the Policy
> Engine and Layer 3 markers above.

The following must always be true:

- Identity has one canonical owner.
- Workflow owns process state.
- Events are immutable.
- Policies are deterministic.
- AI never mutates business state directly.
- All important actions are auditable.
- Every business action flows through the Decision Runtime.

---

# Failure Philosophy

Failures are expected.

Every service must define:

- Failure detection
- Retry strategy
- Recovery strategy
- Escalation path
- Observability

Silent failures are unacceptable.

---

# Replaceability

The architecture must support replacing:

- AI model providers
- Voice providers
- Messaging providers
- Databases
- Queues

without changing business logic.

---

# Evolution Rules

Every significant change follows:

1. RFC
2. Architecture review
3. Implementation
4. Tests
5. Rollout
6. Monitoring
7. Merge

---

# Anti-Patterns

Engineers must never:

- Put business rules in prompts.
- Duplicate policy logic.
- Allow direct AI database writes.
- Couple AI employees together.
- Bypass the Decision Runtime.
- Rewrite historical events.

---

# Future Evolution

Future capabilities should extend the platform rather than introducing new architectural layers.

Examples:

- Multi-location support
- Franchise management
- New AI employees
- Additional communication channels

These should reuse the existing architecture rather than creating parallel systems.
