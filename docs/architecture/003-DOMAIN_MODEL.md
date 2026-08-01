# 003 – DOMAIN MODEL

Version: 0.2 (Draft)
Status: Target Architecture — partially implemented

> ## ⚠ Implementation status
>
> Audited 2026-08-01 — see [`GAP-ANALYSIS-2026-08-01.md`](GAP-ANALYSIS-2026-08-01.md).
>
> | Entity | Status | Where |
> |---|---|---|
> | Business | ✅ Built | `db_models.Business` — aggregate root, scoping enforced on every query |
> | Customer | ✅ Built | `db_models.Customer`, unique on `(business_id, phone)` |
> | Contact | ❌ Absent | Customer currently *is* the contact; `phone` lives on Customer |
> | Property | ❌ Absent | Addresses exist only as free-text `Job.address` |
> | Equipment | ❌ Absent | No table |
> | Conversation | 🟡 Implicit | `Message` rows threaded by `customer_phone`; no Conversation entity |
> | Appointment | ❌ Absent | `Job` carries a *stated preference* (`preferred_window`), never a booked slot — nothing checks technician availability |
> | Job | ✅ Built | `db_models.Job` |
> | Workflow | 🟡 Per-employee | `RecoveryJob` is one; Reviews uses timestamps on `Job`; no generic entity |
> | Decision | ❌ Absent | Created as a post-hoc audit record by D1 of [`IMPLEMENTATION-PLAN-001.md`](IMPLEMENTATION-PLAN-001.md) |
> | Policy | ❌ Absent | Policy data lives as free-text columns on `Business`, enforced only inside prompts |
> | Event | 🟡 Table built, unused | `db_models.Event` + `EventBus` exist; nothing publishes |
>
> **Ownership below is target state.** Three of the seven owners named — Decision Runtime, Policy
> Engine, Workflow Engine (as a generic service) — do not exist yet.
>
> Canonical rules that **already hold**: #1 (identity independent of communication — enforced in
> the voice path, which keys the Customer on the caller's number and never on the call thread),
> #2 (conversations never become truth), #3 (events append-only, by construction), #5 (every
> existing entity has exactly one owner).
>
> Canonical rule **not yet enforced**: #4 (workflow owns process state) holds for Recovery only.

Purpose
The Domain Model defines the canonical business entities of Roster.

Core Entities:
- Business
- Customer
- Contact
- Property
- Equipment
- Conversation
- Appointment
- Job
- Workflow
- Decision
- Policy
- Event

Key Principles:
- Business owns customers.
- Customer owns relationships, not phone numbers.
- Properties are independent from customers.
- Conversations never become business truth.
- Workflows own state.
- Events are immutable.
- Policies are versioned.
- AI owns none of the business entities; it reasons over them.

Ownership:
Business, Customer, Contact, Property, Equipment -> Identity
Conversation -> Communication
Appointment, Job, Workflow -> Workflow Engine
Decision -> Decision Runtime
Policy -> Policy Engine
Event -> Event Platform

Canonical Rules:
1. Identity exists independently of communication.
2. Conversations never become truth.
3. Events are append-only.
4. Workflow owns process state.
5. Every entity has exactly one owner.
6. Future features extend existing entities rather than creating duplicates.
