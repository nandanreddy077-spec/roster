"""Verification for the SQLite -> Postgres migration (docs/PRODUCTION_READINESS.md
P0-3). The migration targets Postgres — identity sequences, dialect quoting,
string-timestamp coercion — so a faithful test needs a real Postgres target,
not a second SQLite file (whose DateTime columns reject the ISO strings raw
sqlite3 hands back, a strictness Postgres does not have).

Set ROSTER_TEST_DATABASE_URL to a scratch Postgres to run this — the same hook
conftest.test_engine already uses to run the whole suite on Postgres, and the
exact check to run before the production cutover. Without it these skip.

What is covered: every table copied, timestamps + FKs + Twilio identifiers
preserved, identity sequences advanced past the copied ids (the classic bug —
SQLite ids are plain values, Postgres ids are sequence-backed, and skipping
the reset means the first insert after cutover collides with row 1), a
non-empty target refused, and a count mismatch raising loudly.
"""

import os
from datetime import datetime
from pathlib import Path

import pytest
from db_models import Business, Customer, Job, SQLModel
from migrate_to_postgres import MigrationError, copy_rows, migrate, verify_migration
from sqlalchemy import create_engine, inspect, text
from sqlmodel import Session, select

_PG_URL = os.environ.get("ROSTER_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not _PG_URL.startswith("postgresql"),
    reason="needs a scratch Postgres in ROSTER_TEST_DATABASE_URL (see docstring)",
)


@pytest.fixture
def pg_engine():
    eng = create_engine(_PG_URL)
    SQLModel.metadata.drop_all(eng)
    yield eng
    SQLModel.metadata.drop_all(eng)
    eng.dispose()


def _seed_sqlite(path: Path) -> dict:
    eng = create_engine(f"sqlite:///{path}")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        biz = Business(
            business_name="Kestrel HVAC",
            trade="HVAC",
            inbound_number="+15125550100",
            twilio_number_sid="PN123",
            xai_signing_secret="whsec_abc",
            sms_delivery_status="active",
            created_at=datetime(2026, 1, 2, 3, 4, 5),
        )
        s.add(biz)
        s.commit()
        s.refresh(biz)
        cust = Customer(business_id=biz.id, phone="+15125559999", name="Dana")
        s.add(cust)
        s.commit()
        s.refresh(cust)
        s.add(
            Job(
                business_id=biz.id,
                customer_id=cust.id,
                customer_phone="+15125559999",
                service_type="AC repair",
                urgency="same_day",
                created_at=datetime(2026, 1, 3, 0, 0, 0),
            )
        )
        s.commit()
    eng.dispose()
    return {"business": 1, "customer": 1, "job": 1}


def test_migrate_copies_rows_preserves_relationships_and_resets_sequences(tmp_path, pg_engine):
    src = tmp_path / "source.db"
    expected = _seed_sqlite(src)

    before, after = migrate(src, _PG_URL)
    for table, n in expected.items():
        assert before[table] == after[table] == n

    with Session(pg_engine) as s:
        biz = s.exec(select(Business)).one()
        assert biz.twilio_number_sid == "PN123"
        assert biz.xai_signing_secret == "whsec_abc"
        assert biz.created_at == datetime(2026, 1, 2, 3, 4, 5)
        job = s.exec(select(Job)).one()
        assert job.business_id == biz.id and job.customer_id is not None

        # The sequence bug: the next insert must not collide with row 1.
        s.add(Business(business_name="Second", trade="Plumbing"))
        s.commit()
        assert s.exec(select(Business)).all()[-1].id > biz.id


def test_migrate_refuses_a_non_empty_target(tmp_path, pg_engine):
    src = tmp_path / "source.db"
    _seed_sqlite(src)
    migrate(src, _PG_URL)
    with pytest.raises(MigrationError, match="non-empty"):
        migrate(src, _PG_URL)


def test_migrate_fails_loudly_when_counts_disagree(tmp_path, pg_engine, monkeypatch):
    src = tmp_path / "source.db"
    _seed_sqlite(src)

    def _lossy_copy(sqlite_path, engine):
        copy_rows(sqlite_path, engine)
        with engine.begin() as conn:
            conn.execute(text('DELETE FROM "job"'))
        return {}

    monkeypatch.setattr("migrate_to_postgres.copy_rows", _lossy_copy)
    with pytest.raises(MigrationError, match="row count differs"):
        migrate(src, _PG_URL)


def test_columns_not_on_the_model_are_dropped(tmp_path, pg_engine):
    src = tmp_path / "source.db"
    _seed_sqlite(src)
    e = create_engine(f"sqlite:///{src}")
    with e.begin() as conn:
        conn.execute(text("ALTER TABLE business ADD COLUMN dead_field VARCHAR"))
    e.dispose()

    migrate(src, _PG_URL)
    assert "dead_field" not in {c["name"] for c in inspect(pg_engine).get_columns("business")}


def test_verify_migration_is_usable_standalone(tmp_path, pg_engine):
    src = tmp_path / "source.db"
    _seed_sqlite(src)
    migrate(src, _PG_URL)
    before, after, problems = verify_migration(src, pg_engine)
    assert problems == [] and before == after
