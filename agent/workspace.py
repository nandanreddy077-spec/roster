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
