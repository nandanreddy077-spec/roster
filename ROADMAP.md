# Roster Roadmap

**Phase:** Product Validation (architecture is complete and frozen — see
`docs/superpowers/specs/2026-07-13-roster-platform-architecture-prd.md`).

**Mission:** maximize the probability that a home-service business *pays* for
Roster. Not to make the architecture better. Not feature parity with Avoca.

**Every feature must strengthen at least one of:**
1. **Trust** — the owner believes it's working.
2. **Revenue** — it books or recovers jobs (real dollars).
3. **Employee mental model** — it feels like they *hired staff*, not installed software.

**The gate:** before building anything, answer *"is this required before our
first 20 paying customers?"* If no → it lives here in the roadmap, not in the
codebase. **We do not build enterprise features because Avoca has them. We
build customer value.**

---

## Now — Phase 2 milestones (build in order, one at a time, then stop and sell)

### M1 — Owner SMS  *(building)*
When Frontdesk books a job, the owner gets a text within seconds.
- **Why required before 20:** the ICP owner won't open a dashboard. A text in
  their pocket is the only proof-of-work that reaches them → **trust**. And
  *"Frontdesk booked AC Repair"* makes the **employee model** physical.
- **Done when:** a booked job (SMS or voice) texts `escalation_phone` within
  seconds, containing customer, service, urgency, callback number, and the
  employee name.

### M2 — AI Office dashboard
Reframe the dashboard from a list of features to an **office of employees**
(🏢 AI Office → Employees → ✓ Frontdesk, ✓ Quote Chaser, …). Not a redesign —
just make the staffing metaphor obvious. **Employee model.** Uses existing
DESIGN.md tokens only.

### M3 — Employee scorecards
Per-employee card in **business metrics only** — calls, jobs, **$ booked** /
**$ recovered**. Never AI metrics (tokens, latency, accuracy). **Trust +
revenue + employee model.** Built *after* a pilot books real jobs, so it shows
real numbers, not zeros.

### M4 — Hire / Pause / Fire
Owner can pause or fire an employee from the dashboard. The `Employee.status`
field (active/paused/fired) already exists from the foundation; this is the UI +
the Runner honoring it. **Employee model** — you manage staff, you don't
configure software.

**Then: STOP building. Go sell.** The first 20 come from concierge onboarding
calls (self-serve signup + a 15-min founder call per pilot), and the friction
logged on those calls decides what gets automated next — not a guess made here.

---

## Deferred — not required for the first 20 (do NOT build until a real customer forces it)

| Item | Why it waits |
|---|---|
| **CRM/calendar integrations** (ServiceTitan, Housecall Pro, Jobber, Google Cal) | Build the *first one* only when a paying pilot names the tool they actually use. Owner-SMS (M1) is the cheap interim "job lands where they look." Never guess integrations. |
| **Real scheduling / capacity awareness** | Books "someone will come" today; a real slot-picker matters only once owners ask. M1 + a callback covers the gap. |
| **Web chat widget** | A different channel; live voice is the wedge, not website chat. |
| **Coach / call QA / scoring** | Enterprise-tail — a solo/5-truck shop has no CSRs to coach. Avoca's customer, not ours. |
| **Deep analytics / marketing ROAS / multi-location rollup** | Enterprise-tail. M3 scorecards give owners the only numbers they think in. |
| **SOC 2 / security certifications** | Trust for enterprise buyers; earned over time, irrelevant to the first 20. |
| **Email / WhatsApp channels** | SMS + voice is enough for the wedge. |
| **EventBus producer/subscriber wiring + the Runner** | Platform work. The seam is built + tested; wire it only when a single event needs *multiple* subscribers. M1 uses a direct call, not the bus, on purpose. |
| **`message` → `interaction` rename (+ channel/direction)** | Platform cleanup; do it only when those fields are actually used. |
| **Auto-research onboarding pre-fill** (scrape site/GBP) | A nice "it already knows my business" moment, but not required to get paid. |

---

## Guardrail

If a proposed feature isn't on the "Now" list above, the default answer is
**"add it here, don't build it"** — unless a real pilot or paying customer
forces it. Competitor parity is never a reason on its own.
