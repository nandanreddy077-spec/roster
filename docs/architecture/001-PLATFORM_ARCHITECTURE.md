# 001 – PLATFORM ARCHITECTURE

**Version:** 0.1 (Draft)  
**Status:** Target Architecture  
**Owner:** Founding Team

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

Owns deterministic business rules:

- Hours
- Pricing
- Memberships
- Service areas
- Emergency policies

Policies are versioned and never embedded in prompts.

---

## Authority Engine

Determines whether a proposed action is permitted.

Examples:

- Can AI book?
- Can AI cancel?
- Is human approval required?

---

## Workflow Engine

Owns:

- Workflow state
- Retries
- Recovery
- Long-running processes

Workflow state never belongs to conversations.

---

## Event Platform

Every significant action generates an immutable event.

Examples:

- BookingRequested
- BookingConfirmed
- AppointmentCancelled
- ReviewRequested

Events are append-only.

---

## Communication Platform

Handles:

- Voice
- SMS
- Email
- Future channels

Communication delivers messages but does not make business decisions.

---

## Observability Platform

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
