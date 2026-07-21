# AI Staffing Repositioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the four changes from `docs/superpowers/specs/2026-07-21-ai-staffing-repositioning-design.md` — a standing (not KYC/count-gated) request-access onboarding default, a founder-reviewed dashboard recommendation instead of a self-serve "Hire" button, a code-only employee registry, and the diagnosis-before-deployment onboarding principle — in four independently revertable commits, lowest-risk first.

**Architecture:** Every change is additive or copy-only. No schema migration, no route removed, no backend logic changed except the registry's own new (currently unused) module. `app.py`, `portal.py`, `roles.py`, `db_models.py`, and every webhook are untouched.

**Tech Stack:** Python 3 / FastAPI / SQLModel / Jinja2 templates (existing `agent/` app), pytest.

## Global Constraints

- No schema changes or migrations (spec §Scope, non-goals).
- No changes to `db_models.py`, `app.py`, `portal.py`, `roles.py`, `recovery_engine.py`, `referral_engine.py`, any webhook route, or any existing test file (spec §6).
- `agent/employees.py` is a new sibling file to `agent/roles.py` — does not replace it (spec §4).
- `Status` type is exactly `Literal["live", "internal", "planned"]` (spec §4, revised).
- Exactly one `REGISTRY` entry has `status == "live"`, and it is `frontdesk`. `quote_chaser` and `retention_manager` are `internal`, not `live` (spec §4).
- Dashboard copy must match spec §3 exactly: label "Opportunity detected", button "Discuss Your Next AI Hire", post-request badge "We'll be in touch".
- Every commit must leave the full pytest suite green — this is a live product with real customers in an active acquisition sprint (`SPRINT-10-CUSTOMERS.md`); nothing here may regress Frontdesk, voice, SMS, booking, or login.
- Run tests from the `agent/` directory (`cd agent && python -m pytest tests/ -q`) — matches the existing `conftest.py`'s flat-import convention (`import db_models`, not `agent.db_models`).

---

### Task 1: Employee registry (code + test)

**Files:**
- Create: `agent/employees.py`
- Test: `agent/tests/test_employees.py`
- Modify: `docs/superpowers/specs/2026-07-13-roster-platform-architecture-prd.md`

**Interfaces:**
- Produces: `agent.employees.REGISTRY: list[EmployeeDefinition]`, `agent.employees.EmployeeDefinition` (fields: `key: str`, `department: str`, `status: Literal["live", "internal", "planned"]`, `display_name: str`). No other task in this plan imports from `employees.py` — it's documentation-as-code, not wired into any route yet (per spec §4's "inclusion does not imply availability" rule).

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_employees.py`:

```python
"""Employee registry is documentation-as-code, not a live subsystem: these
tests only guard its own invariants (exactly one live entry, unique keys).
Nothing else in the app imports this module yet — see spec §4."""
from employees import REGISTRY


def test_exactly_one_live_employee_and_it_is_frontdesk():
    live = [e for e in REGISTRY if e.status == "live"]
    assert len(live) == 1
    assert live[0].key == "frontdesk"


def test_registry_keys_are_unique():
    keys = [e.key for e in REGISTRY]
    assert len(keys) == len(set(keys))


def test_quote_chaser_and_retention_manager_are_internal_not_live():
    by_key = {e.key: e for e in REGISTRY}
    assert by_key["quote_chaser"].status == "internal"
    assert by_key["retention_manager"].status == "internal"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && python -m pytest tests/test_employees.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'employees'`

- [ ] **Step 3: Write the registry**

Create `agent/employees.py`:

```python
"""Employee-first registry of Roster's long-term roster (spec:
docs/superpowers/specs/2026-07-21-ai-staffing-repositioning-design.md §4).

This mirrors the RoleDefinition pattern in the platform architecture PRD
(docs/superpowers/specs/2026-07-13-roster-platform-architecture-prd.md §6):
a code registry, not a table. `department` is a descriptive tag, not a
structural entity — departments may be reorganized later, the employees
Roster commits to are what matters.

Inclusion in this registry does NOT imply implementation, availability, or
customer visibility. Nothing in the running app reads this module yet.
"""
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
    # name for what the long-term vision doc splits into Rebooker + Retention
    # (and Reviews, listed under Customer Service there); roles.py already
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

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent && python -m pytest tests/test_employees.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Add the PRD pointer**

In `docs/superpowers/specs/2026-07-13-roster-platform-architecture-prd.md`, find this exact text (end of §6 Domain Model, right before the `---` that precedes `## 7. Database Schema`):

```
- **Property / Equipment** *(reserved, unimplemented)* — the home and the units in it (furnace, AC, water heater). Reserved because trade work is ultimately *about a physical asset at an address*, and modeling it later is a natural evolution (service history per unit). **Not built in v1**; noted here so no future decision accidentally forecloses it. No table, no fields — only this reservation.

---

## 7. Database Schema
```

Replace it with:

```
- **Property / Equipment** *(reserved, unimplemented)* — the home and the units in it (furnace, AC, water heater). Reserved because trade work is ultimately *about a physical asset at an address*, and modeling it later is a natural evolution (service history per unit). **Not built in v1**; noted here so no future decision accidentally forecloses it. No table, no fields — only this reservation.

**Long-term roster:** the full employee-by-employee roadmap (beyond the `RoleDefinition`s above) is enumerated as a code registry in `agent/employees.py`, not here — see `docs/superpowers/specs/2026-07-21-ai-staffing-repositioning-design.md` §4. Inclusion there does not imply implementation.

---

## 7. Database Schema
```

- [ ] **Step 6: Commit**

```bash
cd /Users/nandanreddyavanaganti/new_idea
git add agent/employees.py agent/tests/test_employees.py docs/superpowers/specs/2026-07-13-roster-platform-architecture-prd.md
git commit -m "$(cat <<'EOF'
Add employee registry (code, not a table)

Documentation-as-code for the long-term roster beyond Frontdesk. Nothing
in the running app reads it yet -- see spec 2026-07-21.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Self-serve link removal + DESIGN.md decision log

**Files:**
- Modify: `agent/templates/login.html:37`
- Modify: `DESIGN.md` (append to Decisions Log table)

**Interfaces:** None — pure template/doc edit, no code consumed or produced.

- [ ] **Step 1: Remove the residual self-serve link**

In `agent/templates/login.html`, find:

```html
  <p class="portal-foot">Haven't hired your team yet? <a href="/signup">Get started</a></p>
```

Delete that line entirely (the `<div class="portal-card">` closes right after it — no replacement content; `/signup` stays reachable by direct URL for founder-led onboarding, it's just no longer linked from any live page).

- [ ] **Step 2: Verify no test depends on the removed link**

Run: `cd agent && grep -n "Get started" tests/test_portal_login.py`
Expected: no output (already confirmed during spec review — this is a guard, not expected to find anything)

- [ ] **Step 3: Run the portal login tests**

Run: `cd agent && python -m pytest tests/test_portal_login.py -v`
Expected: PASS (no assertions reference the removed link)

- [ ] **Step 4: Append the DESIGN.md decision log entry**

At the end of `DESIGN.md` (last line of the Decisions Log table), add a new row:

```
| 2026-07-21 | Request-access onboarding made the standing default; dashboard "Hire" reframed as a reviewed conversation | See `docs/superpowers/specs/2026-07-21-ai-staffing-repositioning-design.md`. Supersedes the 2026-07-16 entry's "REVERT to self-serve... once Twilio provisions numbers" plan — request-led onboarding + a founder discovery call stays the default until customer-success metrics (onboarding time, activation rate, first-week retention) show a repeatable self-serve motion works, not because KYC clears or a fixed customer count is hit. `login.html`'s residual self-serve "Get started" link removed (landing itself had no self-serve CTA since the same 2026-07-16 pivot). Dashboard's existing data-driven Quote Chaser recommendation reframed from a self-serve "Hire" button to "Discuss Your Next AI Hire" — same backend (`requested_roster` queue for founder follow-up), copy only, so the recommendation reads as a reviewed business insight, not a feature purchase. |
```

- [ ] **Step 5: Commit**

```bash
cd /Users/nandanreddyavanaganti/new_idea
git add agent/templates/login.html DESIGN.md
git commit -m "$(cat <<'EOF'
Make request-access onboarding the standing default

Removes the last self-serve discovery path (login page's "Get started"
link) and records in DESIGN.md that this is no longer a temporary
KYC workaround with a planned reversion.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: SALES.md — onboarding principle + honesty-anchor fix

**Files:**
- Modify: `SALES.md`

**Interfaces:** None — doc-only.

- [ ] **Step 1: Insert the Customer Onboarding Principle section**

In `SALES.md`, find:

```
## The motion (first 20 = concierge)
Self-serve signup, then **you personally join a 15-minute onboarding call** per
pilot: configure forwarding, verify the AI, make the first real call succeed.
Those calls are also **customer research** — every friction goes in
`CUSTOMER.md`. Do not optimize for fully self-serve before you understand why
customers struggle.
```

Replace it with:

```
## Customer Onboarding Principle (diagnosis before deployment)
While founder-led onboarding is the default, Roster does not assume every
customer starts with the same AI employee. Every customer begins with a
business discovery session. We identify the customer's biggest operational
bottleneck, estimate where the highest ROI exists, and recommend the AI
employee most likely to solve that problem. We deploy that employee, measure
results, and use those results to guide future AI hires. Today, Frontdesk is
the only `live` employee (`agent/employees.py`), so it is usually the answer
— but the onboarding experience is built around diagnosis and ROI first,
deployment second, not product selection first.

## The motion (concierge until proven otherwise)
Request access, then **you personally join a 15-minute discovery call** per
pilot: understand the business's biggest bottleneck (see the Customer
Onboarding Principle above), configure forwarding, verify the AI, make the
first real call succeed. Those calls are also **customer research** — every
friction goes in `CUSTOMER.md`. Founder-led onboarding stays the default
until a repeatable process is validated by customer-success metrics, not by
hitting a fixed customer count. Do not optimize for fully self-serve before
you understand why customers struggle.
```

- [ ] **Step 2: Fix the stale honesty-anchor line**

In `SALES.md`, find:

```
- ✅ **Self-serve signup + onboarding** — real and live at rosterhires.com.
```

Replace it with:

```
- ✅ **Request-access + founder-led onboarding** — real and live at rosterhires.com. Self-serve `/signup` still works but is not advertised (see `DESIGN.md` 2026-07-21).
```

- [ ] **Step 3: Verify the replacements landed**

Run: `grep -n "Customer Onboarding Principle\|Request-access + founder-led onboarding\|concierge until proven otherwise" /Users/nandanreddyavanaganti/new_idea/SALES.md`
Expected: three matching lines, one per heading/phrase above.

- [ ] **Step 4: Commit**

```bash
cd /Users/nandanreddyavanaganti/new_idea
git add SALES.md
git commit -m "$(cat <<'EOF'
SALES.md: add Customer Onboarding Principle, fix stale self-serve claim

Diagnosis-before-deployment is now an explicit rule, not implicit in the
dashboard's existing recommendation logic. Also fixes an honesty-anchor
line that still claimed self-serve signup as the live path post the
2026-07-16 request-access pivot.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Dashboard copy reframe (the one UI-behavior change)

**Files:**
- Modify: `agent/templates/dashboard.html:130-158`

**Interfaces:** None. The form still POSTs to `/roster/hire` with `role=Quote Chaser` — same as today, handled by the existing, unmodified `portal.py:roster_hire`.

- [ ] **Step 1: Replace the "Your office" card's hire section**

In `agent/templates/dashboard.html`, find this exact block:

```html
  <p style="font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:var(--text-dim); margin:0 0 8px;">{% if 'Quote Chaser' in requested %}On the roster{% else %}Ready to hire{% endif %}</p>
  <div style="display:flex; align-items:flex-start; gap:12px;">
    <div style="flex:1;">
      <p style="font-weight:600; margin:0;">Quote Chaser</p>
      <p style="color:var(--text-dim); font-size:13px; margin:2px 0 0;">Follows up on every estimate, so the jobs {{ role_name|lower }} books don't go cold.</p>
      {% if 'Quote Chaser' not in requested and real_job_count > 0 %}
      <p style="font-size:12px; color:var(--accent); margin:7px 0 0;">› Recommended because you've already booked {{ real_job_count }} {{ 'job' if real_job_count == 1 else 'jobs' }} — the estimates are piling up.</p>
      {% endif %}
    </div>
    {% if 'Quote Chaser' in requested %}
    <span style="font-size:12px; font-weight:600; color:var(--text-dim); background:var(--bg-alt); border-radius:20px; padding:3px 10px; white-space:nowrap;">Setting up</span>
    {% else %}
    <form method="post" action="/roster/hire">
      <input type="hidden" name="role" value="Quote Chaser">
      <button type="submit" class="btn btn-primary" style="width:auto; padding:8px 16px;">Hire</button>
    </form>
    {% endif %}
  </div>

  <div style="border-top:1px solid var(--border); margin:16px 0;"></div>
  <p style="font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:var(--text-dim); margin:0 0 8px;">{% if 'Retention Manager' in requested %}On the roster{% else %}Ready when you've got customer history{% endif %}</p>
  <div style="display:flex; align-items:flex-start; gap:12px;{% if 'Retention Manager' not in requested %} opacity:.7;{% endif %}">
    <div style="flex:1;">
      <p style="font-weight:600; margin:0;">Retention Manager</p>
      <p style="color:var(--text-dim); font-size:13px; margin:2px 0 0;">Rebooking, renewals, and review asks — ready to go once you've built up customer history worth keeping warm.</p>
    </div>
    {% if 'Retention Manager' in requested %}
    <span style="font-size:12px; font-weight:600; color:var(--text-dim); background:var(--bg-alt); border-radius:20px; padding:3px 10px; white-space:nowrap;">Setting up</span>
    {% endif %}
  </div>
```

Replace it with:

```html
  <p style="font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:var(--text-dim); margin:0 0 8px;">{% if 'Quote Chaser' in requested %}On the roster{% else %}Opportunity detected{% endif %}</p>
  <div style="display:flex; align-items:flex-start; gap:12px;">
    <div style="flex:1;">
      <p style="font-weight:600; margin:0;">Quote Chaser</p>
      <p style="color:var(--text-dim); font-size:13px; margin:2px 0 0;">Follows up on every estimate, so the jobs {{ role_name|lower }} books don't go cold.</p>
      {% if 'Quote Chaser' not in requested and real_job_count > 0 %}
      <p style="font-size:12px; color:var(--accent); margin:7px 0 0;">› Recommended because you've already booked {{ real_job_count }} {{ 'job' if real_job_count == 1 else 'jobs' }} — the estimates are piling up.</p>
      <p style="font-size:12px; color:var(--text-dim); margin:4px 0 0;">There may be another AI employee that can help — we review your business before deploying one.</p>
      {% endif %}
    </div>
    {% if 'Quote Chaser' in requested %}
    <span style="font-size:12px; font-weight:600; color:var(--text-dim); background:var(--bg-alt); border-radius:20px; padding:3px 10px; white-space:nowrap;">We'll be in touch</span>
    {% else %}
    <form method="post" action="/roster/hire">
      <input type="hidden" name="role" value="Quote Chaser">
      <button type="submit" class="btn btn-primary" style="width:auto; padding:8px 16px;">Discuss Your Next AI Hire</button>
    </form>
    {% endif %}
  </div>

  <div style="border-top:1px solid var(--border); margin:16px 0;"></div>
  <p style="font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:var(--text-dim); margin:0 0 8px;">{% if 'Retention Manager' in requested %}On the roster{% else %}Ready when you've got customer history{% endif %}</p>
  <div style="display:flex; align-items:flex-start; gap:12px;{% if 'Retention Manager' not in requested %} opacity:.7;{% endif %}">
    <div style="flex:1;">
      <p style="font-weight:600; margin:0;">Retention Manager</p>
      <p style="color:var(--text-dim); font-size:13px; margin:2px 0 0;">Rebooking, renewals, and review asks — ready to go once you've built up customer history worth keeping warm.</p>
    </div>
    {% if 'Retention Manager' in requested %}
    <span style="font-size:12px; font-weight:600; color:var(--text-dim); background:var(--bg-alt); border-radius:20px; padding:3px 10px; white-space:nowrap;">We'll be in touch</span>
    {% endif %}
  </div>
```

- [ ] **Step 2: Run the dashboard and roster-hire tests**

Run: `cd agent && python -m pytest tests/test_portal_dashboard.py tests/test_portal_onboarding.py -k "roster_hire or dashboard" -v`

Also run the full roster-hire suite explicitly (these live in `test_portal_dashboard.py` per the file's existing structure):

Run: `cd agent && python -m pytest tests/test_portal_dashboard.py -v`
Expected: PASS — existing assertions check for `"Quote Chaser" in response.text` and `"Retention Manager" in response.text`, not button copy, so no test source changes are needed.

- [ ] **Step 3: Commit**

```bash
cd /Users/nandanreddyavanaganti/new_idea
git add agent/templates/dashboard.html
git commit -m "$(cat <<'EOF'
Dashboard: reframe Quote Chaser prompt as a reviewed recommendation

"Ready to hire" / "Hire" implied a self-serve feature purchase. Copy
only -- same backend (POST /roster/hire still just queues
requested_roster for founder follow-up).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Full-suite validation + manual smoke test

**Files:** None modified — verification only.

- [ ] **Step 1: Run the full backend test suite**

Run: `cd agent && python -m pytest tests/ -q`
Expected: all tests pass, zero failures. This is the Phase 3 gate from the founder's brief — "existing onboarding still works, existing customers unaffected, recommendation still queues correctly, voice/SMS/booking unchanged" is exactly what `test_portal_onboarding.py`, `test_portal_dashboard.py`, `test_channels.py`, `test_xai_voice_adapter.py`, `test_voice_loop_integration.py`, and `test_engine.py` already cover.

- [ ] **Step 2: Manual smoke test via the dev server**

Start the app (`cd agent && uvicorn app:app --reload` or the project's existing run command) and, using the Browser tool:
1. Load `/` — confirm the landing page still renders and the only CTA is "Request early access" (no self-serve link exposed).
2. Load `/login` — confirm the page renders and the "Get started" self-serve link is gone.
3. Log in as a seeded test business (or sign up fresh via `/signup` directly, since that route intentionally still works) and load `/dashboard` — confirm the "Your office" card shows "Opportunity detected" and the "Discuss Your Next AI Hire" button, and that clicking it flips the badge to "We'll be in touch" without erroring.
4. Confirm the dashboard test-chat (existing feature) still sends/receives — this exercises the same `handle_customer_message` path voice/SMS use, giving cheap confidence nothing in the shared engine broke even though this plan didn't touch it.

- [ ] **Step 3: Record results**

If every check in Steps 1–2 passes, this plan is complete. If anything fails, stop and fix before proceeding — do not commit further work or claim completion.

---

## Post-deploy checklist (manual, after the founder deploys to Railway)

Not part of this implementation session — Claude does not deploy without explicit separate authorization. Once the founder pushes/deploys, verify on the live site:
- [ ] Landing page still converts (request-access form submits, `/thanks` renders).
- [ ] `/login` works for an existing real customer.
- [ ] Dashboard copy reads correctly (no template errors, "Opportunity detected" / "Discuss Your Next AI Hire" render for accounts with `real_job_count > 0`).
- [ ] `/roster/hire` still queues correctly (check `Business.requested_roster` for a test click, or `/clients` founder dashboard shows it).
- [ ] Founder onboarding flow (`/clients/new` or a live `/signup` walk-through) works end-to-end for a fresh business.
