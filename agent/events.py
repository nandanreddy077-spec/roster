from dataclasses import dataclass, field
from typing import Any, Dict, Optional

MESSAGE_RECEIVED = "message.received"
CALL_MISSED = "call.missed"
CALL_RECEIVED = "call.received"
CALL_COMPLETED = "call.completed"
JOB_BOOKED = "job.booked"
JOB_COMPLETED = "job.completed"
QUOTE_SENT = "quote.sent"
QUOTE_FOLLOWUP_DUE = "quote.followup_due"
CUSTOMER_DORMANT = "customer.dormant"
MEMBERSHIP_RENEWAL_DUE = "membership.renewal_due"
REVIEW_REQUESTED = "review.requested"
REFERRAL_RECEIVED = "referral.received"
LLM_COMPLETED = "llm.completed"

@dataclass
class DomainEvent:
    type: str
    business_id: int
    payload: Dict[str, Any] = field(default_factory=dict)
    customer_id: Optional[int] = None
    employee_id: Optional[int] = None
    dedup_key: Optional[str] = None
