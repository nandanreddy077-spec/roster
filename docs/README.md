# Documentation

Start with the root [`../README.md`](../README.md) for the project overview
and how to run it. This folder is deeper reference material.

| Doc | Covers |
|---|---|
| [architecture.md](architecture.md) | How a request actually flows through the system today, core files and their responsibilities |
| [api.md](api.md) | Every route in `app.py` and `portal.py` |
| [development.md](development.md) | Local setup, env vars, project layout |
| [deployment.md](deployment.md) | Railway deployment summary (full detail in `agent/README.md`) |
| [testing.md](testing.md) | Running and organizing the pytest suite |
| [coding-guidelines.md](coding-guidelines.md) | Conventions already in use — one idempotent write path per side effect, registries-as-code, fail-closed secrets, naming |
| [future-refactors.md](future-refactors.md) | Findings from the 2026-07-30 hygiene pass that were deliberately not implemented, and why |
| [review-panel.md](review-panel.md) | The 7-role structured review process used before building anything non-trivial |

## Decision history

[`superpowers/specs/`](superpowers/specs/) and
[`superpowers/plans/`](superpowers/plans/) hold every feature's design spec
and implementation plan, paired by date and topic
(`YYYY-MM-DD-<topic>[-design].md`). This is the project's decision log —
check there for *why* something was built the way it is before assuming.
Some entries describe designs later replaced; known cases are listed in
[future-refactors.md](future-refactors.md#3-stale-specs-plans-without-a-superseded-marker).

[`research/`](research/) holds standalone market/competitor research that
informed decisions but isn't itself a build spec.

## Root-level docs (not in this folder, by design)

These live at the repo root because they're read constantly by whoever is
building or selling, not occasional reference:

- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — frozen customer-dashboard
  invariants, required reading before touching `/v2/dashboard*`
- [`../DESIGN.md`](../DESIGN.md) — design tokens and decisions, required
  reading before any visual change
- [`../ROADMAP.md`](../ROADMAP.md), [`../SALES.md`](../SALES.md),
  [`../CUSTOMER.md`](../CUSTOMER.md), [`../SPRINT-10-CUSTOMERS.md`](../SPRINT-10-CUSTOMERS.md) —
  current build/sales priorities and the active customer-acquisition sprint
