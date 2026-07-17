"""Owner notifications — texts the business owner when an employee books a job.

The customer-facing proof-of-work that reaches an owner who never opens the
dashboard: a booked job lands in their pocket as an SMS ("Frontdesk just booked
AC Repair..."), so the AI feels like a real employee reporting to the boss.

Deliberately a direct outbound send, not routed through the EventBus — there is
exactly one subscriber to a booking today. Move it onto the bus only if a second
subscriber ever appears (see ROADMAP.md). Best-effort by design: a failed
owner-text must never break the booking that already committed.
"""
from channels import get_channel
from db_models import Business, Job

# Threads that are the owner testing their own AI, not real customer bookings —
# we don't text the owner about their own dashboard test message.
_TEST_THREADS = {"dashboard", "portal-test"}

# Lazily-built shared channel (avoids rebuilding a Twilio client per booking).
# Tests inject their own channel via the `channel=` arg and never touch this.
_owner_channel = None


def _resolve_channel(channel):
    global _owner_channel
    if channel is not None:
        return channel
    if _owner_channel is None:
        _owner_channel = get_channel()
    return _owner_channel


def build_owner_message(job: Job, employee_name: str) -> str:
    who = job.customer_name or "a customer"
    callback = job.callback_number or "no number given"
    return (
        f"\U0001F4CB {employee_name} just booked a job: {job.service_type} "
        f"({job.urgency}) for {who}. Callback: {callback}"
    )


def notify_owner_of_escalation(
    business: Business, caller_number: str, reason: str, channel=None
) -> bool:
    """URGENT owner text for a live-call escalation (emergency, complaint,
    anything the AI can't handle). This is the honest replacement for a call
    transfer we cannot perform: the owner gets the caller's number and the
    reason immediately. Returns False (never raises) if the send failed —
    the caller of this function must then tell the caller the truth and give
    them the owner's number directly."""
    if not business.escalation_phone:
        return False
    ch = _resolve_channel(channel)
    try:
        ch.send(
            business.inbound_number or "",
            business.escalation_phone,
            (
                f"URGENT — {business.business_name or 'your business'}: caller "
                f"{caller_number} needs you NOW. Reason: {reason}. "
                f"Call them back immediately."
            ),
        )
    except Exception:
        return False
    return True


def notify_owner_of_booking(
    business: Business, job: Job, employee_name: str = "Frontdesk", channel=None
) -> bool:
    """Text the owner about a newly booked job. Returns True if a message was
    sent, False if skipped (no escalation number set, or a dashboard-test
    booking) or if the send failed. Never raises into the booking path."""
    if not business.escalation_phone:
        return False
    if job.customer_phone in _TEST_THREADS:
        return False
    ch = _resolve_channel(channel)
    try:
        ch.send(
            business.inbound_number or "",
            business.escalation_phone,
            build_owner_message(job, employee_name),
        )
    except Exception:
        return False
    return True
