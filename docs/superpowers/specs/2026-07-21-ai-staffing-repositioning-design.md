# AI Staffing Repositioning — Immediate Changes

**Date:** 2026-07-21
**Status:** ⚠️ **SUPERSEDED 2026-07-28** by the department-first pivot —
`docs/superpowers/specs/2026-07-28-departments-product-blueprint-design.md`
(canonical product source of truth). **Kept as the historical record, not as
guidance.** Its §4 decision — *"the registry is employee-first, with
`department` as a descriptive tag, not a structural entity"* — is exactly what
the 2026-07-28 pivot reverses: departments are now the structural,
customer-facing unit and employees are an implementation detail inside them.
Its §2/§3 direction of travel (request-led onboarding as the standing default,
hiring as a reviewed conversation rather than a self-serve purchase) was
*correct* and is carried forward and hardened — self-serve is now retired
outright, with no founder-only exception.
**Original status:** Approved by founder, pending spec review
**Supersedes (partially):** the "REVERT to self-serve" note in `DESIGN.md`'s 2026-07-16 decision log entry

## Context

The founder pasted a long-term architecture document ("Roster Platform Refactor
— From AI Receptionist to AI Staffing Company") describing a full Office →
Departments → Employees platform with 7 departments and ~20 employee roles.
That document is the **long-term company architecture**, not a build order —
it says so itself ("DO NOT BUILD ALL OF THESE").

This is currently day 5 of the [[customer-sprint-30-days]]
(`SPRINT-10-CUSTOMERS.md`, Jul 17–Aug 15 2026), which explicitly bans feature
work and repositioning (§8). The founder confirmed, in chat, an explicit
exception: the specific changes below are treated as customer-acquisition
work, not general feature work, because they directly serve the "get 10 paying
customers" goal — not a reopening of the [[platform-architecture-locked]]
freeze or the M1 build gate. Nothing here revisits the 7 constitutional
principles; `Business` remains the aggregate root, `Employee` remains the
generic declarative binding.

Investigation found more of the target state already exists than the pasted
doc assumed: the landing page already frames Roster as a staffing company that
hires AI employees (not "AI receptionist software"), self-serve `/signup` is
already unlinked from the landing (since the 2026-07-16 KYC-driven pivot to a
request-access funnel), and the dashboard already has a data-driven "next
hire" recommendation (`roles.py: next_hire()`) with a backend that just queues
a lead for founder follow-up. The real gap is narrower than the pasted doc
implies.

## Scope

**In scope (this pass):**
1. Make the request-access-only funnel the standing default, not a KYC
   workaround with a planned reversion or a decision tied to a fixed
   customer count.
2. Reframe the dashboard's existing self-serve "Hire" action as a
   founder-reviewed hiring conversation, not a feature purchase — no backend
   change.
3. Add a code-level employee registry so future roles have a place to slot in
   without another redesign — no new table, no new UI, no new routes.
4. Write down the **Customer Onboarding Principle**: diagnosis before
   deployment. Record it here and in `SALES.md`.

**Explicit non-goals:**
- No new employees implemented (Quote Chaser and Retention Manager already
  exist as engines — `recovery_engine.py`, `referral_engine.py` — everything
  else in the pasted doc's 7 departments stays undocumented-in-code until a
  real customer need forces it, per [[platform-architecture-locked]]).
- No employee catalog/marketplace surfaced to customers.
- No schema changes or migrations.
- No changes to voice, SMS, booking, webhooks, or existing integrations.
- No large refactor. Every commit is small and independently revertable.

## 1. Positioning copy

No changes. `landing/index.html`, `SALES.md`, and `ROSTER.md` already describe
Roster as an AI staffing company that customers hire employees from, not "AI
receptionist software." (`SALES.md` line 14: *"Roster is the AI staffing
company for home-service businesses... Not 'an AI receptionist.'"*) The one
stale line is `SALES.md`'s honesty anchor (§5 below) — fixed as part of that
edit, not a standalone copy pass.

## 2. Self-serve signup gate becomes the standing default

`DESIGN.md`'s 2026-07-16 log entry unlinked `/signup` from the landing because
Twilio KYC blocked per-customer number provisioning, with an explicit note:
*"REVERT to self-serve 'Hire your AI employee' CTAs once Twilio provisions
numbers."* That reversion plan is cancelled. Request-led onboarding
(`AccessRequest` → founder follow-up → founder walks the owner through
`/signup` live, or `/clients/new`) stays the default funnel — not tied to
KYC status, and not tied to a fixed customer count either. Founder-led
onboarding remains the default until Roster has validated a repeatable
onboarding and deployment process; the transition to self-serve is driven
by customer-success metrics (e.g. onboarding time, activation rate,
first-week retention), not by hitting an arbitrary number of customers.

Changes:
- `DESIGN.md` decision log: add a 2026-07-21 entry recording this as a
  deliberate standing default (metrics-gated, not count-gated or
  KYC-gated), referencing this spec.
- `templates/login.html`: remove the "Haven't hired your team yet? Get
  started" link to `/signup` — the last discoverable self-serve entry point
  on a live page. `/signup` itself stays working (used directly by the
  founder during/after a discovery call); it's just no longer advertised.

## 3. Dashboard: recommendation becomes a hiring conversation

Current state (`templates/dashboard.html:129-147`, `portal.py:387-399`): the
"Your office" card shows Quote Chaser under a "Ready to hire" label with a
dynamic reason line ("Recommended because you've already booked N jobs — the
estimates are piling up") and a "Hire" button that POSTs to `/roster/hire`.
The backend already just appends to `Business.requested_roster` for the
founder to manually configure — it does not self-provision anything. That
backend behavior is exactly right for "internal sales tool" and is unchanged.

Copy-only changes to `templates/dashboard.html`:
- Section label: "Ready to hire" → **"Opportunity detected"**
- Keep the existing dynamic line (it's concrete and specific — matches
  `DESIGN.md`'s "real dollar math, not vague claims" principle) and add one
  line under it: *"There may be another AI employee that can help — we
  review your business before deploying one."*
- Button label: "Hire" → **"Discuss Your Next AI Hire"**
- Post-request badge: "Setting up" → **"We'll be in touch"**

No route, form field, or backend logic changes. `test_portal_dashboard.py`
and `test_roster_hire_*` tests assert on `Quote Chaser` text and POST
behavior, not button copy — no test changes required, verified by reading the
test file.

## 4. Employee registry (not a department org chart)

Per founder correction: departments may be reorganized later; the employees
Roster commits to are what matters. The registry is employee-first, with
`department` as a descriptive tag, not a structural table.

New file `agent/employees.py` (sits next to `roles.py`, does not replace it —
`roles.py` keeps the trade-display-name and hire-sequencing logic that's
actually wired into the running product):

```python
from dataclasses import dataclass
from typing import Literal

Status = Literal["live", "internal", "planned"]
# live     -> a customer can be hired into this employee today, self-serve or
#             founder-configured, no bespoke engineering per customer.
# internal -> the engine exists and the founder can manually deploy it for a
#             customer on request; not yet a standing dashboard offer.
# planned  -> vision only. No engine, no route, no UI.

@dataclass(frozen=True)
class EmployeeDefinition:
    key: str
    department: str
    status: Status
    display_name: str

REGISTRY: list[EmployeeDefinition] = [
    # Customer Service
    EmployeeDefinition("frontdesk", "customer_service", "live", "Frontdesk"),
    EmployeeDefinition("support", "customer_service", "planned", "Support"),
    # Sales
    EmployeeDefinition("lead_qualifier", "sales", "planned", "Lead Qualifier"),
    EmployeeDefinition("quote_chaser", "sales", "internal", "Quote Chaser"),
    EmployeeDefinition("membership_agent", "sales", "planned", "Membership Agent"),
    EmployeeDefinition("upsell_agent", "sales", "planned", "Upsell Agent"),
    # Operations
    EmployeeDefinition("dispatcher", "operations", "planned", "Dispatcher"),
    EmployeeDefinition("route_optimizer", "operations", "planned", "Route Optimizer"),
    EmployeeDefinition("emergency_coordinator", "operations", "planned", "Emergency Coordinator"),
    # Finance
    EmployeeDefinition("collections", "finance", "planned", "Collections"),
    EmployeeDefinition("financing", "finance", "planned", "Financing"),
    # Customer Success — "Retention Manager" is Roster's existing consolidated
    # name for what the founder's doc splits into Rebooker + Retention (and
    # Reviews, listed under Customer Service in the doc); roles.py already
    # ships this as one employee, so the registry follows the shipped shape
    # rather than the doc's finer split.
    EmployeeDefinition("retention_manager", "customer_success", "internal", "Retention Manager"),
    # Marketing
    EmployeeDefinition("reactivation", "marketing", "planned", "Reactivation"),
    EmployeeDefinition("referral", "marketing", "planned", "Referral"),
    EmployeeDefinition("campaign_manager", "marketing", "planned", "Campaign Manager"),
    # Intelligence
    EmployeeDefinition("business_analyst", "intelligence", "planned", "Business Analyst"),
    EmployeeDefinition("operations_manager", "intelligence", "planned", "Operations Manager"),
]
```

This mirrors the PRD's existing pattern for `RoleDefinition` (§6: *"code
registry, NOT a table... the template an Employee instantiates"*) — same
shape, so it composes with the platform-architecture-locked plan instead of
introducing a second data model. **The registry defines the long-term
capability map of the company. Inclusion in the registry does not imply
implementation, availability, or customer visibility** — `internal` and
`planned` entries are inert data: no route references them, no template
renders them, nothing imports them except (later) documentation tooling and,
eventually, the real `RoleDefinition` registry when a role actually ships.

Note: `quote_chaser` and `retention_manager` are `internal`, not `live` —
their engines exist (`recovery_engine.py`, `referral_engine.py`) and you can
deploy either manually today, but neither is a standing self-serve or
standing dashboard offer yet. `live` in this registry specifically means "a
customer can be hired into this without bespoke founder engineering," which
is only true for Frontdesk.

PRD update: add a short §6a to
`docs/superpowers/specs/2026-07-13-roster-platform-architecture-prd.md`
pointing to `employees.py` as where the long-term roster is enumerated,
one sentence, no restructuring of the existing PRD content.

## 5. Customer Onboarding Principle (diagnosis before deployment)

New governing rule, recorded here and in `SALES.md`:

> While founder-led onboarding is the default (§2), Roster does not assume
> every customer starts with the same AI employee. Every customer begins
> with a business discovery session. We identify the customer's biggest
> operational bottleneck, estimate where the highest ROI exists, and
> recommend the AI employee most likely to solve that problem. We deploy
> that employee, measure results, and use those results to guide future AI
> hires. Today, Frontdesk is the only `live` employee (§4), so it is usually
> the answer — but the onboarding experience is built around diagnosis and
> ROI first, deployment second, not product selection first.

This is the throughline connecting §3 and §4: the dashboard's "opportunity
detected" card *is* the ongoing, post-hire version of this diagnosis — it's
already data-driven (real booked-job count), it already routes to founder
review instead of self-service, and the registry in §4 is where the diagnosed
answer eventually gets configured. No new code implements this — it's a
sales/process principle that the existing pieces already embody, written down
so it's not implicit.

`SALES.md` changes:
- Fix the stale honesty-anchor line (§ "What we can honestly claim TODAY"):
  "✅ Self-serve signup + onboarding — real and live" is no longer true post
  the 2026-07-16 pivot and this spec. Replace with the request-access +
  founder-led reality.
- Update "The motion (first 20 = concierge)" — currently opens "Self-serve
  signup, then..." — to open with the discovery/diagnosis framing above,
  replacing "self-serve signup" with "request access."

## 6. Files touched

| File | Change |
|---|---|
| `agent/employees.py` (new) | Employee registry, §4 |
| `agent/tests/test_employees.py` (new) | Registry check, §7 |
| `docs/superpowers/specs/2026-07-13-roster-platform-architecture-prd.md` | +1 short section, pointer to registry |
| `agent/templates/dashboard.html` | Copy only, §3 |
| `agent/templates/login.html` | Remove self-serve link, §2 |
| `DESIGN.md` | New decision-log entry, §2 |
| `SALES.md` | Onboarding principle + honesty-anchor fix, §5 |

No changes to: `db_models.py`, `app.py`, `portal.py`, `roles.py`,
`recovery_engine.py`, `referral_engine.py`, any webhook, or any existing
test file (one new test file is added — see §7).

## 7. Testing

Non-trivial new logic here is limited to the `employees.py` registry
(a data structure, not behavior) — per the project's "leave one check" norm,
add a small `agent/tests/test_employees.py`: asserts exactly one `status ==
"live"` entry and that it's `frontdesk`, asserts all keys are unique. No
other test changes needed (verified existing dashboard/roster-hire tests
assert on role names and POST behavior, not copy — see §3).

## 8. Commit sequence

Four independent, revertable commits:
1. `agent/employees.py` + `test_employees.py` + PRD pointer — pure addition,
   zero behavior change.
2. `templates/dashboard.html` copy reframe (§3).
3. `templates/login.html` self-serve link removal + `DESIGN.md` log entry (§2).
4. `SALES.md` onboarding principle + honesty-anchor fix (§5).

Each commit ships independently; none depends on another landing first.
