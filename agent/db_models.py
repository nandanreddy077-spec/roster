import json
from datetime import datetime
from typing import List, Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from models import ClientConfig


class Business(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    business_name: str = ""
    trade: str = ""
    services_json: str = "[]"  # JSON-encoded list[str]
    hours: str = ""
    pricing_faq: str = ""
    escalation_phone: str = ""
    answer_mode: str = Field(default="backup")  # "primary" (AI picks up every call) or "backup" (AI catches only calls the owner misses) - asked during onboarding; drives the call-forwarding instructions
    business_phone: str = ""  # the number customers currently dial; the owner forwards it to inbound_number so calls reach the receptionist
    inbound_number: Optional[str] = None  # the business line customers text/call; routes inbound SMS
    xai_phone_number: Optional[str] = None  # number registered with xAI's Voice Agent API (see xai_voice_adapter.py); unset = no live-voice receptionist configured for this client yet
    xai_signing_secret: Optional[str] = None  # webhook signing secret returned when xai_phone_number was registered (per-number, not account-wide — see provisioning.py)
    twilio_number_sid: Optional[str] = None  # Twilio's SID for the purchased number, needed to later attach it to a SIP trunk
    review_link: Optional[str] = None  # owner's Google/Yelp review URL; unset until they provide one
    referral_incentive: Optional[str] = None  # e.g. "$25 off"; unset = referrals off for this client
    requested_roster: Optional[str] = None  # JSON list of extra roles queued for founder setup — self-serve "Hire" clicks on Quote Chaser/Retention Manager append here (see agent/roles.py), same mechanism the old /hire addons used
    email: Optional[str] = Field(default=None, unique=True, index=True)
    password_hash: Optional[str] = None
    tone: str = "professional and friendly"
    source: Optional[str] = None  # how the owner heard about Roster; asked from the dashboard, not at signup
    source_prompt_dismissed: bool = False
    frontdesk_live: bool = False
    activated_at: Optional[datetime] = None
    tested_at: Optional[datetime] = None  # set the first time an owner's dashboard test message gets a real reply back — this, not form submission, is what earns the honest "Working" status (see portal.py)
    trial_spend_cents: int = 0
    trial_cap_cents: int = 2000  # $20 hard cap
    trial_soft_buffer_cents: int = 200  # $2 grace on top of the hard cap — see trial_cap.py
    trial_cap_notified: bool = False  # set once the founder has been alerted the hard cap was crossed, so the alert fires only once
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
            tone=self.tone,
        )


class Customer(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("business_id", "phone", name="uq_customer_business_phone"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    business_id: int = Field(foreign_key="business.id", index=True)
    phone: str = Field(index=True)
    name: Optional[str] = None
    source: Optional[str] = None
    tags_json: str = "[]"
    first_seen_at: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def tags(self) -> List[str]:
        return json.loads(self.tags_json)


class Message(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    business_id: int = Field(foreign_key="business.id")
    customer_id: Optional[int] = Field(default=None, foreign_key="customer.id", index=True)
    customer_phone: str = "dashboard"  # threads a conversation per customer on a business's line
    role: str  # "user" or "assistant"
    content_json: str  # JSON-encoded content (str or list of content blocks)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Job(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    business_id: int = Field(foreign_key="business.id")
    customer_id: Optional[int] = Field(default=None, foreign_key="customer.id", index=True)
    customer_phone: str = "dashboard"
    customer_name: Optional[str] = None
    service_type: str
    urgency: str
    address: Optional[str] = None
    callback_number: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None  # set by the "Mark done" action; drives the Reviews SMS
    referral_sent_at: Optional[datetime] = None  # set once the referral ask has gone out for this job


class RecoveryCampaign(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    business_id: int = Field(foreign_key="business.id")
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
    business_id: int = Field(foreign_key="business.id")
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


class ReferralLead(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    business_id: int = Field(foreign_key="business.id")
    source_job_id: int = Field(foreign_key="job.id")  # which completed job triggered this ask
    asker_phone: str  # the existing customer who was asked
    referred_name: Optional[str] = None  # extracted by Claude, if present
    referred_phone: Optional[str] = None  # extracted by Claude, if present
    raw_reply_text: str  # always stored, regardless of extraction outcome
    created_at: datetime = Field(default_factory=datetime.utcnow)
