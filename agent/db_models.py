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
    review_link: Optional[str] = None  # owner's Google/Yelp review URL; unset until they provide one
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
    completed_at: Optional[datetime] = None  # set by the "Mark done" action; drives the Reviews SMS


class RecoveryCampaign(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="client.id")
    face: str  # "quote" or "reactivation"
    name: str
    customer_list_json: str  # JSON list of {phone, name, service_type, estimate_amount, days_since}
    template_overrides_json: str = "{}"  # JSON map of message_day (str) -> custom text
    started_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def customer_list(self) -> List[dict]:
        return json.loads(self.customer_list_json)

    @property
    def template_overrides(self) -> dict:
        return json.loads(self.template_overrides_json)


class RecoveryJob(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="recoverycampaign.id")
    client_id: int = Field(foreign_key="client.id")
    customer_phone: str
    customer_name: Optional[str] = None
    service_type: str
    estimate_amount: Optional[str] = None
    days_since: Optional[str] = None
    anchor_date: Optional[str] = None  # ISO YYYY-MM-DD; only set for the "membership" face
    current_status: str = "pending"  # pending, awaiting_slot, booked, declined, no_response
    last_sent_day: Optional[int] = None
    offered_slots_json: str = "[]"
    booked_job_id: Optional[int] = Field(default=None, foreign_key="job.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def offered_slots(self) -> List[str]:
        return json.loads(self.offered_slots_json)


class RecoveryMessageLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    recovery_job_id: int = Field(foreign_key="recoveryjob.id")
    message_day: int
    message_text: str
    sent_at: datetime = Field(default_factory=datetime.utcnow)
    customer_reply: Optional[str] = None
    replied_at: Optional[datetime] = None
