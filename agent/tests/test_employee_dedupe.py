"""The employee uniqueness migration.

audit F2 (verified empirically): create_all() does NOT add an index to a table
that already exists, so the constraint needs real DDL for existing databases —
and that DDL can only run once duplicates are gone.

audit F3: nothing prevents duplicates today. _hire_employee is a
SELECT-then-INSERT with no constraint behind it, and FastAPI runs sync handlers
in a threadpool even at --workers 1."""

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from db import _dedupe_employees, _migrate_add_indexes
from db_models import Business, Employee

INDEX_NAME = "uq_employee_business_role"


def _business(session, email):
    b = Business(business_name="B", trade="hvac", email=email)
    session.add(b)
    session.commit()
    session.refresh(b)
    return b


def _drop_index(engine):
    """The test engine's create_all builds the unique index from the model, so
    duplicates can't be inserted until it's dropped. Dropping it is how a test
    reproduces a PRE-migration database — the only state the migration exists
    to fix."""
    with engine.connect() as conn:
        conn.execute(text(f"DROP INDEX IF EXISTS {INDEX_NAME}"))
        conn.commit()


def test_dedupe_keeps_the_oldest_row_and_reports_what_it_removed(test_engine):
    """Deterministic rule: keep the LOWEST id per (business_id, role_key).
    Nothing has ever written Employee.status (audit: no pause/resume/fire
    exists), so no duplicate can carry state worth preserving over another —
    oldest-wins is safe as well as deterministic."""
    _drop_index(test_engine)
    with Session(test_engine) as s:
        b = _business(s, "dedupe1@test.io")
        keep = Employee(business_id=b.id, role_key="frontdesk", display_name="first")
        s.add(keep)
        s.commit()
        s.refresh(keep)
        s.add(Employee(business_id=b.id, role_key="frontdesk", display_name="second"))
        s.add(Employee(business_id=b.id, role_key="frontdesk", display_name="third"))
        s.commit()
        business_id, keep_id = b.id, keep.id

    removed = _dedupe_employees(test_engine)

    assert len(removed) == 2
    with Session(test_engine) as s:
        rows = s.exec(select(Employee).where(Employee.business_id == business_id)).all()
        assert [r.id for r in rows] == [keep_id]
        assert rows[0].display_name == "first"


def test_dedupe_leaves_distinct_roles_alone(test_engine):
    with Session(test_engine) as s:
        b = _business(s, "dedupe2@test.io")
        s.add(Employee(business_id=b.id, role_key="frontdesk"))
        s.add(Employee(business_id=b.id, role_key="quote_chaser"))
        s.commit()
        business_id = b.id

    assert _dedupe_employees(test_engine) == []

    with Session(test_engine) as s:
        assert len(s.exec(select(Employee).where(Employee.business_id == business_id)).all()) == 2


def test_dedupe_never_merges_across_businesses(test_engine):
    """I12: the same role_key at two businesses is not a duplicate."""
    with Session(test_engine) as s:
        a = _business(s, "dedupe3a@test.io")
        b = _business(s, "dedupe3b@test.io")
        s.add(Employee(business_id=a.id, role_key="frontdesk"))
        s.add(Employee(business_id=b.id, role_key="frontdesk"))
        s.commit()

    assert _dedupe_employees(test_engine) == []

    with Session(test_engine) as s:
        assert len(s.exec(select(Employee)).all()) == 2


def test_dedupe_is_idempotent(test_engine):
    _drop_index(test_engine)
    with Session(test_engine) as s:
        b = _business(s, "dedupe4@test.io")
        s.add(Employee(business_id=b.id, role_key="frontdesk"))
        s.add(Employee(business_id=b.id, role_key="frontdesk"))
        s.commit()

    first = _dedupe_employees(test_engine)
    second = _dedupe_employees(test_engine)

    assert len(first) == 1
    assert second == []


def test_the_index_migration_creates_the_unique_index(test_engine):
    """audit F2: this is the step create_all() cannot do for an existing
    table. Asserting the index EXISTS by name is what proves the DDL ran."""
    _drop_index(test_engine)
    assert INDEX_NAME not in {i["name"] for i in inspect(test_engine).get_indexes("employee")}

    _migrate_add_indexes(test_engine)

    assert INDEX_NAME in {i["name"] for i in inspect(test_engine).get_indexes("employee")}


def test_the_index_migration_is_idempotent(test_engine):
    _migrate_add_indexes(test_engine)
    _migrate_add_indexes(test_engine)  # must not raise

    assert INDEX_NAME in {i["name"] for i in inspect(test_engine).get_indexes("employee")}


def test_the_database_rejects_a_duplicate_employee(test_engine):
    """I3. The whole point of the migration: after it, a duplicate is
    impossible rather than merely unlikely."""
    _migrate_add_indexes(test_engine)

    with Session(test_engine) as s:
        b = _business(s, "dupe@test.io")
        s.add(Employee(business_id=b.id, role_key="frontdesk"))
        s.commit()

        s.add(Employee(business_id=b.id, role_key="frontdesk"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_init_db_is_idempotent_against_pre_existing_duplicates(test_engine, monkeypatch):
    """The whole migration, run twice against a database that already contains
    duplicates — the exact state a real deploy will meet (founder request).

    Second run must prove: nothing removed, nothing recreated, index still
    present, no errors."""
    import db

    monkeypatch.setattr(db, "engine", test_engine)
    _drop_index(test_engine)
    with Session(test_engine) as s:
        b = _business(s, "initdup@test.io")
        s.add(Employee(business_id=b.id, role_key="frontdesk", display_name="first"))
        s.add(Employee(business_id=b.id, role_key="frontdesk", display_name="second"))
        s.add(Employee(business_id=b.id, role_key="quote_chaser"))
        s.commit()
        business_id = b.id

    db._init_db_locked()

    with Session(test_engine) as s:
        after_first = [
            (e.id, e.role_key, e.display_name)
            for e in s.exec(
                select(Employee).where(Employee.business_id == business_id).order_by(Employee.id)
            ).all()
        ]
    assert [r[1] for r in after_first] == ["frontdesk", "quote_chaser"]
    assert after_first[0][2] == "first"  # the oldest duplicate survived
    assert INDEX_NAME in {i["name"] for i in inspect(test_engine).get_indexes("employee")}

    db._init_db_locked()  # second run — must be a complete no-op

    with Session(test_engine) as s:
        after_second = [
            (e.id, e.role_key, e.display_name)
            for e in s.exec(
                select(Employee).where(Employee.business_id == business_id).order_by(Employee.id)
            ).all()
        ]
    assert after_second == after_first  # nothing removed, nothing recreated
    assert INDEX_NAME in {i["name"] for i in inspect(test_engine).get_indexes("employee")}
