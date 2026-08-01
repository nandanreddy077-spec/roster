# 000 – ROSTER ARCHITECTURE CONSTITUTION (Draft v0.1)

> **Note**
>
> This is the first volume of the architecture handbook. It intentionally focuses on
> immutable engineering principles rather than implementation details.

---

# Purpose

This document defines the engineering constitution of Roster.

Frameworks will change.
AI models will change.
Programming languages will change.
Engineers will change.

This document should change rarely.

If implementation conflicts with this document, the implementation is wrong.

---

# Mission

Roster exists to build the world's most trusted AI workforce for home service businesses.

Trust is the product.

Automation is a consequence of trust.

---

# Vision

Roster is not an AI receptionist.

Roster is an AI workforce platform.

AI employees are applications running on a shared operational platform.

The platform—not any individual AI model—is the product.

---

# Engineering Philosophy

## Truth before Intelligence

Verified facts always take precedence over AI reasoning.

## Trust before Automation

Automation that reduces trust is rejected.

## Authority before Action

AI proposes.
The platform authorizes.
Only then is work executed.

## Recovery before Optimization

Every workflow must define failure and recovery before optimization.

## Simplicity over Cleverness

Choose the simplest architecture that preserves correctness.

---

# The Ten Laws of Roster

> ## ⚠ Compliance status (audited 2026-08-01)
>
> The Laws are permanent and are **not** softened here — this document states that where the
> implementation conflicts, *the implementation is wrong*. That remains true. This note records
> which Laws the codebase does not yet satisfy, so the gap is tracked rather than assumed closed.
> Evidence: [`GAP-ANALYSIS-2026-08-01.md`](GAP-ANALYSIS-2026-08-01.md).
>
> | Law | Status |
> |---|---|
> | 1 Truth before Intelligence | 🟡 Verified facts win where they are checked at all; hours, pricing and service area are not checked |
> | 2 Authority before Action | 🟡 Deployment-level only. No per-action authority exists |
> | 3 Verification before Commitment | 🟡 Ad-hoc per handler; no shared verification step |
> | **4 Policies live outside prompts** | ❌ **Violated.** `engine.py:259-262` and `:295-298` interpolate hours, pricing and service area directly into the LLM system prompt, and `_service_area_note` delegates enforcement of the service-area rule to the model |
> | 5 Every important action is observable | 🟡 Owner alerts and voice calls are logged; the `Event` table has no publishers |
> | 6 Humans retain ultimate authority | ✅ Escalation to the owner exists on every path; no autonomous irreversible action |
> | 7 Failures must be recoverable | 🟡 Strong in Recovery (claim/release retry); inconsistent elsewhere |
> | 8 History is immutable | ✅ No update path on any log or event row |
> | 9 Architecture outlives AI models | ✅ Provider adapters are ports; model choice is one constant |
> | 10 Trust is the North Star | — Not mechanically checkable |
>
> Law 4 is the largest open violation and is the highest-value fix in the gap analysis's
> recommended order.

1. Truth before Intelligence.
2. Authority before Action.
3. Verification before Commitment.
4. Policies live outside prompts.
5. Every important action is observable.
6. Humans retain ultimate authority.
7. Failures must be recoverable.
8. History is immutable.
9. Architecture outlives AI models.
10. Trust is the North Star.

---

# AI Constitution

AI may:

- Understand language
- Extract information
- Reason
- Summarize
- Recommend
- Select tools

AI must never own:

- Customer identity
- Business truth
- Pricing rules
- Permissions
- Workflow state
- Financial correctness
- Audit history

---

# Engineering Rules

Never:

- Store business rules inside prompts.
- Duplicate business logic.
- Allow AI to directly mutate business state.
- Hide failures.
- Invent missing facts.

Always:

- Log important decisions.
- Validate before execution.
- Design rollback paths.
- Write regression tests.

---

# Definition of Done

A feature is complete only when it is:

- Correct
- Observable
- Recoverable
- Tested
- Documented
- Explainable

---

# Success Criteria

Customers should trust outcomes, not notice the AI.

When customers say:

> "This company is incredibly organized."

Roster has succeeded.

---

# Next Documents

- 001 – P0 Platform Architecture
- 002 – Decision Model
- 003 – Domain Model

This document intentionally avoids implementation details. Those belong in the following architecture volumes.
