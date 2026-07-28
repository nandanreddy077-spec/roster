"""Expansion interest: an existing customer asking for one more department.

Strictly a REQUEST record. It must never imply deployment — that conflation
is exactly what Business.requested_roster gets wrong today (runner.is_active
treats "requested" as "live"), and it is what the department migration exists
to undo."""
from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from db_models import Business, DepartmentInterest


def test_a_new_interest_starts_open(session):
    row = DepartmentInterest(business_id=1, department_key="finance")
    session.add(row)
    session.commit()
    session.refresh(row)
    assert row.actioned_at is None
    assert row.created_at is not None


def test_the_database_rejects_a_second_open_interest_in_one_department(session):
    """H3: de-duplication is a database guarantee, not an application check.
    A double-clicked button submits twice concurrently — on SQLite through
    FastAPI's threadpool, on Postgres across worker processes — and a
    SELECT-then-INSERT would let both through."""
    session.add(DepartmentInterest(business_id=1, department_key="finance"))
    session.commit()

    session.add(DepartmentInterest(business_id=1, department_key="finance"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_two_businesses_may_each_have_open_interest_in_the_same_department(session):
    """The index is scoped by business_id — one business's request must never
    block another's."""
    session.add(DepartmentInterest(business_id=1, department_key="finance"))
    session.add(DepartmentInterest(business_id=2, department_key="finance"))
    session.commit()

    assert len(session.exec(select(DepartmentInterest)).all()) == 2


def test_a_business_may_ask_again_once_the_previous_request_is_actioned(session):
    """The index is PARTIAL (WHERE actioned_at IS NULL) precisely so an
    actioned row leaves it. A customer declined in March can ask again in
    June without ops having to delete history."""
    first = DepartmentInterest(business_id=1, department_key="finance")
    session.add(first)
    session.commit()

    first.actioned_at = datetime.utcnow()
    session.add(first)
    session.commit()

    session.add(DepartmentInterest(business_id=1, department_key="finance"))
    session.commit()  # must not raise

    assert len(session.exec(select(DepartmentInterest)).all()) == 2
