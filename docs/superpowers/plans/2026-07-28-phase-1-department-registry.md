# Phase 1 — Department Code Registry: Task-Level Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce `Department` as a real, importable code registry — the 7
canonical departments with the customer-facing copy Phase 5 renders, plus the
helpers that map an `Employee.role_key` to its department and derive which
departments a business actually has staffed.

**Architecture:** A code registry, not a database table — mirroring the
existing `RoleDefinition` (`runner.py`) and `EmployeeDefinition`
(`employees.py`) patterns. `departments.py` derives department *membership*
from `employees.py`'s existing `department` tag rather than duplicating a
member list, so the two registries cannot drift. Nothing imports the new
module at the end of this phase — it is inert, additive, and cannot regress
running behavior. Phases 4 and 5 are its first consumers.

**Tech Stack:** Python 3, dataclasses, pytest. No new dependency.

**Parent plan:** `docs/superpowers/plans/2026-07-28-departments-migration-execution-plan.md` (Phase 1)
**Product source of truth:** `docs/superpowers/specs/2026-07-28-departments-product-blueprint-design.md`

## Global Constraints

- Run all tests from the `agent/` directory: `cd agent && .venv/bin/python -m pytest tests/ -q`. The suite uses flat imports (`from employees import REGISTRY`), per `conftest.py`.
- Baseline to preserve: **318 tests passing.** Every task must leave the full suite green, not merely its own new tests.
- **No schema change, no migration, no new table or column** in this phase. Department is code, never data.
- **No existing module may import `departments.py` when this phase ends.** It is additive-only; wiring happens in Phases 4–5.
- `departments.py` must not import `db_models` — `active_departments_for` duck-types on `.role_key` and `.status` so the registry stays free of database dependencies and its tests need no DB fixture.
- Department keys are exactly: `customer_service`, `sales`, `operations`, `finance`, `customer_success`, `marketing`, `leadership`. `leadership` is the canonical name (matches the live `/roster` page and the blueprint) — **not** `intelligence`.
- Copy fields (`mission`, `problem`, `outcome`, `why_adopt`) follow `DESIGN.md`'s voice: warm, blunt, plain, zero jargon, "written by someone who's been in a truck." Two of them are quoted verbatim from the approved blueprint §7 and must not be reworded (Finance, Marketing).
- Commit after every task. Four tasks, four commits, each independently revertable.

---

## Regression Risks (read before starting)

**R1 — `roles.ROLE_KEYS` emits role keys that do not exist in `employees.REGISTRY`.** This is the sharpest edge in the phase and Task 3 exists to handle it.

`roles.py:57-61` maps display names onto `Employee.role_key` values:
```
"retention manager" -> "retention"     # employees.REGISTRY has "retention_manager"
"reviews"           -> "reviews"       # employees.REGISTRY had no such entry at all
```
Those values are written to real rows by `portal.py:53-58` (`_hire_employee`) and `db.py:203-205` (the backfill). So a live database can hold `Employee.role_key == "retention"` or `"reviews"`, neither of which the employee registry knew. Without handling, those employees resolve to **no department** and silently vanish from every department view built in Phases 4 and 5 — a bug that would look like "the dashboard is missing my Retention Manager" and be very hard to trace back here.

**Each of the two is fixed differently, on purpose:**
- `"reviews"` is a *missing employee*, so Task 1 registers it properly (under Customer Service — see the product decision below). No alias.
- `"retention"` is a *key-spelling mismatch* for an employee that is already registered, so Task 3 maps it. This is the only compatibility alias in the phase.

**R2 — `role_key_for()` slugifies unknown input.** `roles.py:66` falls back to `name.replace(" ", "_")` for any unrecognized role name, and `app.py:471`'s founder deploy route appends raw strings to `requested_roster`. Arbitrary role keys are therefore reachable from real data. `department_for_role()` must return `None` for them, never raise — Phases 4/5 must still render a business that has one.

**R3 — The two registries are joined by a bare string.** `EmployeeDefinition.department` and `Department.key` must agree exactly. Task 2 adds a cross-registry integrity test so drift fails at test time rather than in a template.

**R4 — Existing `test_employees.py` must keep passing unmodified** through Task 1's rename. Its three current assertions are about `status` and `key`, not `department`, so the rename should not touch them — verified by reading the file, and confirmed by running the suite.

**R5 (assessed, low) — nothing reads `department` today.** A repo-wide grep for `department` found hits only in `employees.py` itself, marketing HTML, and spec/plan documents. The Task 1 rename cannot break a consumer because there is no consumer.

### Product decision (founder, 2026-07-28) — Reviews belongs to Customer Service

**Decided:** `reviews` is a **Customer Service** employee. `retention_manager`
stays in **Customer Success**. The department model reflects the *product
architecture*, not the historical implementation — it is fine for a single
underlying engine to serve both today.

**How this is implemented:** rather than aliasing `"reviews"` onto
`retention_manager` (which would have put it in the wrong department), Task 1
adds a real `reviews` entry to `employees.REGISTRY` tagged `customer_service`,
status `internal`. The key `"reviews"` is already emitted by
`roles.ROLE_KEYS` and already written to real `Employee` rows, so this
registers an employee that genuinely exists in data but was missing from the
registry. `department_for_role("reviews")` then resolves natively with no
alias at all.

**Consequence:** the compatibility map in Task 3 shrinks to a single entry —
`"retention"` → `"retention_manager"` — which is a pure key-spelling
mismatch, not a department-model decision. The customer-facing department
model and the underlying implementation are cleanly separated: Reviews'
capability may still be executed by the same engine as Retention Manager,
but the customer sees it under the department that owns the outcome.

---

## Task 1: Align the employee registry with the canonical department model

Two corrections to `employees.py`, both prerequisites for the join
`departments.py` will make: rename the `intelligence` tag to `leadership`,
and register the `reviews` employee that already exists in data but was
missing from the registry.

**Files:**
- Modify: `agent/employees.py` — the two `intelligence` entries + section comment; add a `reviews` entry under Customer Service
- Test: `agent/tests/test_employees.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `EmployeeDefinition.department` now takes `"leadership"` where it previously took `"intelligence"`; `REGISTRY` gains `EmployeeDefinition("reviews", "customer_service", "internal", "Reviews")`. Every later task joins the two registries on the `department` string, and Task 3 relies on `reviews` resolving natively rather than through an alias.

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_employees.py`:

```python
def test_department_tags_are_the_canonical_seven():
    """Department tags are the join key between employees.py and
    departments.py. 'leadership' is canonical — it's what the live /roster
    page and the approved product blueprint both use; 'intelligence' was an
    internal-only name that never appeared anywhere customer-facing."""
    expected = {
        "customer_service", "sales", "operations",
        "finance", "customer_success", "marketing", "leadership",
    }
    assert {e.department for e in REGISTRY} == expected


def test_reviews_is_registered_under_customer_service():
    """roles.ROLE_KEYS already emits "reviews" as an Employee.role_key, so
    the employee exists in data — it was just missing from the registry.
    Founder decision 2026-07-28: Reviews belongs to Customer Service (the
    department that owns the outcome), while Retention Manager stays in
    Customer Success. One engine may serve both; the department model
    follows the product architecture, not the implementation."""
    by_key = {e.key: e for e in REGISTRY}
    assert by_key["reviews"].department == "customer_service"
    assert by_key["reviews"].status == "internal"
    assert by_key["retention_manager"].department == "customer_success"
```

- [ ] **Step 2: Run them and confirm they fail for the right reasons**

Run: `cd agent && .venv/bin/python -m pytest tests/test_employees.py -v`

Expected: 2 FAILs — the first showing `intelligence` present / `leadership` missing, the second a `KeyError: 'reviews'`. If either fails for a different reason, stop and investigate before changing code.

- [ ] **Step 3: Make both corrections**

In `agent/employees.py`, add the `reviews` entry to the Customer Service block:

```python
    # Customer Service
    EmployeeDefinition("frontdesk", "customer_service", "live", "Frontdesk"),
    EmployeeDefinition("support", "customer_service", "planned", "Support"),
    # Reviews' capability ships today inside the same engine as Retention
    # Manager, but it belongs to the department that owns the outcome
    # (founder, 2026-07-28). roles.ROLE_KEYS already emits this key, so
    # real Employee rows can carry it.
    EmployeeDefinition("reviews", "customer_service", "internal", "Reviews"),
```

…and change the section comment `# Intelligence` to `# Leadership`, with both entries retagged:

```python
    # Leadership
    EmployeeDefinition("business_analyst", "leadership", "planned", "Business Analyst"),
    EmployeeDefinition("operations_manager", "leadership", "planned", "Operations Manager"),
```

- [ ] **Step 4: Run the new tests, then the full suite**

Run: `cd agent && .venv/bin/python -m pytest tests/test_employees.py -v`
Expected: 5 passed — the 3 pre-existing tests unmodified (R4) plus the 2 new ones. In particular `test_exactly_one_live_employee_and_it_is_frontdesk` must still pass, which is why `reviews` is `internal`, not `live`.

Run: `cd agent && .venv/bin/python -m pytest tests/ -q`
Expected: `318 passed` → now `320 passed`. No failures.

- [ ] **Step 5: Commit**

```bash
git add agent/employees.py agent/tests/test_employees.py
git commit -m "refactor(employees): canonical department tags + register Reviews

Two corrections ahead of departments.py joining the two registries:

- intelligence -> leadership. The live /roster page and the approved
  blueprint both say Leadership; intelligence was internal-only and never
  customer-facing. Nothing reads this field yet (verified by grep).
- Register the Reviews employee under Customer Service. roles.ROLE_KEYS
  already emits 'reviews' as a role_key so it exists on real Employee
  rows, but the registry had no entry for it. Founder decision: Reviews
  belongs to the department that owns the outcome, while Retention
  Manager stays in Customer Success — one engine may serve both."
```

---

## Task 2: The `Department` dataclass and registry

**Files:**
- Create: `agent/departments.py`
- Test: `agent/tests/test_departments.py` (new)

**Interfaces:**
- Consumes: `employees.REGISTRY` (for the cross-registry integrity tests, and in Task 3 for membership).
- Produces:
  - `departments.Department` — frozen dataclass, fields: `key: str`, `display_name: str`, `mission: str`, `problem: str`, `outcome: str`, `why_adopt: str`, `hireable: bool = True`.
  - `departments.REGISTRY: list[Department]` — exactly 7 entries, in customer-facing display order.

- [ ] **Step 1: Write the failing tests**

Create `agent/tests/test_departments.py`:

```python
"""The department registry is the customer-facing unit of the product
(blueprint §1). These tests guard its own invariants and — critically — its
join with the employee registry: the two are linked by a bare string, and if
they drift, employees silently disappear from every department view."""
from departments import REGISTRY, Department
from employees import REGISTRY as EMPLOYEE_REGISTRY

CANONICAL_ORDER = [
    "customer_service", "sales", "operations",
    "finance", "customer_success", "marketing", "leadership",
]


def test_registry_holds_the_seven_departments_in_display_order():
    assert [d.key for d in REGISTRY] == CANONICAL_ORDER


def test_department_keys_are_unique():
    keys = [d.key for d in REGISTRY]
    assert len(keys) == len(set(keys))


def test_leadership_is_the_only_department_that_is_not_hireable():
    """Blueprint: Leadership is included automatically with any active
    department, never sold or hired separately."""
    assert [d.key for d in REGISTRY if not d.hireable] == ["leadership"]


def test_every_department_has_complete_customer_facing_copy():
    """Phase 5 renders these fields directly onto inactive department cards
    (blueprint §7). A blank field is a visible content bug, not a cosmetic
    one, so an empty string fails here rather than in a template."""
    for d in REGISTRY:
        for field in ("display_name", "mission", "problem", "outcome", "why_adopt"):
            assert getattr(d, field).strip(), f"{d.key}.{field} is empty"


def test_every_employee_department_tag_resolves_to_a_real_department():
    """R3: the registries are joined by a bare string. Fail here, not in a
    view that silently renders one department short."""
    department_keys = {d.key for d in REGISTRY}
    for e in EMPLOYEE_REGISTRY:
        assert e.department in department_keys, (
            f"employee {e.key!r} is tagged with unknown department {e.department!r}"
        )


def test_every_hireable_department_has_at_least_one_employee_behind_it():
    """A hireable department with nobody in it would be sellable and
    unstaffable — the one inconsistency this registry must never ship."""
    tagged = {e.department for e in EMPLOYEE_REGISTRY}
    for d in REGISTRY:
        if d.hireable:
            assert d.key in tagged, f"{d.key} is hireable but has no employees"
```

- [ ] **Step 2: Run and confirm failure**

Run: `cd agent && .venv/bin/python -m pytest tests/test_departments.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'departments'`.

- [ ] **Step 3: Create the registry**

Create `agent/departments.py`:

```python
"""Department registry — the customer-facing unit of Roster's product.

A department is a business capability delivered by a coordinated team of
specialised AI employees that share the same business context, memory, and
objectives. Customers hire departments because they buy outcomes, not
individual AI employees. (Product blueprint §1:
docs/superpowers/specs/2026-07-28-departments-product-blueprint-design.md)

This is a CODE registry, not a table — same pattern as employees.py's
EmployeeDefinition and runner.py's RoleDefinition. Department membership is
NOT duplicated here: it is derived from each EmployeeDefinition's own
`department` tag, so the two registries cannot drift out of sync.

The copy fields are customer-facing. `mission` heads an active department's
page; `problem`/`outcome`/`why_adopt` are rendered on an INACTIVE
department's card, which the blueprint (§7) requires to educate rather than
just report absence. Voice follows DESIGN.md: warm, blunt, plain, no jargon.
"""
from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class Department:
    key: str
    display_name: str
    mission: str      # one line, heads the department page
    problem: str      # inactive card: what's broken today
    outcome: str      # inactive card: what changes when it's staffed
    why_adopt: str    # inactive card: why an owner eventually wants it
    hireable: bool = True


# Display order is customer-facing and matches the live /roster page.
# Mission lines are reused verbatim from agent/landing/roster.html — already
# founder-approved copy in the right voice; do not rewrite them.
REGISTRY: List[Department] = [
    Department(
        key="customer_service",
        display_name="Customer Service",
        mission="Every call answered, every happy customer thanked.",
        problem=(
            "The phone rings while you're under a sink or on a roof. Nobody "
            "picks up, and the job goes to whoever answers next."
        ),
        outcome="Every call gets answered and the job gets on the books, day or night.",
        why_adopt=(
            "Usually the first department a shop staffs — a missed call is "
            "money gone today, not money gone next quarter."
        ),
    ),
    Department(
        key="sales",
        display_name="Sales",
        mission="Nobody works a quote, so it goes cold. This department doesn't let it.",
        problem=(
            "Estimates go out and nobody works them. Most quotes need several "
            "follow-ups before they close, and yours get one."
        ),
        outcome=(
            "Every open estimate gets chased until it's a yes or a no, so fewer "
            "of them just go quiet."
        ),
        why_adopt=(
            "Usually added once Customer Service is booking steadily and the "
            "pile of unanswered estimates becomes the obvious next leak."
        ),
    ),
    Department(
        key="operations",
        display_name="Operations",
        mission="The crew runs on schedule, even when nobody's watching it.",
        problem=(
            "Jobs get booked, then someone has to work out who's going where — "
            "and that someone is you, between jobs."
        ),
        outcome=(
            "The right tech gets to the right job, and urgent work finds the "
            "nearest free truck without a scramble."
        ),
        why_adopt=(
            "Usually added once there are enough trucks that dispatching stops "
            "being something you can hold in your head."
        ),
    ),
    Department(
        key="finance",
        display_name="Finance",
        mission="The money owed gets collected, not just invoiced.",
        problem=(
            "Invoices go out, and collecting on them means being the bad guy — "
            "or not collecting at all."
        ),
        outcome=(
            "Money you've already earned actually lands in the account, without "
            "an awkward phone call from you."
        ),
        why_adopt=(
            "Usually after noticing how much sits unpaid past 30 days once "
            "Customer Service and Operations are already busy booking and "
            "running jobs."
        ),
    ),
    Department(
        key="customer_success",
        display_name="Customer Success",
        mission="Old customers become repeat customers.",
        problem=(
            "Old customers who'd happily book again are sitting in a list nobody "
            "has time to call, and maintenance plans lapse quietly."
        ),
        outcome=(
            "Past customers come back on their own schedule, and plans get "
            "renewed before they expire."
        ),
        why_adopt=(
            "Usually added once there's enough customer history on the books to "
            "be worth working — it's the cheapest revenue in the business."
        ),
    ),
    Department(
        key="marketing",
        display_name="Marketing",
        mission="The phone rings without you spending on ads to make it ring.",
        problem=(
            "Happy customers would refer you and buy more, but nobody's "
            "consistently asking them to."
        ),
        outcome="The phone rings more without spending on ads to make it ring.",
        why_adopt=(
            "Usually once Customer Success is already rebooking old customers "
            "and the natural next question is \"how do I get new ones the same "
            "way.\""
        ),
    ),
    Department(
        key="leadership",
        display_name="Leadership",
        mission="Someone's watching the business, even at 11pm.",
        problem=(
            "You find out how the week really went by feel, usually after it's "
            "too late to do anything about it."
        ),
        outcome=(
            "One place that tells you what happened across the whole operation "
            "and what it means."
        ),
        why_adopt=(
            "Included with every workforce from the first department onward — it "
            "gets sharper as more departments come online and there's more to "
            "connect."
        ),
        hireable=False,
    ),
]
```

- [ ] **Step 4: Run the new tests, then the full suite**

Run: `cd agent && .venv/bin/python -m pytest tests/test_departments.py -v`
Expected: 6 passed.

Run: `cd agent && .venv/bin/python -m pytest tests/ -q`
Expected: `326 passed`. No failures.

- [ ] **Step 5: Commit**

```bash
git add agent/departments.py agent/tests/test_departments.py
git commit -m "feat(departments): add the Department code registry

Seven departments with the customer-facing copy Phase 5 renders on
active and inactive department cards. Membership is derived from
employees.py's department tag rather than duplicated, so the two
registries cannot drift; a cross-registry integrity test enforces it.

Code registry, not a table — same pattern as EmployeeDefinition and
RoleDefinition. Nothing imports this yet."
```

---

## Task 3: `department_for_role()` — including the legacy-key aliases

**Files:**
- Modify: `agent/departments.py` (append)
- Test: `agent/tests/test_departments.py` (append)

**Interfaces:**
- Consumes: `departments.REGISTRY`, `employees.REGISTRY`.
- Produces: `departments.department_for_role(role_key: str) -> Department | None` — returns `None` for any unrecognized key, never raises.

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_departments.py`:

```python
from departments import department_for_role


def test_department_for_role_resolves_a_registry_key():
    assert department_for_role("frontdesk").key == "customer_service"
    assert department_for_role("quote_chaser").key == "sales"
    assert department_for_role("collections").key == "finance"


def test_department_for_role_resolves_the_legacy_retention_key():
    """R1: roles.ROLE_KEYS maps "Retention Manager" -> "retention", but the
    employee registry's key is "retention_manager". That spelling is already
    on real Employee rows (written by portal.py:53 and db.py:203), so without
    this alias those employees resolve to no department at all and vanish
    from every department view in Phases 4 and 5."""
    assert department_for_role("retention").key == "customer_success"


def test_reviews_resolves_natively_to_customer_service_without_an_alias():
    """The other half of R1 is fixed as a registry entry, not an alias (Task
    1): Reviews is a real Customer Service employee, so "reviews" resolves
    through the normal path. Asserting the department here — not just that it
    resolves — is what guards the founder's 2026-07-28 decision from being
    quietly undone by a future edit to the alias map."""
    assert department_for_role("reviews").key == "customer_service"


def test_department_for_role_returns_none_for_an_unknown_key():
    """R2: roles.role_key_for() slugifies arbitrary text as a fallback, and
    app.py's deploy route appends raw strings, so unknown keys are reachable
    from real data. Return None — never raise, or Phase 4/5 can't render a
    business that has one."""
    assert department_for_role("something_nobody_registered") is None


def test_department_for_role_handles_empty_input():
    assert department_for_role("") is None


def test_every_role_key_roles_py_can_emit_resolves_to_a_department():
    """The regression guard for R1: if anyone adds a mapping to
    roles.ROLE_KEYS without a matching employee or alias, this fails here
    instead of silently hiding an employee in production."""
    from roles import ROLE_KEYS

    for role_key in sorted(set(ROLE_KEYS.values())):
        assert department_for_role(role_key) is not None, (
            f"roles.ROLE_KEYS can emit {role_key!r}, which resolves to no department"
        )
```

- [ ] **Step 2: Run and confirm failure**

Run: `cd agent && .venv/bin/python -m pytest tests/test_departments.py -v`
Expected: FAIL — `ImportError: cannot import name 'department_for_role' from 'departments'`.

- [ ] **Step 3: Implement**

Append to `agent/departments.py`:

```python
from employees import REGISTRY as _EMPLOYEE_REGISTRY

# roles.ROLE_KEYS predates the employee registry and spells one key
# differently: "retention" where the registry says "retention_manager". That
# spelling is already on real Employee rows (portal.py's _hire_employee,
# db.py's backfill), so this is a mapping over existing data, not a migration
# to run. ("reviews", the other mismatch, is NOT aliased — it's a real
# Customer Service employee registered in employees.py, so it resolves
# natively.) test_departments.py asserts every key roles.py can emit
# resolves through here.
_LEGACY_ROLE_KEYS = {
    "retention": "retention_manager",
}

_DEPARTMENT_KEY_BY_ROLE = {e.key: e.department for e in _EMPLOYEE_REGISTRY}
_BY_KEY = {d.key: d for d in REGISTRY}


def department_for_role(role_key: str):
    """The Department an Employee.role_key belongs to, or None if the key
    isn't one Roster knows. Returns None rather than raising: role keys reach
    the database from roles.role_key_for()'s slugify fallback and from the
    founder deploy route, so unknown values are ordinary data, not a bug."""
    canonical = _LEGACY_ROLE_KEYS.get(role_key, role_key)
    department_key = _DEPARTMENT_KEY_BY_ROLE.get(canonical)
    if department_key is None:
        return None
    return _BY_KEY.get(department_key)
```

- [ ] **Step 4: Run the new tests, then the full suite**

Run: `cd agent && .venv/bin/python -m pytest tests/test_departments.py -v`
Expected: 12 passed.

Run: `cd agent && .venv/bin/python -m pytest tests/ -q`
Expected: `332 passed`. No failures.

- [ ] **Step 5: Commit**

```bash
git add agent/departments.py agent/tests/test_departments.py
git commit -m "feat(departments): map Employee.role_key to its department

Includes aliases for the two legacy keys roles.ROLE_KEYS emits that the
employee registry doesn't contain ('retention', 'reviews') — both already
exist on real Employee rows, and without aliasing those employees would
resolve to no department and disappear from every department view.

Unknown keys return None rather than raising: role_key_for() slugifies
arbitrary text, so unrecognized keys are ordinary data."
```

---

## Task 4: `active_departments_for()` and `hireable_departments()`

**Files:**
- Modify: `agent/departments.py` (append)
- Test: `agent/tests/test_departments.py` (append)

**Interfaces:**
- Consumes: `departments.REGISTRY`, `departments.department_for_role`.
- Produces:
  - `departments.active_departments_for(employees) -> list[Department]` — takes any iterable of objects with `.role_key` and `.status` (duck-typed; deliberately not importing `db_models`). Returns departments in registry display order, deduplicated.
  - `departments.hireable_departments() -> list[Department]` — the 6 sellable departments, Leadership excluded.

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_departments.py`:

```python
from departments import active_departments_for, hireable_departments


class FakeEmployee:
    """departments.py duck-types on .role_key/.status so it never imports
    db_models — which also means these tests need no database fixture."""

    def __init__(self, role_key, status="active"):
        self.role_key = role_key
        self.status = status


def test_active_departments_for_returns_the_departments_that_are_staffed():
    staffed = active_departments_for([
        FakeEmployee("frontdesk"),
        FakeEmployee("quote_chaser"),
    ])
    assert [d.key for d in staffed] == ["customer_service", "sales"]


def test_active_departments_for_returns_display_order_not_input_order():
    """The dashboard renders these in a fixed, learnable order — it must not
    depend on the order rows came back from the database."""
    staffed = active_departments_for([
        FakeEmployee("quote_chaser"),   # sales
        FakeEmployee("frontdesk"),      # customer_service
    ])
    assert [d.key for d in staffed] == ["customer_service", "sales"]


def test_active_departments_for_deduplicates_two_employees_in_one_department():
    staffed = active_departments_for([
        FakeEmployee("quote_chaser"),
        FakeEmployee("lead_qualifier"),
    ])
    assert [d.key for d in staffed] == ["sales"]


def test_a_fired_employee_does_not_keep_a_department_staffed():
    assert active_departments_for([FakeEmployee("frontdesk", status="fired")]) == []


def test_a_paused_employee_keeps_its_department_staffed():
    """Paused is 'muted', not 'gone' — the department is still on the roster
    and must still appear on the dashboard."""
    staffed = active_departments_for([FakeEmployee("frontdesk", status="paused")])
    assert [d.key for d in staffed] == ["customer_service"]


def test_an_unknown_role_key_is_skipped_rather_than_crashing():
    """R2 again, at the aggregate level: a business carrying one unrecognized
    role_key must still render its other departments."""
    staffed = active_departments_for([
        FakeEmployee("mystery_role"),
        FakeEmployee("frontdesk"),
    ])
    assert [d.key for d in staffed] == ["customer_service"]


def test_active_departments_for_handles_an_empty_roster():
    assert active_departments_for([]) == []


def test_hireable_departments_is_the_six_sellable_ones():
    keys = [d.key for d in hireable_departments()]
    assert "leadership" not in keys
    assert len(keys) == 6
```

- [ ] **Step 2: Run and confirm failure**

Run: `cd agent && .venv/bin/python -m pytest tests/test_departments.py -v`
Expected: FAIL — `ImportError: cannot import name 'active_departments_for' from 'departments'`.

- [ ] **Step 3: Implement**

Append to `agent/departments.py`:

```python
def active_departments_for(employees) -> List[Department]:
    """Which departments this business actually has staffed, in display order.

    A department counts as staffed while it has at least one employee that
    hasn't been fired — `paused` is "muted", not "gone" (db_models.Employee
    status: active | paused | fired). Unknown role keys are skipped, so one
    unrecognized row can't hide a business's other departments.

    Takes any iterable of objects with .role_key and .status — duck-typed on
    purpose, so this module never imports db_models.
    """
    staffed = set()
    for employee in employees:
        if employee.status == "fired":
            continue
        department = department_for_role(employee.role_key)
        if department is not None:
            staffed.add(department.key)
    return [d for d in REGISTRY if d.key in staffed]


def hireable_departments() -> List[Department]:
    """The departments a customer can actually be sold. Leadership is
    excluded: it's included automatically with any active department and is
    never hired separately (blueprint §4a)."""
    return [d for d in REGISTRY if d.hireable]
```

- [ ] **Step 4: Run the new tests, then the full suite**

Run: `cd agent && .venv/bin/python -m pytest tests/test_departments.py -v`
Expected: 20 passed.

Run: `cd agent && .venv/bin/python -m pytest tests/ -q`
Expected: `340 passed`. No failures.

- [ ] **Step 5: Verify the phase's no-consumer constraint**

Run: `cd agent && grep -rn "import departments\|from departments" --include="*.py" . | grep -v tests/`
Expected: **no output.** Only tests may import `departments.py` at the end of this phase. Any hit outside `tests/` means the module got wired early, which breaks the phase's "cannot regress running behavior" guarantee.

- [ ] **Step 6: Commit**

```bash
git add agent/departments.py agent/tests/test_departments.py
git commit -m "feat(departments): derive a business's staffed departments

active_departments_for() groups an Employee list into departments in
display order, treating paused as still-staffed and fired as gone, and
skipping unknown role keys so one bad row can't hide the rest.

Duck-typed on .role_key/.status so departments.py never imports
db_models and its tests need no database fixture."
```

---

## Phase 1 Acceptance Criteria

All must hold before Phase 1 is submitted for review:

1. **Full suite green at 340 passed**, up from the 318 baseline — 22 new tests, zero modified pre-existing tests, zero failures, zero skips introduced.
2. **`agent/departments.py` is imported by nothing outside `agent/tests/`** — verified by the Task 4 Step 5 grep, which must return no output.
3. **No schema change** — `git diff main --stat -- agent/db_models.py agent/db.py` returns empty.
4. **Zero behavior change** — `git diff main --name-only` lists exactly four files: `agent/employees.py`, `agent/departments.py`, `agent/tests/test_employees.py`, `agent/tests/test_departments.py`. No route, template, webhook, or engine file is touched.
5. **Both registries agree** — the cross-registry integrity tests pass, and every role key `roles.ROLE_KEYS` can emit resolves to a department (the R1 guard). **Reviews resolves to Customer Service and Retention Manager to Customer Success**, each asserted by name so the founder's 2026-07-28 decision can't be quietly undone by a later edit.
6. **Every department carries complete customer-facing copy** — no empty `mission`, `problem`, `outcome`, or `why_adopt` on any of the 7.
7. **Four commits**, one per task, each independently revertable and each leaving the suite green on its own.
8. **The app still boots** — `cd agent && .venv/bin/python -c "import app"` exits 0, confirming no import cycle was introduced.

**Deliberately NOT in this phase** (and not a gap): no route, no template, no UI, no wiring into `portal.py`/`app.py`/`runner.py`, no `Employee.department` column. Departments become visible to a founder in Phase 4 and to a customer in Phase 5.
