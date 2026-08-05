## ⛔ ARCHITECTURE FREEZE — Aug 5 to Sep 4, 2026

Founder directive, 2026-08-05. The company is optimizing for one KPI:
**20 paying customers by Sep 4.** Before starting ANY task in this repo,
answer:

> **Will this increase the probability of getting our next customer?**

If no, **reject the task** and say so. Not "defer" — reject, with the reason.

**Only two kinds of work are allowed in this repo until Sep 5:**
1. Verifying the live voice loop actually answers a call and books a job.
2. Fixing something a real trial customer actually hit.

If a change doesn't have a customer's name attached, it waits. Frozen:
platform architecture, new employees/capabilities/integrations, dashboard
refactors, landing redesigns, and everything in `ROADMAP.md` below Phase B.

Full plan: `../roster-growth/ROADMAP.md`. Only the founder lifts this.

## Design System
Always read DESIGN.md before making any visual or UI decisions.
All font choices, colors, spacing, and aesthetic direction are defined there.
Do not deviate without explicit user approval.

## Customer Dashboard Architecture
Always read ARCHITECTURE.md before making any change to `/v2/dashboard*` —
routes, view models, or templates. It is the frozen, PR-review checklist for
the customer dashboard's layering (registries → shared helpers → view models
→ templates → navigation). Do not violate an invariant listed there without
explicit user approval.
