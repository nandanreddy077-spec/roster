"""Expansion interest — an existing customer asking for one more department.

Recording interest DEPLOYS NOTHING. It creates no Employee, writes no
requested_roster, and has no effect on runner.is_active(). Roster provisions
after a discovery call (blueprint §10a), so "the customer asked" and "we
deployed" are deliberately separate, separately-observable facts. The existing
requested_roster blob fails to make that distinction (runner.is_active treats
"requested" as "live"); this module must not repeat it.

Kept out of departments.py on purpose: that module is a pure registry with no
database dependency, and its tests need no fixture. This one owns the writes.
"""

from datetime import datetime
from typing import List, Optional

from db_models import DepartmentInterest
from departments import get_department
from sqlalchemy.exc import IntegrityError
from sqlmodel import select


def _open_interest(session, business_id: int, department_key: str) -> Optional[DepartmentInterest]:
    return session.exec(
        select(DepartmentInterest).where(
            DepartmentInterest.business_id == business_id,
            DepartmentInterest.department_key == department_key,
            DepartmentInterest.actioned_at.is_(None),
        )
    ).first()


def record_interest(session, business_id: int, department_key: str) -> DepartmentInterest:
    """Record that this business wants `department_key`, or return the request
    already open for it.

    Raises ValueError for an unknown or non-hireable department: this value
    arrives from a customer-facing POST, so it is validated at the boundary
    rather than trusted (Leadership is never sold — blueprint §4a).

    Concurrency: the "is one already open?" check and the insert are not
    atomic, so two racing submissions can both reach the insert. The partial
    unique index makes the database reject the loser, which then returns the
    winner's row — the same try/catch/re-select pattern used by
    repositories.get_or_create_customer and eventbus.publish.
    """
    department = get_department(department_key)
    if department is None:
        raise ValueError(f"unknown department: {department_key!r}")
    if not department.hireable:
        raise ValueError(f"{department_key!r} is not a hireable department")

    existing = _open_interest(session, business_id, department_key)
    if existing is not None:
        return existing

    row = DepartmentInterest(business_id=business_id, department_key=department_key)
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        # Lost the race: another submission created the open request between
        # our check and our insert. Return theirs.
        session.rollback()
        winner = _open_interest(session, business_id, department_key)
        if winner is None:
            # Would require the winning row to be actioned within microseconds
            # of being created. Fail loudly rather than return a lie.
            raise
        return winner
    session.refresh(row)
    return row


def open_interests_for(session, business_id: int) -> List[DepartmentInterest]:
    """This business's unactioned expansion requests, oldest first.

    Oldest-first because this is a work queue for ops (blueprint §10a's
    "ongoing customer management" stage) — the request waiting longest is the
    one to handle next. Scoped to one business_id: business isolation is the
    security boundary everywhere in Roster (platform PRD §12).
    """
    return list(
        session.exec(
            select(DepartmentInterest)
            .where(
                DepartmentInterest.business_id == business_id,
                DepartmentInterest.actioned_at.is_(None),
            )
            .order_by(DepartmentInterest.id)
        ).all()
    )


def mark_actioned(session, interest_id: int) -> Optional[DepartmentInterest]:
    """Close one expansion request — ops has handled it, whether that meant
    deploying the department or declining.

    Handled is NOT deployed: this sets a timestamp and nothing else. It
    creates no Employee and changes no department's live status; deployment is
    a separate founder action (Phase 4). Returns the row, or None if there is
    no such request.

    Idempotent: re-closing an already-closed request keeps the original
    timestamp, so a double-click in the admin UI can't rewrite history.

    Closing also frees the partial unique index, which is what lets the same
    customer ask for that department again later.
    """
    row = session.get(DepartmentInterest, interest_id)
    if row is None:
        return None
    if row.actioned_at is None:
        row.actioned_at = datetime.utcnow()
        session.add(row)
        session.commit()
        session.refresh(row)
    return row
