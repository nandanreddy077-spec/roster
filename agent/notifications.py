"""Owner notifications — texts the business owner when an employee books a job.

The customer-facing proof-of-work that reaches an owner who never opens the
dashboard: a booked job lands in their pocket as an SMS ("Frontdesk just booked
AC Repair..."), so the AI feels like a real employee reporting to the boss.

Deliberately a direct outbound send, not routed through the EventBus — there is
exactly one subscriber to a booking today. Move it onto the bus only if a second
subscriber ever appears (see ROADMAP.md). Best-effort by design: a failed
owner-text must never break the booking that already committed.
"""
import sys

from channels import get_channel
from db_models import Business, Job, OwnerNotification

# Threads that are the owner testing their own AI, not real customer bookings —
# we don't text the owner about their own dashboard test message.
_TEST_THREADS = {"dashboard", "portal-test"}

# WHAT happened. The semantic axis — what a customer-facing view groups on.
KIND_JOB_BOOKED = "job_booked"
KIND_ESCALATION = "escalation"
KIND_CALL_DROPPED = "call_dropped"

# WHERE it originated. Operational only, never customer-facing: it exists so
# an SMS booking and a voice booking (both KIND_JOB_BOOKED) can be told apart
# for debugging and analytics without string-matching the message body.
SOURCE_SMS_BOOKING = "sms_booking"
SOURCE_VOICE_BOOKING = "voice_booking"
SOURCE_ALERT_OWNER = "alert_owner"
SOURCE_CALL_DROPPED = "call_dropped"

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


def build_escalation_message(business: Business, caller_number: str, reason: str) -> str:
    """One builder for both the SMS body and the logged message — built
    separately they would drift, and the owner's dashboard would show
    something subtly different from the text they actually got."""
    return (
        f"URGENT — {business.business_name or 'your business'}: caller "
        f"{caller_number} needs you NOW. Reason: {reason}. "
        f"Call them back immediately."
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
            build_escalation_message(business, caller_number, reason),
        )
    except Exception:
        return False
    return True


def is_test_thread(customer_phone: str) -> bool:
    """True for the owner's own test conversations, which never produce a real
    owner alert. Shares _TEST_THREADS with notify_owner_of_booking so the SMS
    and the log can't disagree about what counts as real activity."""
    return customer_phone in _TEST_THREADS


def record_owner_notification(session, business_id: int, kind: str, source: str,
                              message: str, delivered: bool):
    """Persist an owner alert so it survives the SMS. Returns the row, or None
    if the write failed.

    Best-effort by the same rule as the sends above: the booking or escalation
    this describes has ALREADY committed and matters far more than its record,
    so nothing here may raise into the caller. In particular it must never
    affect a send's return value — notify_owner_of_escalation's bool decides
    whether the voice agent tells an emergency caller that help is coming
    (see engine.py's voice prompt).

    Call this only AFTER the caller's primary work is committed: this commits
    the session, and an in-flight transaction would be flushed with it.
    """
    try:
        row = OwnerNotification(
            business_id=business_id, kind=kind, source=source,
            message=message, delivered=delivered,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row
    except Exception as e:
        # Loud, unlike the sends above: a lost notification row is invisible
        # everywhere else, and this is the module meant to end exactly that
        # class of silence.
        print(f"[notifications] failed to record {kind} for business {business_id}: {e}",
              file=sys.stderr)
        try:
            session.rollback()
        except Exception:
            pass
        return None


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
