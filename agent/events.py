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

# Booking lifecycle (Milestone A). Unlike every constant above, these have a
# real reader: the booking timeline the owner sees on the client page is
# rendered straight from these rows. The `event` table is append-only and
# already deduplicated, which is exactly what an audit trail needs — so this
# reuses it rather than introducing a second history table.
BOOKING_REQUESTED_EVENT = "booking.requested"
BOOKING_OWNER_NOTIFIED = "booking.owner_notified"
BOOKING_PROPOSED_EVENT = "booking.proposed"
BOOKING_CONFIRMED_EVENT = "booking.confirmed"
BOOKING_CANCELLED_EVENT = "booking.cancelled"
BOOKING_CUSTOMER_NOTIFIED = "booking.customer_notified"


@dataclass
class DomainEvent:
    type: str
    business_id: int
    payload: Dict[str, Any] = field(default_factory=dict)
    customer_id: Optional[int] = None
    employee_id: Optional[int] = None
    dedup_key: Optional[str] = None
