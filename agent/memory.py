from typing import List, Optional
from sqlmodel import Session, select
from db_models import Business, Customer, Message
from repositories import get_or_create_customer


class BusinessMemory:
    """Port scoped to ONE business. Business-scoped isolation IS the security
    boundary (prevents cross-tenant leakage / prompt-injection exfiltration).
    v1 body is relational; recall() is a recency stub — semantic swap later,
    same signature, no caller changes. Queries `Message` (renamed to `Interaction` in Phase 2)."""

    def __init__(self, business_id: int, engine=None):
        from db import engine as _default
        self.business_id = business_id
        self._engine = engine or _default

    def profile(self) -> Optional[Business]:
        with Session(self._engine) as s:
            return s.get(Business, self.business_id)

    def get_customer(self, phone: str) -> Optional[Customer]:
        with Session(self._engine) as s:
            return s.exec(select(Customer).where(
                Customer.business_id == self.business_id, Customer.phone == phone)).first()

    def upsert_customer(self, phone: str, name: Optional[str] = None) -> Customer:
        with Session(self._engine) as s:
            return get_or_create_customer(s, self.business_id, phone, name)

    def timeline(self, customer_id: int) -> List[Message]:
        with Session(self._engine) as s:
            return s.exec(select(Message).where(
                Message.business_id == self.business_id, Message.customer_id == customer_id
            ).order_by(Message.id)).all()

    def recall(self, query: str, limit: int = 20) -> List[Message]:
        with Session(self._engine) as s:
            return list(reversed(s.exec(select(Message).where(
                Message.business_id == self.business_id).order_by(Message.id.desc()).limit(limit)).all()))
