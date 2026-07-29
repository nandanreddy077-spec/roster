"""DepartmentWorkspace — the ONE view model the Department Workspace template
renders (founder, 2026-07-29).

Shared registries -> shared view models -> presentation. This module is the
"shared view model" layer: it joins departments.py's DepartmentStatus with
metrics.py's METRIC_RECORDS/EMPLOYEE_RECORDS exactly once, so no template or
route ever has to. A template that joined those registries itself would be
duplicating business logic — exactly what every invariant in this migration
exists to prevent.

CUSTOMER_STATE_LABELS and METRIC_LABELS live here — moved out of portal.py so
there is exactly one copy of the customer's wording, not two maps that can
drift. portal.py imports them rather than redefining them.
"""
from dataclasses import dataclass
from typing import List, Optional, Tuple

import metrics
from db_models import Employee
from departments import department_status_for, get_department
from employees import REGISTRY as _EMPLOYEE_REGISTRY

_EMPLOYEE_BY_KEY = {e.key: e for e in _EMPLOYEE_REGISTRY}

# Only a staffed (non-fired) employee inside an active department ever reaches
# an EmployeeWorkspace today. Customer pause/resume controls don't exist yet
# (Phase 4a's I14 deferral — "customer controls in v1 stay intentionally
# minimal"), so there is exactly one status label until that changes.
EMPLOYEE_STATUS_LABEL = "Working"

# The CUSTOMER's wording for each DepartmentStatus state. The founder console
# keeps its own map over the same states (app.py) — DepartmentStatus is
# presentation-neutral so neither audience's voice leaks into the other's.
#
# `partial` reads as Working on purpose: a half-staffed department IS doing
# work, and finishing it is Roster's operational problem, not the owner's
# worry (audit C7). `unavailable` reads the same as `empty` — the customer
# doesn't need to know whether a department is unstaffed or unbuilt.
CUSTOMER_STATE_LABELS = {
    "staffed": "Working",
    "partial": "Working",
    "empty": "Not yet part of your workforce",
    "unavailable": "Not yet part of your workforce",
}
ACTIVE_STATES = ("staffed", "partial")

# The CUSTOMER's wording for each metric. Every key in metrics.METRIC_RECORDS
# must appear here, or it would render as a bare, unlabeled number.
METRIC_LABELS = {
    metrics.JOBS_BOOKED: "Jobs booked",
    metrics.CALLS_ANSWERED: "Calls answered",
    metrics.ESCALATIONS: "Sent to you personally",
    metrics.REVIEW_REQUESTS_SENT: "Review requests sent",
    metrics.QUOTES_CHASED: "Estimates followed up",
    metrics.QUOTES_RECOVERED: "Estimates won back",
    metrics.CUSTOMERS_REACHED: "Past customers contacted",
    metrics.CUSTOMERS_RETURNED: "Customers who came back",
    metrics.REFERRALS_RECEIVED: "Referrals received",
}


@dataclass(frozen=True)
class EmployeeView:
    """One employee's presence in the workspace: what it did, ready to
    render. Facts from EMPLOYEE_RECORDS, labels from METRIC_LABELS."""
    role_key: str
    display_name: str
    outcomes: List[Tuple[str, int]]
    activity: List[metrics.ActivityRow]


@dataclass(frozen=True)
class DepartmentWorkspace:
    """Everything the Department Workspace page renders. The template's job
    is to lay this out — it never queries, joins a registry, or decides
    anything a route or this module hasn't already decided."""
    department: object          # departments.Department
    question: str
    is_active: bool
    health_label: str
    employees: List[EmployeeView]
    outcomes: List[Tuple[str, int]]
    activity: List[metrics.ActivityRow]


@dataclass(frozen=True)
class EmployeeWorkspace:
    """Everything the Employee Workspace page renders — the drill-down leaf
    of Overview -> Department -> Employee -> Activity. Deliberately just five
    fields: mission and status answer the one question this page opens with,
    before any raw event (founder, 2026-07-29). No charts, no date range, no
    pagination — reopen this design before adding a sixth field."""
    employee: object       # employees.EmployeeDefinition
    mission: str
    status: str
    outcomes: List[Tuple[str, int]]
    activity: List[metrics.ActivityRow]


def _labeled(raw: dict) -> List[Tuple[str, int]]:
    return [(METRIC_LABELS[key], value) for key, value in raw.items()]


def build_department_workspace(session, business_id: int, department_key: str,
                               since=None) -> Optional[DepartmentWorkspace]:
    """The one assembly point. Returns None for an unknown department key or
    one that isn't active for this business — the route 404s on either,
    rather than rendering a workspace for a department the customer doesn't
    have (you open your own office, not a directory of ones you might rent).
    """
    department = get_department(department_key)
    if department is None:
        return None

    from sqlmodel import select

    employees = session.exec(
        select(Employee).where(Employee.business_id == business_id)
    ).all()
    status = next(
        (s for s in department_status_for(employees) if s.department.key == department_key),
        None,
    )
    if status is None or status.state not in ACTIVE_STATES:
        return None

    role_keys = [e.key for e in status.staffed]
    employee_views = [
        EmployeeView(
            role_key=e.key,
            display_name=e.display_name,
            outcomes=_labeled(metrics.employee_outcomes(session, business_id, e.key, since=since)),
            activity=metrics.employee_activity(session, business_id, e.key, limit=5, since=since),
        )
        for e in status.staffed
    ]

    return DepartmentWorkspace(
        department=department,
        question=department.question,
        is_active=True,
        health_label=CUSTOMER_STATE_LABELS[status.state],
        employees=employee_views,
        outcomes=_labeled(metrics.department_outcomes(session, business_id, department_key, role_keys)),
        activity=metrics.department_activity(session, business_id, role_keys, since=since),
    )


def build_employee_workspace(session, business_id: int, department_key: str,
                             role_key: str, since=None) -> Optional[EmployeeWorkspace]:
    """DepartmentWorkspace -> EmployeeWorkspace: derived from the parent
    workspace rather than a second independent lookup, so "is this employee
    actually working" is decided exactly once, by build_department_workspace.
    This page can never disagree with the department page it was reached from.

    Returns None for an unknown department, one this business hasn't deployed,
    a role not staffed in it, or a role that belongs to a DIFFERENT department
    — the route 404s on any of these.
    """
    dept_ws = build_department_workspace(session, business_id, department_key, since=since)
    if dept_ws is None:
        return None
    view = next((e for e in dept_ws.employees if e.role_key == role_key), None)
    if view is None:
        return None

    definition = _EMPLOYEE_BY_KEY.get(role_key)
    return EmployeeWorkspace(
        employee=definition,
        mission=definition.mission if definition else "",
        status=EMPLOYEE_STATUS_LABEL,
        outcomes=view.outcomes,
        # The department page's employee card only previews 3-5 rows (Task 6);
        # this page IS the drill-down, so it re-fetches the employee's full
        # activity rather than reusing the capped preview in `view`.
        activity=metrics.employee_activity(session, business_id, role_key, limit=50, since=since),
    )
