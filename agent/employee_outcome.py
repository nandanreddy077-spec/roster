"""The Employee Outcome Contract (Milestone B, "No Silent Failures").

The whole architecture already exists, hand-written three times, in
xai_voice_adapter.run_call's except branches: on a call timeout, a budget
cutoff, or a mid-call crash, it texts the owner an honest reason and records
an OwnerNotification, so "the AI stopped working" never means "nothing
happened and nobody knows." This module generalizes that pattern for every
other employee, rather than each one open-coding its own version — see
docs/superpowers/specs/2026-08-11-no-silent-failures-design.md.

Two functions, two root causes:

  report_employee_blocked  — an employee is hired but cannot do its job (a
      missing review_link, an undeployed dependency, ...). RC1 in the design
      doc: there was no "cannot work" state, so this was a bare `continue`.

  report_llm_failure       — a live customer turn's Claude call raised.
      RC2: silence was a legal outcome; now it isn't.

BOTH ARE DEDUPED, DELIBERATELY, THE SAME WAY: notify the owner on the first
occurrence of a (business, cause) pair, then go quiet on repeats. A business
stuck on one missing precondition ticks hourly forever; texting the owner
every time would train them to ignore Roster's texts within a day, which is
alert fatigue functioning as silent failure with extra steps (design doc,
Challenge 1). The mechanism is EventBus's existing dedup_key uniqueness — no
new table, no new "have we already told them" column, just the audit trail
Milestone A gave its first reader.

ponytail: dedup keys never expire, so a cause that resolves and then
recurs (the owner unsets membership_plan after having set it) notifies only
once, ever, not once per episode. Upgrade: publish an "employee.unblocked"
event on resolution and let the dedup key include an episode counter derived
from it. Not worth it before a real customer hits this — see
report_employee_blocked's docstring.
"""

import logging
from datetime import date, datetime
from typing import cast

from channels import get_channel
from db_models import Business
from eventbus import bus
from events import DomainEvent

logger = logging.getLogger(__name__)

# Module-level so tests can substitute a recorder — same convention every
# other service module (service.py, booking_manager.py, ...) already uses.
sms_channel = get_channel()

EMPLOYEE_BLOCKED_EVENT = "employee.blocked"
LLM_FAILURE_EVENT = "employee.llm_failed"

# What a customer hears in place of silence, on both the trial-cap path and
# an LLM failure. Deliberately ONE shared sentence rather than one per
# reason: it must never leak WHY (a billing cap, an outage) — only that
# someone will follow up — and one honest sentence is easier to keep honest
# than five almost-identical ones.
CUSTOMER_FALLBACK_MESSAGE = (
    "Thanks for your message — we're following up and someone from the team "
    "will get back to you shortly."
)


def _bid(business: Business) -> int:
    """Business.id is Optional[int] by SQLModel's primary-key convention, but
    is never None for a row loaded from the database — the only way this
    module is ever reached. cast rather than assert on purpose: this is
    notification code called from inside tick loops, and an AssertionError
    here would take down the processing of every OTHER business in that tick
    to complain about a type that cannot actually be wrong."""
    return cast(int, business.id)


def _today() -> date:
    """Seam for tests — see report_llm_failure's dedup-by-day."""
    return datetime.utcnow().date()


def _notify_owner(business: Business, message: str) -> bool:
    """Best-effort, never raises — same posture as every owner alert in this
    codebase (notifications.notify_owner_of_escalation etc.). A business with
    no escalation_phone has no channel to report through at all; that is
    itself the deepest instance of this failure class, and is exactly why
    this must not raise into a tick loop processing other businesses."""
    if not business.escalation_phone:
        return False
    try:
        sms_channel.send(
            from_number=business.inbound_number or "",
            to_number=business.escalation_phone,
            body=message,
        )
        return True
    except Exception as e:
        logger.error(
            "employee_outcome: failed to notify owner",
            exc_info=e,
            extra={"business_id": business.id},
        )
        return False


def report_employee_blocked(
    session, business: Business, role_key: str, cause: str, detail: str
) -> bool:
    """An employee is hired but cannot do its job right now. Always logs at
    WARNING (so every tick this stays blocked is visible in ops logs); texts
    the owner and records an OwnerNotification only the FIRST time this
    (business, role, cause) triple is seen.

    Returns True if this call was the one that notified (a fresh block),
    False if it was a dedup no-op — callers don't need this today, but it
    matches record_owner_notification's own "tell the caller what happened"
    convention and costs nothing to return.
    """
    logger.warning(
        "employee blocked",
        extra={"business_id": business.id, "role_key": role_key, "cause": cause},
    )
    from notifications import record_owner_notification

    published = bus.publish(
        session,
        DomainEvent(
            type=EMPLOYEE_BLOCKED_EVENT,
            business_id=_bid(business),
            payload={"role_key": role_key, "cause": cause, "detail": detail},
            dedup_key=f"{EMPLOYEE_BLOCKED_EVENT}:{_bid(business)}:{role_key}:{cause}",
        ),
    )
    if not published:
        return False  # already told the owner about this exact cause

    message = f"⚠️ Your {role_key.replace('_', ' ').title()} employee is stuck: {detail}."
    delivered = _notify_owner(business, message)
    record_owner_notification(
        session, _bid(business), "employee_blocked", "missing_precondition", message, delivered
    )
    return True


LEAD_WENT_COLD_EVENT = "lead.went_cold"


def report_lead_went_cold(
    session,
    business: Business,
    customer_name,
    customer_phone: str,
    service_type: str,
    recovery_job_id,
) -> bool:
    """A follow-up sequence finished its last touch with no reply.

    RC4 in the design doc: reaching a terminal state carried no obligation to
    tell anyone, so a chased estimate simply stopped being chased. Nothing
    automated will contact this customer again — which makes this the last
    moment the owner can still act, and therefore the moment worth a text.

    Deduped per RecoveryJob: a lead can only go cold once, and tick() re-reads
    "pending" rows each run, so without the key an already-cold lead could be
    re-announced.
    """
    from notifications import record_owner_notification

    who = customer_name or customer_phone
    published = bus.publish(
        session,
        DomainEvent(
            type=LEAD_WENT_COLD_EVENT,
            business_id=_bid(business),
            payload={"recovery_job_id": recovery_job_id, "customer_phone": customer_phone},
            dedup_key=f"{LEAD_WENT_COLD_EVENT}:{recovery_job_id}",
        ),
    )
    if not published:
        return False

    message = (
        f"📉 {who} never replied about the {service_type} — we've finished "
        f"following up. Worth a call from you if you still want the job: {customer_phone}"
    )
    delivered = _notify_owner(business, message)
    record_owner_notification(
        session, _bid(business), "lead_went_cold", "recovery_sequence_exhausted", message, delivered
    )
    return True


def report_if_failed(session, business: Business, role_key: str, result: dict) -> bool:
    """One line at every live agent.respond() call site.

    engine.respond has already given the CUSTOMER an honest reply — that half
    is unconditional and needs no caller cooperation. This is the owner half,
    which the engine cannot do itself: it holds a ClientConfig, not a session
    or a Business row. Kept as a helper rather than five copies of the same
    `if result.get("failed")` so a caller cannot get it subtly wrong, and so
    the structural test in test_employee_outcome.py has one name to grep for.
    """
    if not result.get("failed"):
        return False
    return report_llm_failure(session, business, role_key, result.get("error") or "unknown error")


def report_llm_failure(session, business: Business, role_key: str, error: str) -> bool:
    """A live customer turn's Claude call raised. Deduped PER CALENDAR DAY,
    not per turn: an Anthropic outage fails many turns in a row, and an alert
    per turn would flood the owner during the exact outage that made the
    first alert matter. The caller (engine.AgentEngine.respond's failure
    branch) has already given the customer CUSTOMER_FALLBACK_MESSAGE — this
    is the owner-side half of that same turn."""
    logger.warning(
        "LLM call failed",
        extra={"business_id": business.id, "role_key": role_key, "error": error},
    )
    from notifications import record_owner_notification

    published = bus.publish(
        session,
        DomainEvent(
            type=LLM_FAILURE_EVENT,
            business_id=_bid(business),
            payload={"role_key": role_key, "error": error},
            dedup_key=f"{LLM_FAILURE_EVENT}:{_bid(business)}:{_today().isoformat()}",
        ),
    )
    if not published:
        return False

    message = (
        f"⚠️ {role_key.replace('_', ' ').title()} is having trouble responding right now "
        "(a technical issue on our end). Customers are getting a polite "
        "follow-up message instead of being left on read — worth checking in "
        "on anyone who texted recently."
    )
    delivered = _notify_owner(business, message)
    record_owner_notification(
        session, _bid(business), "llm_failure", "agent_error", message, delivered
    )
    return True
