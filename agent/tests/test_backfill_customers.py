from db import _backfill_customers
from db_models import Business, Customer, Job
from sqlmodel import Session, select


def test_backfill_creates_and_links(test_engine):
    with Session(test_engine) as s:
        b = Business(business_name="B", trade="hvac", email="mig@test.io")
        s.add(b)
        s.commit()
        s.refresh(b)
        s.add(
            Job(
                business_id=b.id,
                customer_phone="+15557778888",
                service_type="AC",
                urgency="routine",
            )
        )
        s.commit()
    _backfill_customers(test_engine)
    with Session(test_engine) as s:
        cust = s.exec(select(Customer).where(Customer.phone == "+15557778888")).first()
        assert cust is not None
        job = s.exec(select(Job).where(Job.customer_phone == "+15557778888")).first()
        assert job.customer_id == cust.id


def test_backfill_is_idempotent(test_engine):
    with Session(test_engine) as s:
        b = Business(business_name="B", trade="hvac", email="mig2@test.io")
        s.add(b)
        s.commit()
        s.refresh(b)
        s.add(
            Job(
                business_id=b.id,
                customer_phone="+15551112222",
                service_type="AC",
                urgency="routine",
            )
        )
        s.commit()
    _backfill_customers(test_engine)
    _backfill_customers(test_engine)  # second run must not create a duplicate Customer
    with Session(test_engine) as s:
        custs = s.exec(select(Customer).where(Customer.phone == "+15551112222")).all()
        assert len(custs) == 1
