"""The single path that puts an AI employee to work for a business.

Before this module there were three Employee writers and two paths that marked
a business "deployed" without creating a row at all (audit F1) — so a business
showed zero departments until the app restarted, because the customer
dashboard reads those rows.

Deployment is idempotent by design rather than by luck: a caller can re-run it
to finish a half-completed deploy, and the database's unique index makes a
duplicate impossible even under a concurrent double-submit.
"""
from typing import List, Optional

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from db_models import Employee
from departments import canonical_role_key, deployable_employees_for, get_department
from employees import REGISTRY as _EMPLOYEE_REGISTRY

_BY_KEY = {e.key: e for e in _EMPLOYEE_REGISTRY}


def _existing(session, business_id: int, role_key: str) -> Optional[Employee]:
    return session.exec(
        select(Employee).where(
            Employee.business_id == business_id, Employee.role_key == role_key
        )
    ).first()


def deploy_role(session, business_id: int, role_key: str) -> Optional[Employee]:
    """Put one employee to work. Returns the newly-created row, or None if it
    was already deployed — so a caller can distinguish a fresh deployment from
    a repeat and re-run safely to finish a partial one.

    Raises ValueError for an unknown role, or one whose registry status is
    `planned`: those have no engine, and a row for one would make the
    department look staffed while it cannot do any work (audit F4).
    """
    definition = _BY_KEY.get(canonical_role_key(role_key))
    if definition is None:
        raise ValueError(f"unknown role: {role_key!r}")
    if definition.status == "planned":
        raise ValueError(
            f"{role_key!r} is planned, not deployable — it has no engine yet"
        )

    if _existing(session, business_id, role_key) is not None:
        return None

    row = Employee(
        business_id=business_id, role_key=role_key,
        display_name=definition.display_name,
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        # Lost a concurrent double-submit; the unique index rejected us and the
        # winner's row is the one that counts.
        session.rollback()
        return None
    session.refresh(row)
    return row


def deploy_department(session, business_id: int, department_key: str) -> List[Employee]:
    """Staff a whole department. Returns only the rows NEWLY created, so
    re-running to complete a partial deployment reports just the gap it filled.

    Raises ValueError for an unknown department, a non-hireable one
    (Leadership), or one with no deployable employees — Operations, Finance and
    Marketing have none today, and silently doing nothing would let the ops
    console claim it deployed vaporware.
    """
    department = get_department(department_key)
    if department is None:
        raise ValueError(f"unknown department: {department_key!r}")
    if not department.hireable:
        raise ValueError(f"{department_key!r} is not a hireable department")

    deployable = deployable_employees_for(department_key)
    if not deployable:
        raise ValueError(
            f"{department_key!r} has no deployable employees yet — "
            "every role in it is still `planned`"
        )

    created = []
    for definition in deployable:
        row = deploy_role(session, business_id, definition.key)
        if row is not None:
            created.append(row)
    return created
