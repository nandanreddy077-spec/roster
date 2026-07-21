"""Upsell Agent: after a job completes, recommends a follow-on service via
SMS. Template-based (matches recovery_engine.py's day-based templates) —
deliberately no LLM call for a first cut, so there's no prompt-injection
surface and no added per-send cost. One-way informational message only —
it does not claim to handle a reply conversation (that would need the same
find_active_*/handle_*_reply machinery recovery/referral already have,
which this doesn't build).
"""
from typing import Optional

from db_models import Business, Job


class _SafeDict(dict):
    def __missing__(self, key):
        return ""


def render_template(text: str, **variables) -> str:
    return text.format_map(_SafeDict(variables))


UPSELL_TEMPLATE = (
    "Thanks for choosing {business_name}! Since we were just out for your "
    "{service_type}, a lot of customers in your situation also ask about a "
    "maintenance plan or a follow-up check-up — call or text us anytime if "
    "you'd like details."
)


def build_upsell_message(business_name: str, service_type: Optional[str]) -> str:
    return render_template(
        UPSELL_TEMPLATE,
        business_name=business_name,
        service_type=service_type or "recent job",
    )


def send_upsell_message(business: Business, job: Job, sms_channel) -> Optional[str]:
    """Sends the upsell text and returns the body sent, or None if skipped
    (no callback number to send to)."""
    if not job.callback_number:
        return None
    body = build_upsell_message(business.business_name, job.service_type)
    sms_channel.send(from_number=business.inbound_number or "", to_number=job.callback_number, body=body)
    return body
