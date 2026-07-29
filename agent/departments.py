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

from employees import REGISTRY as _EMPLOYEE_REGISTRY


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


@dataclass(frozen=True)
class DepartmentStatus:
    """What is actually deployed in one department for one business.

    PRESENTATION-NEUTRAL BY RULE (founder, 2026-07-29). Facts only — no
    labels, no copy, no wording. The founder console renders `empty` as
    "Not staffed"; the customer dashboard renders the same `empty` as "Not yet
    part of your workforce". Shared computation, independent presentation:
    a wording field here would leak one audience's voice onto the other's
    screen the first time either changed.

    Both surfaces MUST derive deployment state from this — never from
    requested_roster, a tested_at timestamp, or a hardcoded template badge
    (the blueprint's permanent state-derivation invariant).
    """
    department: Department
    deployable: List          # EmployeeDefinition — can be provisioned today
    staffed: List             # EmployeeDefinition — actually deployed
    deployed_count: int
    deployable_count: int
    state: str                # staffed | partial | empty | unavailable


def department_status_for(employees) -> List[DepartmentStatus]:
    """Every department's real state for one business, in display order.

    Takes any iterable of objects with .role_key and .status — duck-typed, so
    this module still never imports db_models.
    """
    deployed = {
        canonical_role_key(e.role_key)
        for e in employees if e.status != "fired"
    }
    statuses = []
    for department in REGISTRY:
        deployable = deployable_employees_for(department.key)
        staffed = [d for d in deployable if canonical_role_key(d.key) in deployed]
        if not deployable:
            state = "unavailable"
        elif len(staffed) == len(deployable):
            state = "staffed"
        elif staffed:
            state = "partial"
        else:
            state = "empty"
        statuses.append(DepartmentStatus(
            department=department,
            deployable=deployable,
            staffed=staffed,
            deployed_count=len(staffed),
            deployable_count=len(deployable),
            state=state,
        ))
    return statuses


def canonical_role_key(role_key: str) -> str:
    """The registry spelling of a role key, resolving legacy spellings.
    roles.ROLE_KEYS emits "retention" where the registry says
    "retention_manager", and that spelling is on real Employee rows
    (test_employee_model.py pins it), so callers comparing a stored key to the
    registry must normalize through here."""
    return _LEGACY_ROLE_KEYS.get(role_key, role_key)


def deployable_employees_for(department_key: str) -> List:
    """The employees in this department that can actually be provisioned —
    registry status `live` or `internal`. NEVER `planned`: those have no
    engine, and deploying one would make active_departments_for() report the
    department as staffed while it cannot do any work (audit F4)."""
    return [
        e for e in _EMPLOYEE_REGISTRY
        if e.department == department_key and e.status in ("live", "internal")
    ]


def get_department(key: str):
    """The Department with this key, or None. The public lookup for callers
    validating a department key that arrived from outside the system (see
    expansion.record_interest)."""
    return _BY_KEY.get(key)


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
