# Roster Roadmap

**Mission:** make a home-service business owner believe they **hired an office
employee**, not installed software. Every feature, screen, notification,
onboarding step, and workflow reinforces that.

**The filter — build only if it clearly improves one of:**
1. **Trust** — the owner believes it's working.
2. **Revenue** — it books or recovers real jobs (dollars).
3. **Employee experience** — it feels like managing staff, not configuring software.

**The gate:** if a proposed feature doesn't clearly improve one of the three,
it goes in the "Deferred" table below, not the codebase. **Assume Avoca can
match any feature within a year — so we never justify a build by "a competitor
has it," only by "it helps a customer adopt, trust, or expand Roster."**

Grounding docs: [`SALES.md`](SALES.md) (does it close deals?),
[`CUSTOMER.md`](CUSTOMER.md) (what real customers actually said),
architecture PRD in `docs/superpowers/specs/`.

---

## Phases as business milestones

### Phase A — Foundation ✅ *(done)*
Business · Customer · Employee models · shared Memory · EventBus · 217 tests ·
merged to `main` (PR #1). The architecture is frozen — no more platform work
without a real customer forcing it.

### Phase B — Trust *(current)*
Goal: an owner watches it work and believes it. Nothing here adds new revenue
mechanics — it makes the existing product *trustable* and *feel like employees*.
- **M1 — Owner SMS** *(complete, PR #2)* — booked job → owner gets a text in seconds.

> ## ⛔ BUILD FREEZE after M1 (founder directive, 2026-07-13)
> **Do NOT start M2 or anything below it until BOTH gates clear:**
> 1. **Frontdesk is proven end-to-end** — a real call/text is *answered*,
>    *books* a job, and *notifies* the owner. (Currently NOT met — no real
>    call has been verified. See `CUSTOMER.md` Open Risk #1.)
> 2. **≥3 real customer demos observed AND founder gives explicit approval** to proceed.
>
> If gate 1 fails, the only work is fixing Frontdesk — not new features. A fresh
> session must honor this freeze and confirm both gates with the founder before
> writing any M2+ code.

- **M2 — AI Office dashboard** *(frozen — see gate above)* — reframe from a feature list to an office of
  named employees showing today's activity ("👩 Frontdesk — 19 calls, 7 booked, Working").
- **M3 — Employee cards** — per-employee business metrics (jobs, $ booked/recovered); never AI metrics.
- **M4 — Better onboarding & activation** — cross the call-forwarding cliff; "teach your hire," don't configure software. Hire / Pause / Fire (the `Employee.status` field already exists).
- **Then: stop and sell.** First 20 via concierge onboarding calls; friction logged in `CUSTOMER.md` decides what to automate.

### Phase C — Revenue *(agents already built — this phase turns them on)*
The recovery employees already exist in code (Quote Chaser, Rebooker, Renewals,
Reviews). Phase C is **activating them per real customer** and making the money
visible — not building them from scratch. Comes *after* trust: an owner who
doesn't yet trust Frontdesk won't handoff their customer list to Quote Chaser.
- Turn each recovery agent on for pilots who have the data it needs.
- Surface **$ recovered** on the employee cards (M3) so the ROI is undeniable.

### Phase D — Integrations *(only when a paying customer asks)*
When a pilot says *"can this connect to Jobber / Housecall Pro / my calendar?"* —
build **that one**. Never before. Owner-SMS (M1) is the cheap interim "the job
lands where I look." The `Integration` port is defined; zero adapters until demand.

### Phase E — Scale *(upmarket, later)*
Enterprise: permissions, multi-seat, multi-location rollup, SLAs, marketplace.
Only once the SMB motion is proven and a real upmarket customer pulls us there.
The foundation supports it; we do not build it now.

---

## Deferred — not required for the first 20 (do NOT build until a real customer forces it)

| Item | Phase | Why it waits |
|---|---|---|
| CRM/calendar integrations (Jobber, Housecall Pro, ServiceTitan, Google Cal) | D | Build the first only when a paying pilot names their tool. |
| Real scheduling / capacity awareness | D | "Someone will come" + a callback covers it until owners ask. |
| Web chat widget | C/D | Live voice is the wedge, not website chat. |
| Coach / call QA / scoring | E | Enterprise-tail — the ICP has no CSRs to coach. |
| Deep analytics / marketing ROAS / multi-location | E | Enterprise-tail. Employee cards (M3) give owners the only numbers they think in. |
| SOC 2 / security certs | E | Enterprise trust, earned over time. |
| Email / WhatsApp channels | D | SMS + voice is enough for the wedge. |
| EventBus producer/subscriber wiring + Runner | — | Platform. The seam is built; wire it only when one event needs *multiple* subscribers. |
| `message`→`interaction` rename (+ channel/direction) | — | Platform cleanup; only when those fields are used. |
| Auto-research onboarding pre-fill | B/D | Nice "it already knows my business" moment; not required to get paid. |

---

## Guardrail
If it isn't on the current phase's list, the default answer is **"add it to this
table, don't build it"** — unless a real pilot or paying customer forces it.
Competitor parity is never a reason on its own.

---

## Employee registry discipline (founder, 2026-07-21)
`agent/employees.py`'s `internal` status is not a resting state. Every
quarter, review each `internal` entry and move it to `live` (repeatable,
customer-ready) or back to `planned` (not valuable enough to keep
half-finished). No employee stays "internal" indefinitely — that's how a
roadmap quietly turns into ten half-finished agents.

**What decides the next employee to build is customer conversations, not
this list or intuition.** Track the recurring bottleneck pattern in
`CUSTOMER.md`'s onboarding-call log (its "Biggest bottleneck identified"
field). If 15 of 20 discovery calls surface unclosed estimates, Quote
Chaser graduates next — not because it's next in `employees.py`.
