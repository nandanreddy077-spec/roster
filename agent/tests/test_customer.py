import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from db_models import Business, Customer
from repositories import get_or_create_customer


def test_get_or_create_idempotent(session):
    b = Business(business_name="B", trade="hvac", email="c-idem@test.io")
    session.add(b)
    session.commit()
    session.refresh(b)
    c1 = get_or_create_customer(session, b.id, "+15551234567", name="Pat")
    c2 = get_or_create_customer(session, b.id, "+15551234567")
    assert c1.id == c2.id and c1.name == "Pat"


def test_same_phone_distinct_per_business(session):
    b1 = Business(business_name="B", trade="hvac", email="c-b1@test.io")
    b2 = Business(business_name="B", trade="hvac", email="c-b2@test.io")
    session.add(b1)
    session.add(b2)
    session.commit()
    session.refresh(b1)
    session.refresh(b2)
    a = get_or_create_customer(session, b1.id, "+15550000000")
    b = get_or_create_customer(session, b2.id, "+15550000000")
    assert a.id != b.id


def test_schema_enforces_unique_business_phone(session):
    b = Business(business_name="B", trade="hvac", email="c-uniq@test.io")
    session.add(b)
    session.commit()
    session.refresh(b)
    session.add(Customer(business_id=b.id, phone="+15559999999"))
    session.commit()
    session.add(Customer(business_id=b.id, phone="+15559999999"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
