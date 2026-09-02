"""Booking Manager — the ONE writer of Job.booking_status.

Before this module there were three independent writers (bookings.book_job's
default, recovery_service's confirm_slot, and app.py's /confirm route), no
transition was validated, and any status could follow any other. Worse, the
loop never ended: /confirm set the column and redirected, sending the customer
nothing — so a customer told "the office will confirm and text you back" was
never contacted again.

Two rules this module exists to enforce, both structural rather than
conventional:

  1. **Every state change goes through here**, so the legal-transition table
     below is the whole truth about how a booking may move. A test asserts no
     other production module assigns booking_status.

  2. **A state change and the message about it are one operation.** Confirming
     without telling the customer is the bug this milestone fixes; making them
     separate calls would let it come straight back.

ORDERING (the same rule bookings.py and notifications.py already follow, for
the same reason): the state change commits FIRST. Everything after it —
events, the customer's text, the owner's receipt — is best-effort and must
never roll back a transition that already happened. A booking that is
confirmed but whose SMS failed is recoverable; a lost confirmation is not.

CONCURRENCY: each transition is a conditional UPDATE that only wins if the row
is still in a legal source state (the same claim-before-send discipline as
recovery_service.tick's day claim). Two racing confirms produce exactly one
state change, one event, and one customer text.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from booking_language import (
    render_cancelled_language,
    render_confirmed_language,
    render_owner_proposed_language,
)
from channels import get_channel, normalize_phone
from db_models import (
    BOOKING_CANCELLED,
    BOOKING_CONFIRMED,
    BOOKING_PROPOSED,
    BOOKING_REQUESTED,
    BOOKING_RESCHEDULE_REQUESTED,
    Business,
    Job,
)
from eventbus import bus
from events import (
    BOOKING_CANCELLED_EVENT,
    BOOKING_CONFIRMED_EVENT,
    BOOKING_CUSTOMER_NOTIFIED,
    BOOKING_OWNER_NOTIFIED,
    BOOKING_PROPOSED_EVENT,
    BOOKING_REQUESTED_EVENT,
    DomainEvent,
)
from notifications import is_test_thread
from sqlalchemy import update as sa_update
from sqlmodel import Session, col

logger = logging.getLogger(__name__)

# Module-level so tests can substitute a recorder, matching the convention
# service.py, review_service.py and membership_service.py already use.
sms_channel = get_channel()

# The whole truth about how a booking may move. Anything not listed is
# rejected — notably CANCELLED is terminal (a returning customer gets a new
# Job, which keeps completed_at and every employee that reads it untouched).
LEGAL_TRANSITIONS: frozenset = frozenset(
    {
        (BOOKING_REQUESTED, BOOKING_PROPOSED),
        (BOOKING_REQUESTED, BOOKING_CONFIRMED),
        (BOOKING_REQUESTED, BOOKING_CANCELLED),
        (BOOKING_REQUESTED, BOOKING_RESCHEDULE_REQUESTED),
        (BOOKING_PROPOSED, BOOKING_CONFIRMED),
        (BOOKING_PROPOSED, BOOKING_CANCELLED),
        (BOOKING_PROPOSED, BOOKING_RESCHEDULE_REQUESTED),
        (BOOKING_CONFIRMED, BOOKING_CANCELLED),
        (BOOKING_CONFIRMED, BOOKING_RESCHEDULE_REQUESTED),
        (BOOKING_RESCHEDULE_REQUESTED, BOOKING_PROPOSED),
        (BOOKING_RESCHEDULE_REQUESTED, BOOKING_CONFIRMED),
        (BOOKING_RESCHEDULE_REQUESTED, BOOKING_CANCELLED),
    }
)

# States from which a booking is still awaiting an owner decision — what a
# bare "Y" from the owner is allowed to resolve.
AWAITING_OWNER = (BOOKING_REQUESTED, BOOKING_RESCHEDULE_REQUESTED)


class IllegalTransition(ValueError):
    """A caller asked for a move the state machine does not allow."""


class NothingToConfirm(ValueError):
    """confirm() was called on a booking that has no time in it.

    Not an IllegalTransition — REQUESTED → CONFIRMED is a legal move; the
    problem is that there is nothing to confirm. Separate so callers can say
    something useful ("propose a time first") instead of the state-machine
    message, which would be both wrong and baffling."""


@dataclass(frozen=True)
class BookingResult:
    """`applied` is False when the booking was ALREADY in the target state —
    an idempotent no-op, not a failure. Callers use it to avoid re-sending."""

    applied: bool
    state: str
    customer_notified: bool = False
    customer_name: str = ""


def _sources_for(target: str) -> list:
    return [s for (s, t) in LEGAL_TRANSITIONS if t == target]


def _customer_number(job: Job) -> Optional[str]:
    """Where to text this customer. callback_number first: a voice booking's
    customer_phone is the synthetic `xai-voice:{call_id}` thread, and texting
    that would reach nobody.

    Normalized on the way out as well as on the way in (bookings._normalized).
    Belt and braces on purpose: rows written before that fix still hold
    transcribed shapes like "(512) 555-0149", and Twilio resolves a number
    with no `+` against the SENDER's country — so an un-normalized destination
    does not fail loudly, it delivers a booking confirmation to a stranger and
    reports success.
    """
    number = normalize_phone(job.callback_number or job.customer_phone or "")
    if not number or is_test_thread(number):
        return None
    return number


def _notify_customer(business: Business, job: Job, body: str) -> bool:
    """Best-effort, never raises. The transition has already committed and
    matters more than the message about it — same posture as
    notifications.notify_owner_of_booking."""
    number = _customer_number(job)
    if number is None:
        return False
    try:
        sms_channel.send(from_number=business.inbound_number or "", to_number=number, body=body)
    except Exception as e:
        logger.error(
            "booking: failed to notify customer",
            exc_info=e,
            extra={"business_id": business.id, "job_id": job.id},
        )
        return False
    return True


def _publish(session: Session, job: Job, event_type: str, payload: dict, dedup: Optional[str]):
    """Best-effort audit write. A gap in the timeline must never cost a
    transition, so this swallows and logs rather than raising."""
    try:
        bus.publish(
            session,
            DomainEvent(
                type=event_type,
                business_id=job.business_id,
                customer_id=job.customer_id,
                payload=payload,
                dedup_key=dedup,
            ),
        )
    except Exception as e:
        logger.error(
            "booking: failed to publish event",
            exc_info=e,
            extra={"business_id": job.business_id, "job_id": job.id, "event_type": event_type},
        )


def _claim(session: Session, job: Job, target: str, **extra_values) -> bool:
    """Move the row to `target`, but only from a legal source state.

    Returns True if THIS call made the change. False means the row was already
    in the target state (idempotent no-op). Raises IllegalTransition when the
    row is in some other state entirely — a real programming error, and loud
    on purpose.
    """
    values = {"booking_status": target}
    values.update(extra_values)
    claimed = session.execute(
        sa_update(Job)
        .where(col(Job.id) == job.id, col(Job.booking_status).in_(_sources_for(target)))
        .values(**values)
    )
    session.commit()
    # CursorResult carries rowcount; the base Result protocol mypy infers here
    # does not. The UPDATE above always produces a cursor result.
    if claimed.rowcount == 1:  # type: ignore[attr-defined]
        session.refresh(job)
        return True

    session.refresh(job)
    if job.booking_status == target:
        return False  # someone (or an earlier identical call) already did it
    raise IllegalTransition(f"cannot move job {job.id} from {job.booking_status!r} to {target!r}")


def confirm(session: Session, business: Business, job: Job) -> BookingResult:
    """A human has actually checked availability and locked the time in.

    This is the ONLY path to BOOKING_CONFIRMED in the codebase, and the only
    caller of render_confirmed_language — nothing automated may reach it,
    because nothing automated has checked a real technician's calendar.

    Raises NothingToConfirm when the booking has no window. That check comes
    BEFORE the claim, deliberately: the state change commits first and is
    never rolled back (see the ORDERING note in the module docstring), so a
    guard that ran after it would leave a booking marked CONFIRMED with no
    time and no message — strictly worse than the bug it was catching.

    The bug this replaced (found 2026-09-01): the window was passed as
    `window or "your visit"`, so confirming a booking nobody had proposed a
    time for sent the customer a confirmation naming no time at all — just
    "your visit". The customer had no way to know when anyone was coming, and
    every reason to think a time had been agreed. There is no honest
    confirmation without a time, so this refuses instead of inventing one:
    the owner proposes a window first, then confirms it. The exact wording is
    deliberately not quoted here — test_booking_honesty.py's repo-wide audit
    scans literal source text, and it is right to fail on a source file that
    contains the phrase for any reason, docstrings included.
    """
    window = job.preferred_window or ""
    name = job.customer_name or ""
    if not window.strip():
        raise NothingToConfirm(
            f"job {job.id} has no proposed time — there is nothing to confirm yet"
        )
    if not _claim(session, job, BOOKING_CONFIRMED, confirmed_at=datetime.utcnow()):
        return BookingResult(False, BOOKING_CONFIRMED, customer_name=name)

    _publish(
        session,
        job,
        BOOKING_CONFIRMED_EVENT,
        {"job_id": job.id, "window": window},
        f"booking.confirmed:{job.id}",
    )
    notified = _notify_customer(business, job, render_confirmed_language(window))
    if notified:
        _publish(
            session,
            job,
            BOOKING_CUSTOMER_NOTIFIED,
            {"job_id": job.id, "about": BOOKING_CONFIRMED},
            f"booking.customer_notified:{job.id}:{BOOKING_CONFIRMED}",
        )
    return BookingResult(True, BOOKING_CONFIRMED, notified, name)


def reject(
    session: Session, business: Business, job: Job, reason: Optional[str] = None
) -> BookingResult:
    """The owner can't take this booking. Terminal.

    `reason` is the owner's own words and is stored for the owner's benefit
    only — it is never forwarded to the customer, because nobody has read it.
    """
    window = job.preferred_window or ""
    name = job.customer_name or ""
    if not _claim(
        session,
        job,
        BOOKING_CANCELLED,
        cancelled_at=datetime.utcnow(),
        booking_notes=reason,
    ):
        return BookingResult(False, BOOKING_CANCELLED, customer_name=name)

    _publish(
        session,
        job,
        BOOKING_CANCELLED_EVENT,
        {"job_id": job.id, "reason": reason or ""},
        f"booking.cancelled:{job.id}",
    )
    notified = _notify_customer(business, job, render_cancelled_language(window))
    if notified:
        _publish(
            session,
            job,
            BOOKING_CUSTOMER_NOTIFIED,
            {"job_id": job.id, "about": BOOKING_CANCELLED},
            f"booking.customer_notified:{job.id}:{BOOKING_CANCELLED}",
        )
    return BookingResult(True, BOOKING_CANCELLED, notified, name)


def propose(session: Session, business: Business, job: Job, window: str) -> BookingResult:
    """The owner offers a different window. The customer is asked, not told —
    a proposal is not an agreement, so this can never produce confirmation
    language.

    Unlike confirm/reject, a proposal may legitimately happen more than once
    (the owner offers Friday, then Saturday), so the events carry no dedup key
    and the claim is not an idempotency guard here — re-proposing the SAME
    window is treated as a repeat and does not re-text.
    """
    name = job.customer_name or ""
    if job.booking_status == BOOKING_PROPOSED and job.preferred_window == window:
        return BookingResult(False, BOOKING_PROPOSED, customer_name=name)

    _claim(
        session,
        job,
        BOOKING_PROPOSED,
        preferred_window=window,
        owner_proposed_at=datetime.utcnow(),
    )
    _publish(session, job, BOOKING_PROPOSED_EVENT, {"job_id": job.id, "window": window}, None)
    notified = _notify_customer(business, job, render_owner_proposed_language(window))
    if notified:
        _publish(
            session,
            job,
            BOOKING_CUSTOMER_NOTIFIED,
            {"job_id": job.id, "about": BOOKING_PROPOSED},
            None,
        )
    return BookingResult(True, BOOKING_PROPOSED, notified, name)


# How long after the owner offers a window an inbound text is still assumed to
# be an answer to it. Bounded for the same reason find_active_referral_ask and
# find_active_membership_offer are bounded: without a limit, an unrelated text
# months later would still be misrouted to this handler forever.
PROPOSAL_REPLY_WINDOW_DAYS = 14


def find_active_owner_proposal(session: Session, business_id: int, customer_phone: str):
    """The booking, if any, whose owner-proposed window this customer is
    answering. Requires owner_proposed_at — a slot the CUSTOMER picked (Quote
    Chaser's confirm_slot) also sits in BOOKING_PROPOSED but is not a question
    awaiting their answer, so it must not intercept their next text."""
    from datetime import timedelta

    from sqlmodel import select

    cutoff = datetime.utcnow() - timedelta(days=PROPOSAL_REPLY_WINDOW_DAYS)
    return session.exec(
        select(Job)
        .where(
            Job.business_id == business_id,
            Job.booking_status == BOOKING_PROPOSED,
            col(Job.owner_proposed_at).is_not(None),
            col(Job.owner_proposed_at) >= cutoff,
            col(Job.callback_number) == customer_phone,
        )
        .order_by(col(Job.owner_proposed_at).desc())
    ).first()


def handle_customer_proposal_reply(
    session: Session, business: Business, job: Job, text: str
) -> str:
    """The customer has answered the owner's proposed window.

    Deliberately does NOT classify the reply with an LLM and does NOT change
    state. Two reasons, and both matter more than the convenience:

      1. Only a human may reach BOOKING_CONFIRMED. A model deciding "that
         works" means yes would be an automated path to a confirmation nobody
         verified — exactly what the booking-honesty audit removed.
      2. Forwarding the customer's own words costs nothing, cannot be
         misclassified, and gives the owner strictly more information than any
         label would.

    So the booking stays PROPOSED and the owner gets the raw reply plus the
    one-tap action to confirm it.
    """
    from notifications import (
        KIND_ESCALATION,
        SOURCE_ALERT_OWNER,
        record_owner_notification,
    )

    who = job.customer_name or job.callback_number or "A customer"
    window = job.preferred_window or "the time you offered"
    message = (
        f"💬 {who} replied about {window}:\n"
        f'"{text.strip()}"\n'
        f"Reply Y{job.id} to confirm it, or N{job.id} to decline."
    )
    delivered = False
    if business.escalation_phone:
        try:
            sms_channel.send(
                from_number=business.inbound_number or "",
                to_number=business.escalation_phone,
                body=message,
            )
            delivered = True
        except Exception as e:
            logger.error(
                "booking: failed to forward customer reply to owner",
                exc_info=e,
                extra={"business_id": business.id, "job_id": job.id},
            )
    # job.business_id, not business.id: identical value, but non-Optional on
    # the model, so the type is honest at the boundary.
    record_owner_notification(
        session, job.business_id, KIND_ESCALATION, SOURCE_ALERT_OWNER, message, delivered
    )
    return "Thanks — I've passed that to the office and they'll confirm it with you."


def record_request(session: Session, business: Business, job: Job, owner_notified: bool) -> None:
    """Audit-only: put a newly-created booking on the timeline.

    Deliberately does NOT create the Job — bookings.book_job already did that,
    and it owns the upsert/dedup rules that keep a repeat log_job from
    duplicating a booking. This records what happened so the owner's timeline
    starts at the beginning instead of at the first owner action.

    Best-effort like every other write in this module: a missing timeline
    entry must never cost a booking.
    """
    _publish(
        session,
        job,
        BOOKING_REQUESTED_EVENT,
        {"job_id": job.id, "window": job.preferred_window or ""},
        f"booking.requested:{job.id}",
    )
    if owner_notified:
        _publish(
            session,
            job,
            BOOKING_OWNER_NOTIFIED,
            {"job_id": job.id},
            f"booking.owner_notified:{job.id}",
        )


# ---- the timeline the owner reads ------------------------------------------

# What each event says out loud. Centralized here for the same reason
# workspace.py centralizes its label maps: a template that invents its own
# wording drifts from what actually happened.
_TIMELINE_LABELS = {
    BOOKING_REQUESTED_EVENT: "Booking created",
    BOOKING_OWNER_NOTIFIED: "Owner notified",
    BOOKING_PROPOSED_EVENT: "Owner proposed a new time",
    BOOKING_CONFIRMED_EVENT: "Owner confirmed",
    BOOKING_CANCELLED_EVENT: "Owner declined",
    BOOKING_CUSTOMER_NOTIFIED: "Customer notified",
}


@dataclass(frozen=True)
class TimelineEntry:
    at: datetime
    label: str
    job_id: int


def timeline(session: Session, business_id: int, job_id: int) -> list:
    """Every recorded step for one booking, oldest first.

    Scoped by business_id as well as job_id — business isolation is the
    security boundary everywhere in Roster, and a timeline is customer data.

    ponytail: filters the payload in Python rather than with a JSON query,
    because payload_json is a plain string column and the per-business event
    count is small. Push the job_id into an indexed column if a business's
    event history ever gets large enough to feel this.
    """
    import json

    from db_models import Event
    from sqlmodel import select

    rows = session.exec(
        select(Event)
        .where(Event.business_id == business_id, col(Event.type).in_(list(_TIMELINE_LABELS)))
        .order_by(col(Event.occurred_at), col(Event.id))
    ).all()

    entries = []
    for row in rows:
        try:
            payload = json.loads(row.payload_json or "{}")
        except ValueError:
            continue
        if payload.get("job_id") != job_id:
            continue
        label = _TIMELINE_LABELS.get(row.type, row.type)
        window = payload.get("window")
        if window and row.type in (BOOKING_REQUESTED_EVENT, BOOKING_PROPOSED_EVENT):
            label = f"{label} — {window}"
        entries.append(TimelineEntry(at=row.occurred_at, label=label, job_id=job_id))
    return entries


# ---- the owner's side of the conversation ----------------------------------


def pending_for_owner(session: Session, business: Business) -> list:
    """Bookings genuinely waiting on an owner decision.

    Escalation rows are excluded for the same reason lead_qualifier_service
    excludes them: they are alert bookkeeping, not appointments, and offering
    to "confirm" one would be meaningless. Completed work is excluded because
    confirming a time for a job already done is nonsense.
    """
    from db_models import ORIGIN_ESCALATION
    from sqlmodel import select

    return list(
        session.exec(
            select(Job)
            .where(
                Job.business_id == business.id,
                col(Job.booking_status).in_(AWAITING_OWNER),
                Job.origin != ORIGIN_ESCALATION,
                col(Job.completed_at).is_(None),
            )
            .order_by(col(Job.id))
        ).all()
    )


def _describe(job: Job) -> str:
    who = job.customer_name or job.callback_number or "a customer"
    when = f" — {job.preferred_window}" if job.preferred_window else ""
    return f"#{job.id} {who} ({job.service_type}){when}"


def _receipt(verb: str, result: BookingResult, job: Job) -> str:
    """What the owner hears back. Requirement 4: the owner is told whether the
    CUSTOMER was actually reached, not merely that the state changed — a
    confirmation the customer never received is the failure this milestone
    exists to make visible."""
    who = result.customer_name or "the customer"
    if not result.applied:
        return f"#{job.id} was already {verb}. Nothing sent."
    if result.customer_notified:
        return f"✅ #{job.id} {verb}. {who} has been texted."
    return f"⚠️ #{job.id} {verb}, but we couldn't reach {who} by text. Please call them directly."


def handle_owner_sms(session: Session, business: Business, text: str) -> str:
    """Route one inbound owner text to a booking action, and return the reply.

    Only ever called for a number that matches this business's
    escalation_phone, so every job it touches is looked up under that same
    business_id — an owner can never reach another shop's booking.
    """
    from owner_commands import CONFIRM, PROPOSE, REJECT, UNKNOWN, parse_owner_command

    command = parse_owner_command(text)
    if command.action == UNKNOWN:
        return _help_text(session, business)

    job = _resolve_job(session, business, command)
    if isinstance(job, str):
        return job  # a message explaining why we couldn't resolve one

    try:
        if command.action == CONFIRM:
            return _receipt("confirmed", confirm(session, business, job), job)
        if command.action == REJECT:
            return _receipt("cancelled", reject(session, business, job), job)
        if command.action == PROPOSE:
            result = propose(session, business, job, command.window)
            if not result.applied:
                return f"#{job.id} was already proposed for {command.window}. Nothing sent."
            who = result.customer_name or "the customer"
            if result.customer_notified:
                return f"Sent — {who} has been asked about {command.window}."
            return f"⚠️ Updated #{job.id}, but we couldn't reach {who} by text."
    except NothingToConfirm:
        # "Y12" on a booking nobody has put a time on. Answering with the
        # state-machine message would be wrong (the transition is legal) and
        # useless; the owner needs the next action, which is to offer a time.
        return (
            f"#{job.id} doesn't have a time on it yet, so there's nothing to "
            f"confirm. Text a time like '{job.id} Friday 8am-12pm' and I'll "
            "ask them if it works."
        )
    except IllegalTransition:
        return (
            f"#{job.id} is {job.booking_status} and can't be changed that way. "
            "Open the dashboard if you need to sort it out."
        )
    return _help_text(session, business)


def _resolve_job(session: Session, business: Business, command):
    """Find the job this command is about, or return the message to send back."""
    if command.job_id is not None:
        job = session.get(Job, command.job_id)
        # Tenant isolation is the security boundary everywhere in Roster: an
        # owner naming another business's job id gets the same answer as one
        # naming a job id that doesn't exist.
        if job is None or job.business_id != business.id:
            return f"I couldn't find booking #{command.job_id} for {business.business_name}."
        return job

    waiting = pending_for_owner(session, business)
    if not waiting:
        return "You have no bookings waiting on you right now."
    if len(waiting) == 1:
        return waiting[0]
    listed = "\n".join(_describe(j) for j in waiting)
    return (
        f"You have {len(waiting)} bookings waiting — which one?\n{listed}\n"
        "Reply with the number, e.g. Y" + str(waiting[0].id)
    )


def _help_text(session: Session, business: Business) -> str:
    waiting = pending_for_owner(session, business)
    if not waiting:
        return "You have no bookings waiting on you right now."
    listed = "\n".join(_describe(j) for j in waiting)
    first = waiting[0].id
    return (
        f"Bookings waiting on you:\n{listed}\n"
        f"Reply Y{first} to confirm, N{first} to decline, "
        f"or text a better time like '{first} Friday 8am-12pm'."
    )
