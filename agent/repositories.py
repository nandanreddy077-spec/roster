from datetime import datetime
from typing import Optional
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select
from db_models import Customer


def get_or_create_customer(session: Session, business_id: int, phone: str, name: Optional[str] = None) -> Customer:
    existing = session.exec(
        select(Customer).where(Customer.business_id == business_id, Customer.phone == phone)
    ).first()
    if existing:
        if name and not existing.name:
            existing.name = name
            existing.updated_at = datetime.utcnow()
            session.add(existing)
            session.commit()
            session.refresh(existing)
        return existing
    c = Customer(business_id=business_id, phone=phone, name=name)
    session.add(c)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return session.exec(
            select(Customer).where(Customer.business_id == business_id, Customer.phone == phone)
        ).first()
    session.refresh(c)
    return c
