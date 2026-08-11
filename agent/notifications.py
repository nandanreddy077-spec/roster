"""Owner notifications — texts the business owner when an employee books a job.

The customer-facing proof-of-work that reaches an owner who never opens the
dashboard: a booked job lands in their pocket as an SMS ("Frontdesk just booked
AC Repair..."), so the AI feels like a real employee reporting to the boss.

Deliberately a direct outbound send, not routed through the EventBus — there is
exactly one subscriber to a booking today. Move it onto the bus only if a second
subscriber ever appears (see ROADMAP.md). Best-effort by design: a failed
owner-text must never break the booking that already committed.
"""

import logging

from channels import get_channel
from db_models import Business, Job, OwnerNotification

logger = logging.getLogger(__name__)

# Threads that are the owner testing their own AI, not real customer bookings —
# we don't text the owner about their own dashboard test message.
_TEST_THREADS = {"dashboard", "portal-test"}

# The voice equivalent of _TEST_THREADS (2026-07-29, safe voice test mode):
# xai_voice_adapter.py builds a call's thread as f"{prefix}{call_id}", using
# this prefix instead of the real VOICE_THREAD_PREFIX when is_test_call=True.
# A prefix, not a fixed string, because a real call_id still needs to be part
# of the thread for correct per-call isolation. Deliberately does NOT start
# with the real prefix ("xai-voice-test:" vs "xai-voice:"), so every existing
# `.startswith(VOICE_THREAD_PREFIX)` filter (metrics.py's _voice_conversations)
# already excludes test threads with no changes of its own.
VOICE_TEST_THREAD_PREFIX = "xai-voice-test:"

# WHAT happened. The semantic axis — what a customer-facing view groups on.
KIND_JOB_BOOKED = "job_booked"
KIND_ESCALATION = "escalation"
KIND_CALL_DROPPED = "call_dropped"
# A customer said yes to a maintenance plan. Its own kind rather than
# KIND_JOB_BOOKED (no job exists) or KIND_ESCALATION (nothing is wrong, and
# reusing it would inflate the escalations metric with good news).
KIND_MEMBERSHIP_ACCEPTED = "membership_accepted"
# The owner's AI employees have stopped replying because a trial cap was
# crossed. This has to reach the OWNER, not just the founder: from their side
# the office simply went quiet, and every minute they don't know is a customer
# texting into silence.
KIND_TRIAL_CAP_REACHED = "trial_cap_reached"

# WHERE it originated. Operational only, never customer-facing: it exists so
# an SMS booking and a voice booking (both KIND_JOB_BOOKED) can be told apart
# for debugging and analytics without string-matching the message body.
SOURCE_SMS_BOOKING = "sms_booking"
SOURCE_VOICE_BOOKING = "voice_booking"
SOURCE_ALERT_OWNER = "alert_owner"
SOURCE_CALL_DROPPED = "call_dropped"
SOURCE_NEGATIVE_REVIEW_REPLY = "negative_review_reply"
SOURCE_RECOVERY_ESCALATION = "recovery_escalation"
SOURCE_RECOVERY_BOOKING = "recovery_booking"
SOURCE_MEMBERSHIP_ACCEPTED = "membership_accepted"
SOURCE_MEMBERSHIP_QUESTION = "membership_question"
SOURCE_TRIAL_CAP = "trial_cap"

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
        f"\U0001f4cb {employee_name} just booked a job: {job.service_type} "
        f"({job.urgency}) for {who}. Callback: {callback}"
    )


def build_membership_message(customer_name: str, customer_phone: str) -> str:
    """One builder for both the SMS body and the logged row, same rule as
    build_escalation_message: built separately they drift, and the owner's
    dashboard ends up showing something subtly different from the text they got.

    Says "wants to sign up", not "signed up": Roster recorded a yes and can't
    charge a card, so the owner still has to finalize billing. The owner's
    text is exactly where an overstatement would cost the most trust.
    """
    who = customer_name or "A customer"
    return (
        f"\U0001f4b3 {who} ({customer_phone}) wants to sign up for your "
        f"maintenance plan. Give them a call to set it up."
    )


def notify_owner_of_membership(
    business: Business, customer_name: str, customer_phone: str, channel=None
) -> bool:
    """Text the owner that a customer accepted the plan. Best-effort and never
    raises, same posture as notify_owner_of_booking — the acceptance has
    already been recorded and matters more than the notification about it."""
    if not business.escalation_phone:
        return False
    ch = _resolve_channel(channel)
    try:
        ch.send(
            business.inbound_number or "",
            business.escalation_phone,
            build_membership_message(customer_name, customer_phone),
        )
    except Exception:
        return False
    return True


def recent_notifications(session, business_id: int, limit: int = 50):
    """This business's owner alerts, newest first — what the dashboard's
    Notifications page renders (blueprint §4).

    Scoped to one business_id, which is the security boundary everywhere in
    Roster (platform PRD §12): no query may cross businesses.
    """
    from sqlmodel import select

    return list(
        session.exec(
            select(OwnerNotification)
            .where(OwnerNotification.business_id == business_id)
            .order_by(OwnerNotification.id.desc())
            .limit(limit)
        ).all()
    )


def build_escalation_message(business: Business, caller_number: str, reason: str) -> str:
    """One builder for both the SMS body and the logged message — built
    separately they would drift, and the owner's dashboard would show
    something subtly different from the text they actually got.

    Trailing punctuation is stripped from `reason` because this sentence
    supplies its own. Most reasons reach here already ending in a period —
    review_service passes a fixed sentence, and alert_owner/recovery pass
    whatever the model wrote — which rendered as "...review request.. Call
    them back". Normalizing here rather than at each call site is what makes
    that true for model-authored reasons too, which no call site controls.
    """
    reason = (reason or "").strip().rstrip(".").strip()
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
    """True for the owner's own test conversations (SMS's fixed threads, or a
    voice test call's `xai-voice-test:{call_id}` thread), which never produce
    a real owner alert. The ONE shared definition of "test" — every caller
    that needs this check (notify_owner_of_booking, metrics.py, portal.py,
    xai_voice_adapter.py) goes through here so none of them can disagree
    about what counts as real activity."""
    return customer_phone in _TEST_THREADS or customer_phone.startswith(VOICE_TEST_THREAD_PREFIX)


def record_owner_notification(
    session, business_id: int, kind: str, source: str, message: str, delivered: bool
):
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
            business_id=business_id,
            kind=kind,
            source=source,
            message=message,
            delivered=delivered,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row
    except Exception as e:
        # Loud, unlike the sends above: a lost notification row is invisible
        # everywhere else, and this is the module meant to end exactly that
        # class of silence.
        logger.error(
            "failed to record owner notification",
            exc_info=e,
            extra={"business_id": business_id, "kind": kind},
        )
        try:
            session.rollback()
        except Exception:
            pass
        return None


def notify_owner_of_booking(
    business: Business, job: Job, employee_name: str = "Frontdesk", channel=None
) -> bool:
    """Text the owner about a newly booked job. Returns True if a message was
    sent, False if skipped (no escalation number set, or a test-thread
    booking — SMS or voice) or if the send failed. Never raises into the
    booking path."""
    if not business.escalation_phone:
        return False
    if is_test_thread(job.customer_phone):
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
