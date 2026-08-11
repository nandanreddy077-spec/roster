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
from expansion import mark_actioned, open_interests_for, record_interest


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


# --- record_interest ---------------------------------------------------------


def test_record_interest_creates_an_open_request(session):
    row = record_interest(session, business_id=1, department_key="finance")

    assert row.id is not None
    assert row.department_key == "finance"
    assert row.actioned_at is None


def test_asking_twice_returns_the_same_open_request(session):
    """A customer double-clicking, or coming back a week later while the
    first request is still open, must not put two rows in front of ops."""
    first = record_interest(session, business_id=1, department_key="finance")
    second = record_interest(session, business_id=1, department_key="finance")

    assert first.id == second.id
    assert len(session.exec(select(DepartmentInterest)).all()) == 1


def test_record_interest_rejects_an_unknown_department(session):
    """H5: department_key is attacker-controllable — it arrives from a
    customer-facing POST in Phase 5."""
    with pytest.raises(ValueError):
        record_interest(session, business_id=1, department_key="not_a_department")


def test_record_interest_rejects_leadership(session):
    """Leadership is included automatically with any active department and is
    never sold (blueprint §4a). Accepting interest in it would put a nonsense
    row in front of ops."""
    with pytest.raises(ValueError):
        record_interest(session, business_id=1, department_key="leadership")


def test_one_business_cannot_record_interest_against_another(session):
    """Business isolation is the security boundary everywhere in Roster
    (platform PRD §12). Two businesses asking for the same department produce
    two independent rows, and neither can see or block the other."""
    a = record_interest(session, business_id=1, department_key="finance")
    b = record_interest(session, business_id=2, department_key="finance")

    assert a.id != b.id
    assert a.business_id == 1
    assert b.business_id == 2


def test_a_concurrent_duplicate_resolves_to_the_existing_row(session, monkeypatch):
    """H3: simulates the interleaving the partial index exists to catch — two
    threads both pass the 'is there an open request?' check, then both
    insert. The loser must return the winner's row, not raise."""
    import expansion

    real_open = expansion._open_interest
    calls = {"n": 0}

    def _pretend_nothing_exists(*args, **kwargs):
        # First call only: report "no open request" even though one exists,
        # exactly as a racing thread would have seen before the winner
        # committed.
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return real_open(*args, **kwargs)

    winner = record_interest(session, business_id=1, department_key="finance")
    monkeypatch.setattr(expansion, "_open_interest", _pretend_nothing_exists)

    loser = record_interest(session, business_id=1, department_key="finance")

    assert loser.id == winner.id
    assert len(session.exec(select(DepartmentInterest)).all()) == 1


def test_recording_interest_deploys_nothing(session):
    """H1 — the permanent guard for this phase.

    Business.requested_roster is what runner.is_active() reads to decide
    whether an employee is LIVE. Interest must never touch it, or a customer
    asking a question would silently deploy an employee — recreating the exact
    conflation this migration exists to remove."""
    from db_models import Employee

    biz = Business(business_name="B", trade="hvac", email="exp-guard@test.io")
    session.add(biz)
    session.commit()
    session.refresh(biz)

    record_interest(session, business_id=biz.id, department_key="finance")
    session.refresh(biz)

    assert biz.requested_roster is None
    assert session.exec(select(Employee).where(Employee.business_id == biz.id)).all() == []


# --- the ops seam Phase 4 consumes -------------------------------------------


def test_open_interests_are_oldest_first(session):
    """A work queue, not a feed: ops should handle the request that has been
    waiting longest. (Deliberately the opposite of recent_notifications,
    which is newest-first because it's something to read, not to work.)"""
    first = record_interest(session, 1, "finance")
    second = record_interest(session, 1, "marketing")

    assert [i.id for i in open_interests_for(session, 1)] == [first.id, second.id]


def test_open_interests_never_leak_another_business(session):
    record_interest(session, 1, "finance")
    record_interest(session, 2, "marketing")

    assert [i.department_key for i in open_interests_for(session, 1)] == ["finance"]


def test_open_interests_excludes_actioned_requests(session):
    handled = record_interest(session, 1, "finance")
    record_interest(session, 1, "marketing")

    mark_actioned(session, handled.id)

    assert [i.department_key for i in open_interests_for(session, 1)] == ["marketing"]


def test_mark_actioned_sets_the_timestamp(session):
    row = record_interest(session, 1, "finance")

    actioned = mark_actioned(session, row.id)

    assert actioned.actioned_at is not None


def test_mark_actioned_is_idempotent(session):
    """Ops double-clicking 'handled' must not move the timestamp or error."""
    row = record_interest(session, 1, "finance")

    first = mark_actioned(session, row.id)
    when = first.actioned_at
    second = mark_actioned(session, row.id)

    assert second.actioned_at == when


def test_mark_actioned_returns_none_for_an_unknown_id(session):
    assert mark_actioned(session, 99999) is None


def test_a_customer_can_ask_again_after_ops_actioned_the_request(session):
    """End-to-end of the partial index's purpose: declined in March, asks
    again in June, and ops sees a NEW request rather than a stale one."""
    first = record_interest(session, 1, "finance")
    mark_actioned(session, first.id)

    second = record_interest(session, 1, "finance")

    assert second.id != first.id
    assert [i.id for i in open_interests_for(session, 1)] == [second.id]


def test_actioning_a_request_deploys_nothing(session):
    """THE lifecycle invariant (founder, 2026-07-29).

    Actioning means "operations handled the request" — NOT "the department
    was deployed". Deployment is a separate operation that arrives in Phase 4
    and produces Employee rows. Walking the full lifecycle here
    (record -> action -> still nothing deployed) is what stops a future edit
    from making mark_actioned() a shortcut that provisions, which would
    recreate the requested_roster conflation (H1) one level up.

    Asserts through Phase 1's own helper, so the customer-visible answer —
    "which departments does this business have?" — is what's being checked,
    not merely the absence of rows."""
    from db_models import Employee
    from departments import active_departments_for

    biz = Business(business_name="B", trade="hvac", email="lifecycle@test.io")
    session.add(biz)
    session.commit()
    session.refresh(biz)

    interest = record_interest(session, biz.id, "finance")
    mark_actioned(session, interest.id)
    session.refresh(biz)

    employees = session.exec(select(Employee).where(Employee.business_id == biz.id)).all()
    assert employees == []
    assert active_departments_for(employees) == []
    assert biz.requested_roster is None
