import json

from db import _backfill_employees
from db_models import Business, Employee
from sqlmodel import Session, select


def test_live_business_gets_active_frontdesk(test_engine):
    with Session(test_engine) as s:
        s.add(Business(business_name="B", trade="hvac", email="emp@test.io", frontdesk_live=True))
        s.commit()
    _backfill_employees(test_engine)
    with Session(test_engine) as s:
        b = s.exec(select(Business).where(Business.email == "emp@test.io")).first()
        emps = s.exec(select(Employee).where(Employee.business_id == b.id)).all()
        assert any(e.role_key == "frontdesk" and e.status == "active" for e in emps)


def test_requested_roster_becomes_employees(test_engine):
    with Session(test_engine) as s:
        s.add(
            Business(
                business_name="B",
                trade="hvac",
                email="emp2@test.io",
                frontdesk_live=True,
                requested_roster=json.dumps(["Quote Chaser"]),
            )
        )
        s.commit()
    _backfill_employees(test_engine)
    with Session(test_engine) as s:
        b = s.exec(select(Business).where(Business.email == "emp2@test.io")).first()
        keys = {
            e.role_key for e in s.exec(select(Employee).where(Employee.business_id == b.id)).all()
        }
        assert "frontdesk" in keys and "quote_chaser" in keys


def test_backfill_employees_idempotent(test_engine):
    with Session(test_engine) as s:
        s.add(Business(business_name="B", trade="hvac", email="emp3@test.io", frontdesk_live=True))
        s.commit()
    _backfill_employees(test_engine)
    _backfill_employees(test_engine)
    with Session(test_engine) as s:
        b = s.exec(select(Business).where(Business.email == "emp3@test.io")).first()
        fds = [
            e
            for e in s.exec(select(Employee).where(Employee.business_id == b.id)).all()
            if e.role_key == "frontdesk"
        ]
        assert len(fds) == 1


def test_retention_manager_maps_to_canonical_key(test_engine):
    with Session(test_engine) as s:
        s.add(
            Business(
                business_name="B",
                trade="hvac",
                email="emp4@test.io",
                frontdesk_live=True,
                requested_roster=json.dumps(["Retention Manager"]),
            )
        )
        s.commit()
    _backfill_employees(test_engine)
    with Session(test_engine) as s:
        b = s.exec(select(Business).where(Business.email == "emp4@test.io")).first()
        keys = {
            e.role_key for e in s.exec(select(Employee).where(Employee.business_id == b.id)).all()
        }
        assert "retention" in keys and "retention_manager" not in keys
