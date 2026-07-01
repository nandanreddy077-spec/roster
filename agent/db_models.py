import json
from datetime import datetime
from typing import List, Optional

from sqlmodel import Field, SQLModel

from models import ClientConfig


class Client(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    business_name: str
    trade: str
    services_json: str  # JSON-encoded list[str]
    hours: str
    pricing_faq: str
    escalation_phone: str
    answer_mode: str = Field(default="backup")  # "primary" or "backup" - set during onboarding
    inbound_number: Optional[str] = None  # the business line customers text/call; routes inbound SMS
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def services(self) -> List[str]:
        return json.loads(self.services_json)

    def to_config(self) -> ClientConfig:
        return ClientConfig(
            client_id=str(self.id),
            business_name=self.business_name,
            trade=self.trade,
            services=self.services,
            hours=self.hours,
            pricing_faq=self.pricing_faq,
            escalation_phone=self.escalation_phone,
            answer_mode=self.answer_mode,
        )


class Message(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="client.id")
    customer_phone: str = "dashboard"  # threads a conversation per customer on a client's line
    role: str  # "user" or "assistant"
    content_json: str  # JSON-encoded content (str or list of content blocks)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Job(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="client.id")
    customer_phone: str = "dashboard"
    customer_name: Optional[str] = None
    service_type: str
    urgency: str
    address: Optional[str] = None
    callback_number: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
