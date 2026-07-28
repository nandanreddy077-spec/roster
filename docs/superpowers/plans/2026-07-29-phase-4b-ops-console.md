# Phase 4b — Ops Console: Task-Level Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Roster team the founder-facing half of blueprint §10a — an
honest, department-grouped view of what is actually deployed, deploy-by-
department, the provisioning pipeline, and expansion requests.

**Architecture:** First consumer of Phase 1's registry, Phase 3's expansion
requests, and Phase 4a's corrected deployment model. Reuses the existing
card-grid admin UI and `portal.css` tokens — `DESIGN.md` fixes the dashboard
as "grid-disciplined, existing card-grid convention, unchanged," so this is
new *content* in an existing visual language, not a redesign.

**Tech Stack:** Python 3, FastAPI, SQLModel, Jinja2, pytest. No new dependency.

**Parent plan:** `docs/superpowers/plans/2026-07-28-departments-migration-execution-plan.md` (Phase 4b)
**Grounding:** `docs/superpowers/specs/2026-07-29-phase-4-deployment-path-audit.md`

## Global Constraints

- Run tests from `agent/`: `cd agent && .venv/bin/python -m pytest tests/ -q`.
- Baseline to preserve: **405 passing** (end of Phase 4a).
- **Founder-facing only.** No change to `portal.py`, `dashboard.html`, or any customer-visible route or template. A diff touching the customer portal means the phase has drifted.
- **`deployment.py` remains the only module that creates `Employee` rows.** Routes call it; they never construct one.
- **Every mutation is POST → 303 redirect → GET** (the pattern every existing founder route already uses), so a browser refresh re-issues the read, never the write.
- **Every founder mutation must be idempotent to the UI** — a double-submit or a stale refresh must produce the same end state, not an error page and not a second effect.
- `DESIGN.md` governs any visual decision; reuse existing classes (`agent-tile`, `agent-badge`, `client-card`, `btn-small`), no new palette.
- Commit after every task; each task leaves the full suite green on its own.

---

## Pre-Implementation Audit

### B1 — The founder's "Agent roster" is entirely fake ⚠️ *(biggest finding)*

`client_detail.html:11-16` renders Frontdesk with a **hardcoded** active badge:

```html
<span class="agent-badge agent-badge-active">Frontdesk</span>
<p class="agent-tile-desc">Always on — answers inbound calls &amp; texts…</p>
```

It reads nothing. Not `Employee`, not `requested_roster`, not even
`frontdesk_live`. **Every business shows Frontdesk as active, deployed or
not.** `clients.html:28` does the same on the list view.

The other tiles derive "active" from fields that have nothing to do with
deployment:

| Tile | "Active" actually means |
|---|---|
| Frontdesk | *nothing* — hardcoded |
| Quote/Reactivation/Membership | ≥1 `RecoveryCampaign` exists |
| Reviews | `review_link` is set |
| Lead-gen | `referral_incentive` is set |

**Consequence:** Phase 4b is not "adding department grouping to an existing
deployment view." **There has never been a deployment view.** The founder has
been reading config-presence badges that look like deployment status. This
raises the phase's value and slightly its scope: Task 1 replaces a fiction
with a fact.

### B2 — The deploy route has no UI at all ⚠️

No template anywhere references `/clients/{id}/employees/deploy` (verified by
grep across `templates/`). The route is reachable only by `curl`.

**This is why Phase 4a's bug survived**: the route that failed to create
`Employee` rows could not be triggered from a browser, so nobody saw the
consequence. Phase 4b ships its first UI — on top of a version that now
works.

### B3 — No stale `requested_roster` reads in the founder surface ✅

Zero references in any template. Its only remaining readers after Phase 4a are
`roles.next_hire()` via the **customer** dashboard (dies in Phase 7) and
`db._backfill_employees`. Phase 4b introduces no new reader, and Task 1's
replacement of the fake roster means the founder surface never gains one.

### B4 — POST → 303 → GET is used consistently ✅

Every founder mutation returns `RedirectResponse(..., status_code=303)`
(verified across all 8 mutating routes). A refresh after a POST re-issues the
GET. Phase 4b must preserve this for its new routes — it is the existing
protection against stale-refresh repetition, and it is the reason ordering
between deployment and rendering is safe: rendering always happens in a
*separate request* with a *fresh session*, never from the POST handler.

### B5 — ⚠️ `provision-number` is not idempotent and costs real money *(pre-existing)*

`/clients/{id}/provision-number` **always buys a fresh Twilio number**. Its
sibling route's docstring says so explicitly:

> *"Does not purchase a new number — that route always buys fresh, which would
> waste money re-buying on every retry while debugging the xAI side."*

PRG protects against refresh-after-response. It does **not** protect against a
**double-click before the first response returns** — two POSTs are already in
flight, and each buys a number. The wasted number is never released.

This is the largest duplicate-action risk in the entire founder workflow, it
is pre-existing rather than introduced here, and the fix is small: refuse
server-side when `twilio_number_sid` is already set, and disable the submit
button on click. **Recommended as Task 6, flagged for founder approval since
it is outside Phase 4b's stated scope.**

### B6 — N+1 risk on the clients list, with the fix already demonstrated in-repo

`/clients` renders every business. Adding "which departments are staffed"
naively means one `Employee` query per business.

The existing code already shows the right pattern — recovery counts are
fetched in **one grouped query**, not per row:

```python
select(RecoveryCampaign.business_id, func.count(RecoveryCampaign.id))
    .group_by(RecoveryCampaign.business_id)
```

Task 1 does the same for employees: **one** query for all of them, grouped by
`business_id` in Python, then `active_departments_for()` per business over
the in-memory list. Two queries total regardless of business count, with a
regression test asserting the query count does not grow with the number of
businesses.

### B7 — `pipeline_stage` must take an explicit target, not "advance"

A route meaning *"advance to the next stage"* is **not** idempotent: a
double-submit advances twice, silently skipping a stage. A route taking an
explicit target stage is naturally idempotent — setting `qa` twice leaves it
at `qa`.

Additionally, setting the stage it is **already** on must be a **no-op
success**, not a validation error: a stale tab re-submitting the current stage
is harmless and must not show the founder an error page.

### B8 — `mark_actioned` is already idempotent ✅

Phase 3 built it to keep the original timestamp on re-close. Its route needs
only PRG. Actioning a request that another tab already actioned is a no-op.

### B9 — Deploy-by-department is idempotent end to end, if the UI cooperates

`deploy_department` already returns only newly-created rows and raises for
non-deployable departments. So a second click returns `[]` and changes
nothing. Two UI requirements make that visible rather than merely harmless:

- Once a department is fully staffed, the deploy button is **replaced by its
  status**, so the founder isn't invited to repeat a no-op.
- A **partially** staffed department (Phase 4a's I10 recovery case) shows
  "Complete deployment", not "Deploy" — the affordance tells the truth about
  what the click will do.

### B10 — Departments with nothing deployable must be visibly refused

Operations, Finance and Marketing are hireable in the registry but have zero
`live`/`internal` employees today (audit F4). `deploy_department` raises for
them. The UI must not render a button that always errors — it shows why the
department can't be staffed yet.

---

## Task 1: Replace the fake roster with a real, department-grouped view

**Files:**
- Modify: `agent/app.py` — `client_detail` and `list_clients` supply real deployment data
- Modify: `agent/templates/client_detail.html` — department-grouped roster replaces the hardcoded tiles
- Modify: `agent/templates/clients.html` — real department badges
- Test: `agent/tests/test_ops_console.py` (new)

**Interfaces:**
- Produces: `app._employees_by_business(session, business_ids) -> dict[int, list[Employee]]` — **one** query for all listed businesses (B6).

- [ ] **Step 1: Write the failing tests**

Create `agent/tests/test_ops_console.py`:

```python
"""The founder ops console.

Before Phase 4b the 'Agent roster' on this page was a fiction: Frontdesk was
hardcoded active for every business, and the other tiles derived 'active' from
unrelated config fields (a review link being set, a campaign existing). The
founder has never had a real deployment view — see audit B1."""
from sqlmodel import Session, select

import app as app_module
from conftest import DASH_AUTH
from db_models import Business, Employee
from deployment import deploy_department, deploy_role
from starlette.testclient import TestClient


def _business(session, name="Test Co"):
    b = Business(business_name=name, trade="HVAC")
    session.add(b)
    session.commit()
    session.refresh(b)
    return b


def test_a_business_with_nothing_deployed_shows_no_staffed_departments(test_engine, monkeypatch):
    """B1: the page used to claim Frontdesk was active for every business,
    deployed or not."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id

    body = TestClient(app_module.app, headers=DASH_AUTH).get(f"/clients/{bid}").text

    assert "Not staffed" in body
    assert "Customer Service" in body  # the department is listed, just not staffed


def test_a_deployed_department_is_shown_as_staffed(test_engine, monkeypatch):
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id
        deploy_department(s, bid, "customer_service")

    body = TestClient(app_module.app, headers=DASH_AUTH).get(f"/clients/{bid}").text

    assert "Customer Service" in body
    assert "Frontdesk" in body   # employees visible as secondary detail
    assert "Reviews" in body


def test_a_partially_staffed_department_offers_completion_not_a_fresh_deploy(test_engine, monkeypatch):
    """B9: the affordance must tell the truth about what the click will do."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id
        deploy_role(s, bid, "frontdesk")  # half of Customer Service

    body = TestClient(app_module.app, headers=DASH_AUTH).get(f"/clients/{bid}").text

    assert "Complete deployment" in body


def test_a_department_with_nothing_deployable_is_explained_not_offered(test_engine, monkeypatch):
    """B10: Operations/Finance/Marketing have zero live-or-internal employees
    today. Rendering a button that always errors would be a lie."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id

    body = TestClient(app_module.app, headers=DASH_AUTH).get(f"/clients/{bid}").text

    assert "No employees built yet" in body


def test_the_clients_list_shows_real_department_counts(test_engine, monkeypatch):
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        staffed = _business(s, "Staffed Co").id
        _business(s, "Empty Co")
        deploy_department(s, staffed, "customer_service")

    body = TestClient(app_module.app, headers=DASH_AUTH).get("/clients").text

    assert "Staffed Co" in body and "Empty Co" in body
    assert "Customer Service" in body


def test_the_clients_list_does_not_query_per_business(test_engine, monkeypatch):
    """B6: one grouped query for every business's employees, matching the
    pattern the recovery counts already use. Asserting the query count is what
    stops this regressing into an N+1 as the client list grows."""
    from sqlalchemy import event

    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        for i in range(5):
            bid = _business(s, f"Co {i}").id
            deploy_role(s, bid, "frontdesk")

    statements = []
    event.listen(test_engine, "before_cursor_execute",
                 lambda *a, **k: statements.append(a[2]))
    try:
        TestClient(app_module.app, headers=DASH_AUTH).get("/clients")
    finally:
        event.remove(test_engine, "before_cursor_execute",
                     lambda *a, **k: statements.append(a[2]))

    employee_queries = [s_ for s_ in statements if "FROM employee" in s_]
    assert len(employee_queries) == 1, (
        f"expected ONE grouped employee query, got {len(employee_queries)} "
        "— this is an N+1 over the client list"
    )
```

- [ ] **Step 2: Run and confirm failure**

Run: `cd agent && .venv/bin/python -m pytest tests/test_ops_console.py -q 2>&1 | tail -6`
Expected: failures on the missing strings (`Not staffed`, `Complete deployment`,
`No employees built yet`) — the template still renders the hardcoded roster.

- [ ] **Step 3: Build the view data in `app.py`**

Add the grouped fetch and use it in both routes:

```python
def _employees_by_business(session, business_ids: list) -> dict:
    """One query for every listed business's employees, grouped in Python.
    A per-business query here would be an N+1 over the whole client list —
    the same trap the recovery counts already avoid with a grouped query."""
    if not business_ids:
        return {}
    rows = session.exec(
        select(Employee).where(Employee.business_id.in_(business_ids))
    ).all()
    grouped = {bid: [] for bid in business_ids}
    for e in rows:
        grouped.setdefault(e.business_id, []).append(e)
    return grouped
```

In `list_clients`, pass `active_departments_for(...)` per business, computed
over the in-memory group. In `client_detail`, build one row per department:

```python
def _department_rows(employees):
    """Every department, with what's deployed for this business. Renders the
    same list for every business so the founder sees the whole org, with
    honest state per department rather than a hardcoded badge."""
    deployed_keys = {e.role_key for e in employees if e.status != "fired"}
    rows = []
    for department in departments.REGISTRY:
        deployable = departments.deployable_employees_for(department.key)
        staffed = [d for d in deployable
                   if d.key in deployed_keys
                   or departments.canonical_role_key(d.key) in deployed_keys]
        rows.append({
            "department": department,
            "deployable": deployable,
            "staffed": staffed,
            "state": (
                "unavailable" if not deployable
                else "staffed" if len(staffed) == len(deployable)
                else "partial" if staffed
                else "empty"
            ),
        })
    return rows
```

- [ ] **Step 4: Rewrite the roster section of `client_detail.html`**

Replace the hardcoded `agent-roster-grid` tiles (lines 11–60 and 79–114 of the
current file) with a loop over `department_rows`, reusing existing classes.
Per row: department name + mission; a badge whose text is `Staffed` /
`Partially staffed` / `Not staffed` / `No employees built yet`; the staffed
employees listed as secondary detail; and for `partial`, the words
"Complete deployment". **The phone-number tile, Reviews link form, Lead-gen
form, recovery campaign panels, conversation, jobs and danger zone all stay
exactly as they are** — they are configuration, not deployment, and this task
only replaces the part that lied about deployment.

Apply the same treatment to `clients.html`'s `agent-badges` block: real
staffed-department names instead of the hardcoded `Frontdesk` badge.

- [ ] **Step 5: Run tests, then the full suite**

Run: `cd agent && .venv/bin/python -m pytest tests/test_ops_console.py -v`
Expected: 6 passed.

Run: `cd agent && .venv/bin/python -m pytest tests/ -q`
Expected: `411 passed`.

- [ ] **Step 6: Commit**

```bash
git add agent/app.py agent/templates/client_detail.html agent/templates/clients.html agent/tests/test_ops_console.py
git commit -m "feat(ops): a real deployment view, replacing a hardcoded one

The Agent roster was a fiction: Frontdesk was hardcoded active for every
business, deployed or not, and the other tiles derived 'active' from
unrelated config (a review link being set, a campaign existing). The
founder has never had an accurate deployment view.

Now grouped by department and read from Employee rows, with honest states:
staffed, partially staffed, not staffed, or no employees built yet.

One grouped employee query for the whole client list, matching the pattern
the recovery counts already use, with a query-count test so it can't
regress into an N+1."
```

---

## Task 2: Deploy by department, idempotent to the UI

**Files:**
- Modify: `agent/app.py` — `deploy_employee` route accepts `department_key`
- Modify: `agent/templates/client_detail.html` — the deploy form
- Test: `agent/tests/test_ops_console.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
# --- deploy by department ------------------------------------------------------


def _post_deploy(test_engine, bid, **data):
    return TestClient(app_module.app, headers=DASH_AUTH).post(
        f"/clients/{bid}/employees/deploy", data=data, follow_redirects=False
    )


def test_deploying_a_department_creates_every_deployable_role(test_engine, monkeypatch):
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id

    r = _post_deploy(test_engine, bid, department_key="customer_service")

    assert r.status_code == 303
    with Session(test_engine) as s:
        keys = {e.role_key for e in s.exec(
            select(Employee).where(Employee.business_id == bid)).all()}
        assert keys == {"frontdesk", "reviews"}


def test_deploying_the_same_department_twice_is_a_no_op(test_engine, monkeypatch):
    """B9: a double-submit must reach the same end state, not error and not
    duplicate."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id

    first = _post_deploy(test_engine, bid, department_key="customer_service")
    second = _post_deploy(test_engine, bid, department_key="customer_service")

    assert first.status_code == 303 and second.status_code == 303
    with Session(test_engine) as s:
        assert len(s.exec(select(Employee).where(Employee.business_id == bid)).all()) == 2


def test_deploying_a_department_with_nothing_deployable_does_not_500(test_engine, monkeypatch):
    """B10: deploy_department raises for these. The route must surface it as a
    message on the page, never as a stack trace."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id

    r = _post_deploy(test_engine, bid, department_key="operations")

    assert r.status_code == 303
    assert "deploy_error" in r.headers["location"]


def test_the_legacy_role_key_form_still_works(test_engine, monkeypatch):
    """The role_key parameter predates this and is still how a single employee
    is deployed; it must keep working (test_employee_deploy.py covers it too)."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id

    _post_deploy(test_engine, bid, role_key="quote_chaser")

    with Session(test_engine) as s:
        assert [e.role_key for e in s.exec(
            select(Employee).where(Employee.business_id == bid)).all()] == ["quote_chaser"]
```

**Plus one end-to-end workflow test (founder request, 2026-07-29)** — this
protects the actual browser round-trip rather than the route and the template
separately, and is the real guard on B4's deploy→render ordering:

```python
def test_deploying_a_department_then_following_the_redirect_shows_it_staffed(
    test_engine, monkeypatch
):
    """The founder's actual workflow, end to end: submit the form, follow the
    303, and see the result. Route-level and template-level tests both passing
    would still permit a page that renders stale data after a write — this is
    what catches that."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id
    client = TestClient(app_module.app, headers=DASH_AUTH)

    posted = client.post(
        f"/clients/{bid}/employees/deploy",
        data={"department_key": "customer_service"},
        follow_redirects=False,
    )
    assert posted.status_code == 303

    landed = client.get(posted.headers["location"])

    assert landed.status_code == 200
    assert "Working: Frontdesk, Reviews" in landed.text
    assert "Not staffed" not in landed.text.split("Customer Service")[1][:200]
```

- [ ] **Step 2–4: Confirm failure, then extend the route**

`deploy_employee` gains an optional `department_key` form field. When present
it calls `deploy_department`; when absent it keeps today's `role_key` path
unchanged. Both `ValueError` cases redirect with a `deploy_error` query param
(the same mechanism `provision_number` already uses with `provision_error`),
which `client_detail` renders — never a 500.

Add the deploy button to each department row where `state` is `empty` or
`partial`, labelled "Deploy" or "Complete deployment" per B9, with
`onsubmit` disabling the button to blunt double-clicks.

- [ ] **Step 5: Run tests + full suite.** Expected: `415 passed`.
- [ ] **Step 6: Commit.**

---

## Task 3: The provisioning pipeline stage

**Files:**
- Modify: `agent/db_models.py` — `Business.pipeline_stage`
- Modify: `agent/db.py` — additive column migration + backfill to `"live"` for existing `frontdesk_live` businesses
- Modify: `agent/app.py` — `POST /clients/{id}/pipeline-stage`
- Modify: `agent/templates/client_detail.html`, `clients.html`
- Test: `agent/tests/test_ops_console.py` (append)

**Design, per B7:** the route takes an **explicit target stage**, never
"advance". Setting the current stage is a **no-op success**. Backward moves
are allowed (ops corrects a mis-click); unknown stages are rejected.

Key tests: setting a stage twice is idempotent; setting the current stage is
not an error; an unknown stage is rejected; existing `frontdesk_live`
businesses backfill to `"live"`; the list view can be read by stage.

Expected after: `421 passed`.

---

## Task 4: Surface and action expansion requests

**Files:**
- Modify: `agent/app.py` — open interests on `client_detail`; `POST /clients/{id}/interests/{interest_id}/actioned`
- Modify: `agent/templates/client_detail.html`
- Test: `agent/tests/test_ops_console.py` (append)

Uses Phase 3's `open_interests_for` and `mark_actioned` unchanged. Because
`mark_actioned` is already idempotent (B8), the route needs only PRG. Tests:
open requests render oldest-first; actioning removes it from the open list;
actioning twice is harmless; actioning another business's interest id is
refused (business isolation at the route layer).

Expected after: `426 passed`.

---

## Task 5: `/clients/new` reaches parity and becomes the single door

**Files:**
- Modify: `agent/app.py` — `create_client` accepts the recommended department(s)
- Modify: `agent/templates/new_client.html`
- Test: `agent/tests/test_ops_console.py` (append)

`/clients/new` becomes the only way a business is created once Phase 7 retires
`/signup`, so this is a hard prerequisite for Phase 6. Field-by-field parity
test against everything the retired onboarding wizard collected (business
name, trade, services, hours, pricing/FAQ, escalation phone, answer mode),
plus optionally deploying the recommended department at creation through
`deployment.py`.

Expected after: `431 passed`.

---

## Task 6 *(approved — kept LAST so it stays independently reviewable)*: make `provision-number` idempotent

**Why it's here:** B5. `/clients/{id}/provision-number` always buys a fresh
Twilio number. PRG protects against refresh-after-response but **not** against
a double-click while the first request is still in flight — two POSTs, two
numbers bought, the spare never released. It is the most expensive duplicate
action in the founder workflow and it is pre-existing.

**Fix:** refuse server-side when `twilio_number_sid` is already set
(redirecting with an explanatory message rather than buying), and disable the
submit button on click. Test: a second POST buys nothing and reports why.

**Approved (founder, 2026-07-29):** included in Phase 4b, as the **final
commit of the phase**, so that although it ships here it remains independently
reviewable and independently revertable — it is unrelated to departments and
should not be buried inside a department-shaped diff.

---

## Phase 4b Acceptance Criteria

1. **Full suite green** at the task's stated count (433 with Task 6 included).
2. **No customer-facing change** — `git diff` on `portal.py`, `dashboard.html`, and the customer templates is empty.
3. **No route constructs an `Employee`** — `deployment.py` remains the only writer.
4. **Every founder mutation is idempotent to the UI** — a double-submit of deploy, pipeline-stage, and action-interest each leaves the same end state with no error page.
5. **No N+1** — the clients list issues exactly one employee query regardless of business count, asserted by a query-count test.
6. **No route 500s on a rejected deploy** — `ValueError` from `deployment.py` becomes a message on the page.
7. **The deployment view reads `Employee` rows only** — no template reads `requested_roster`, and nothing is hardcoded active.
8. **Business isolation at the route layer** — actioning an interest belonging to another business is refused.
