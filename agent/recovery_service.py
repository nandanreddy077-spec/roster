"""Revenue Recovery: standalone outbound SMS agent that chases unsold quotes and
lapsed customers. Independent of the Frontdesk agent — Recovery owns its own
intake, reply handling, and booking, so a client can run Recovery without ever
enabling Frontdesk.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from booking_language import render_slot_language
from bookings import book_job
from calendar_provider import get_calendar_provider
from channels import STOP_KEYWORDS, get_channel
from db_models import (
    BOOKING_PROPOSED,
    ORIGIN_QUOTE_RECOVERY,
    ORIGIN_REACTIVATION,
    Business,
    Job,
    RecoveryCampaign,
    RecoveryJob,
    RecoveryMessageLog,
)
from engine import AgentEngine
from notifications import (
    KIND_ESCALATION,
    KIND_JOB_BOOKED,
    SOURCE_RECOVERY_BOOKING,
    SOURCE_RECOVERY_ESCALATION,
    build_escalation_message,
    build_owner_message,
    notify_owner_of_booking,
    notify_owner_of_escalation,
    record_owner_notification,
)
from recovery_engine import (
    CONFIRM_SLOT_TOOL,
    ESCALATE_TOOL,
    MEMBERSHIP_OFFSETS,
    RECORD_RESPONSE_TOOL,
    SEQUENCE_DAYS,
    TEMPLATES,
    build_recovery_reply_prompt,
    clean_service_type,
    render_template,
)
from repositories import get_or_create_customer
from runner import is_active
from sqlalchemy import update as sa_update
from sqlmodel import Session, select
from trial_cap import can_respond, record_usage

agent = AgentEngine()
sms_channel = get_channel()
logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("pending", "awaiting_slot")


def create_campaign(
    session: Session,
    client: Business,
    face: str,
    name: str,
    customers: List[Dict[str, Any]],
    template_overrides: Optional[Dict[str, str]] = None,
) -> RecoveryCampaign:
    """`customers` is a list of dicts with keys: phone, name (optional),
    service_type, estimate_amount (optional), days_since (optional)."""
    campaign = RecoveryCampaign(
        business_id=client.id,
        face=face,
        name=name,
        customer_list_json=json.dumps(customers),
        template_overrides_json=json.dumps(template_overrides or {}),
    )
    session.add(campaign)
    session.commit()
    session.refresh(campaign)

    for c in customers:
        session.add(
            RecoveryJob(
                campaign_id=campaign.id,
                business_id=client.id,
                customer_phone=c["phone"],
                customer_name=c.get("name"),
                service_type=clean_service_type(c["service_type"]),
                estimate_amount=c.get("estimate_amount"),
                days_since=c.get("days_since"),
                anchor_date=c.get("anchor_date"),
            )
        )
    session.commit()
    return campaign


def enroll_completed_estimates(session: Session) -> List[RecoveryJob]:
    """Auto-enroll every completed estimate Job into the quote sequence —
    Quote Chaser's automatic trigger (2026-07-30 PR #1), alongside the
    founder-pasted CSV campaign create_campaign already supports.

    Job.completed_at ("Mark done") is the timing anchor, not a separate
    "estimate requested" timestamp: an estimate can only be chased once it's
    actually been given, and completed_at is the exact moment that happens —
    the same anchor Reviews already uses for its own delayed ask.
    Job.is_estimate is the structured "was this an estimate call" flag.

    Each enrollment gets its OWN RecoveryCampaign rather than sharing one
    long-lived campaign: tick()'s elapsed-time math for the quote face reads
    campaign.started_at, so a lead enrolled into an old, shared campaign
    would see its true elapsed age on its very first tick — potentially
    firing the day-28 "last chance" message immediately. One campaign per
    job keeps that math correct with no change to tick() itself.

    source_job_id is the idempotency key: a Job already linked to a
    RecoveryJob is never enrolled twice, so this is safe to call every tick.
    """
    enrolled: List[RecoveryJob] = []
    jobs = session.exec(
        select(Job).where(Job.completed_at.is_not(None), Job.is_estimate == True)  # noqa: E712
    ).all()

    for job in jobs:
        if not job.callback_number:
            continue
        # The deployment invariant, at the point where automatic enrollment
        # begins (see runner.dispatch_tick). This is the ONLY place a campaign
        # gets created without a human asking for one: create_campaign is a
        # founder action and is consent by definition, but auto-enrollment has
        # no action behind it at all. Unlike Reviews and Referral, nothing here
        # required any configuration either — a completed estimate alone put a
        # real customer into a multi-touch texting sequence, for every business
        # on the platform. Gating enrollment is sufficient to gate the sends:
        # tick() only ever works campaigns that were founder-created or
        # enrolled here, so there is no third way for one to exist.
        client = session.get(Business, job.business_id)
        if client is None or not is_active(session, client, "quote_chaser"):
            continue
        already_enrolled = session.exec(
            select(RecoveryJob).where(RecoveryJob.source_job_id == job.id)
        ).first()
        if already_enrolled is not None:
            continue

        campaign = RecoveryCampaign(
            business_id=job.business_id,
            face="quote",
            name=f"Auto-detected: {job.service_type}",
            customer_list_json=json.dumps(
                [
                    {
                        "phone": job.callback_number,
                        "name": job.customer_name,
                        "service_type": job.service_type,
                    }
                ]
            ),
        )
        session.add(campaign)
        session.commit()
        session.refresh(campaign)

        recovery_job = RecoveryJob(
            campaign_id=campaign.id,
            business_id=job.business_id,
            source_job_id=job.id,
            customer_phone=job.callback_number,
            customer_name=job.customer_name,
            service_type=clean_service_type(job.service_type),
        )
        session.add(recovery_job)
        session.commit()
        session.refresh(recovery_job)
        enrolled.append(recovery_job)

    return enrolled


def find_active_recovery_job(
    session: Session, client_id: int, customer_phone: str
) -> Optional[RecoveryJob]:
    return session.exec(
        select(RecoveryJob).where(
            RecoveryJob.business_id == client_id,
            RecoveryJob.customer_phone == customer_phone,
            RecoveryJob.current_status.in_(ACTIVE_STATUSES),
            RecoveryJob.last_sent_day.is_not(None),
        )
    ).first()


def _next_due_day(job: RecoveryJob, elapsed_days: int, day_list: List[int]) -> Optional[int]:
    """Latest unsent day/offset whose threshold has passed, from `day_list`
    (must be ascending). Returns the furthest one (not the first) so a job
    that missed several thresholds jumps straight to where it should be,
    instead of replaying the whole backlog as a burst of texts. Works
    identically for the day-count faces (SEQUENCE_DAYS, elapsed_days >= 0)
    and the date-anchored membership face (MEMBERSHIP_OFFSETS, elapsed_days
    can be negative — the comparison logic doesn't care about sign)."""
    candidate = None
    for day in day_list:
        if job.last_sent_day is not None and day <= job.last_sent_day:
            continue
        if elapsed_days >= day:
            candidate = day
        else:
            break
    return candidate


def tick(session: Session) -> List[RecoveryJob]:
    """Send any due sequence messages across all active campaigns. Meant to be
    called once a day (see recovery_tick.py) — safe to call more often since
    it only ever sends a given sequence day/offset's message once (tracked by
    last_sent_day)."""
    sent: List[RecoveryJob] = []
    jobs = session.exec(select(RecoveryJob).where(RecoveryJob.current_status == "pending")).all()

    for job in jobs:
        campaign = session.get(RecoveryCampaign, job.campaign_id)
        if campaign is None or not campaign.is_active:
            continue

        if campaign.face == "membership":
            if job.anchor_date is None:
                continue
            anchor = datetime.strptime(job.anchor_date, "%Y-%m-%d")
            elapsed = (datetime.utcnow() - anchor).days
            day_list = MEMBERSHIP_OFFSETS
        else:
            elapsed = (datetime.utcnow() - campaign.started_at).days
            day_list = SEQUENCE_DAYS

        due_day = _next_due_day(job, elapsed, day_list)

        if due_day is None:
            if job.last_sent_day == day_list[-1] and elapsed > day_list[-1]:
                job.current_status = "no_response"
                job.updated_at = datetime.utcnow()
                session.add(job)
                session.commit()
            continue

        # CLAIM the day before sending: a conditional UPDATE that only wins if
        # last_sent_day is still what we read. Two overlapping ticks (cron
        # firing twice, a manual run during the cron) both reach here, but only
        # one claim succeeds — the customer can never be double-texted.
        prior_day = job.last_sent_day
        claim_condition = (
            RecoveryJob.last_sent_day.is_(None)
            if prior_day is None
            else RecoveryJob.last_sent_day == prior_day
        )
        claimed = session.execute(
            sa_update(RecoveryJob)
            .where(RecoveryJob.id == job.id, claim_condition)
            .values(last_sent_day=due_day, updated_at=datetime.utcnow())
        )
        session.commit()
        if claimed.rowcount != 1:
            continue  # another tick got here first
        session.refresh(job)

        try:
            client = session.get(Business, job.business_id)
            template = (
                campaign.template_overrides.get(str(due_day)) or TEMPLATES[campaign.face][due_day]
            )
            text = render_template(
                template,
                customer_name=job.customer_name or "there",
                service_type=job.service_type,
                estimate_amount=job.estimate_amount or "",
                days_since=job.days_since or "",
                renewal_date=job.anchor_date or "",
            )
            sms_channel.send(
                from_number=client.inbound_number or "", to_number=job.customer_phone, body=text
            )
            session.add(
                RecoveryMessageLog(recovery_job_id=job.id, message_day=due_day, message_text=text)
            )
            session.commit()
            sent.append(job)
        except Exception as e:
            # Send failed: release the claim so the next tick can retry this
            # day. The claim window means a concurrent tick skipped it this
            # round — a skipped retry is recoverable, a double-text is not.
            logger.error(
                "Recovery tick failed to send",
                exc_info=e,
                extra={"business_id": job.business_id, "recovery_job_id": job.id},
            )
            session.rollback()
            session.execute(
                sa_update(RecoveryJob)
                .where(RecoveryJob.id == job.id, RecoveryJob.last_sent_day == due_day)
                .values(last_sent_day=prior_day, updated_at=datetime.utcnow())
            )
            session.commit()
            continue

    return sent


ESCALATED_REPLY = (
    "Thanks for letting us know — someone from our team will reach out to help with that."
)


def _escalate(session: Session, client: Business, job: RecoveryJob, reason: str) -> str:
    """Page the owner via the same trio Frontdesk's alert_owner and Reviews'
    negative-reply handling already use, tagged with a Recovery-specific
    source. Moves the job to "escalated" — excluded from tick()'s "pending"
    query (stops the automated sequence immediately) and from
    ACTIVE_STATUSES (stops find_active_recovery_job from routing this
    customer's further replies back into the rigid intent/slot state
    machine — a later text falls through to Frontdesk's general handling).

    Deliberately NOT gated on send_hours_ok() (flagged as VULN-0003 in the
    2026-08-10 pentest, declined): that gate protects customers from
    proactive scheduler-driven texts, not owner pages from a live reply. This
    fires from an inbound customer reply happening right now, same category
    as service.py/xai_voice_adapter.py's live-call owner alerts — neither of
    which are quiet-hours gated either. Gating only this path would delay the
    owner's escalation notice until 9am while every other live-interaction
    alert in the app still fires instantly."""
    alerted = notify_owner_of_escalation(client, job.customer_phone, reason)
    record_owner_notification(
        session,
        client.id,
        KIND_ESCALATION,
        SOURCE_RECOVERY_ESCALATION,
        build_escalation_message(client, job.customer_phone, reason),
        alerted,
    )
    job.current_status = "escalated"
    job.escalation_reason = reason
    return ESCALATED_REPLY


def _origin_for_face(session: Session, job: RecoveryJob) -> str:
    """Which employee to credit a booking to. The quote face is Quote Chaser;
    reactivation and membership are both Retention Manager's, matching how
    metrics.py already groups them."""
    campaign = session.get(RecoveryCampaign, job.campaign_id)
    return (
        ORIGIN_QUOTE_RECOVERY
        if campaign is not None and campaign.face == "quote"
        else ORIGIN_REACTIVATION
    )


def handle_recovery_reply(
    session: Session, client: Business, job: RecoveryJob, text: str
) -> Optional[str]:
    """Process an inbound reply to an active Recovery sequence. Returns the text
    to send back to the customer (caller sends it — TwiML for SMS)."""
    log = session.exec(
        select(RecoveryMessageLog)
        .where(RecoveryMessageLog.recovery_job_id == job.id)
        .order_by(RecoveryMessageLog.id.desc())
    ).first()
    if log is not None and log.customer_reply is None:
        log.customer_reply = text
        log.replied_at = datetime.utcnow()
        session.add(log)

    if text.strip().lower() in STOP_KEYWORDS:
        job.current_status = "declined"
        job.updated_at = datetime.utcnow()
        session.add(job)
        session.commit()
        return "You've been unsubscribed and won't receive further messages. Reply START to resume."

    # Trial cap: STOP is always honored (free); a paid follow-up turn is not.
    # Past the soft buffer, skip the paid call and stay silent (webhook sends
    # empty TwiML). The customer's reply is already logged above, so nothing
    # is lost.
    if not can_respond(client):
        return None

    history = [{"role": "user", "content": [{"type": "text", "text": text}]}]

    if job.current_status == "awaiting_slot":
        result = agent.respond(
            client.to_config(),
            history,
            tools=[CONFIRM_SLOT_TOOL, RECORD_RESPONSE_TOOL, ESCALATE_TOOL],
            system_prompt=build_recovery_reply_prompt(job, offered_slots=job.offered_slots),
            max_iters=2,
        )
        record_usage(session, client)
        reply = (
            result["reply"]
            or "Sorry, could you confirm which time works — the first, second, or third option?"
        )
        pending = result["pending_tool_call"]
        if pending and pending["name"] == "escalate_to_owner":
            reason = (
                pending["input"].get("reason")
                or "Needs help the automated follow-up can't provide."
            )
            reply = _escalate(session, client, job, reason)
        elif pending and pending["name"] == "confirm_slot":
            idx = pending["input"]["slot_index"]
            slots = job.offered_slots
            if isinstance(idx, int) and 0 <= idx < len(slots):
                chosen = slots[idx]
                cust = get_or_create_customer(
                    session, client.id, job.customer_phone, job.customer_name
                )
                new_job, _ = book_job(
                    session,
                    client,
                    job.customer_phone,
                    job.customer_phone,
                    {
                        "service_type": job.service_type,
                        "urgency": "routine",
                        "customer_name": job.customer_name,
                        "notes": "Booked via Revenue Recovery",
                        # Same field Frontdesk's log_job uses for a stated
                        # time preference — one place the founder console (or
                        # any future surface) reads "the time in play",
                        # regardless of which employee produced the job.
                        "preferred_window": chosen,
                    },
                    customer_id=cust.id,
                    # The face that ran the sequence is the face that earned
                    # the job — this is the attribution behind every recovered
                    # dollar the owner is ever shown.
                    origin=_origin_for_face(session, job),
                    # PROPOSED, never CONFIRMED: `chosen` came from
                    # calendar_provider.get_available_slots(), which invents
                    # plausible weekday windows — nothing here has checked a
                    # real technician, truck, or calendar. See
                    # booking_language.py for the honest wording this earns.
                    booking_status=BOOKING_PROPOSED,
                )
                job.booked_job_id = new_job.id
                job.current_status = "booked"
                reply = render_slot_language(BOOKING_PROPOSED, chosen)
                # Booking notification parity with every other booking path
                # (service.py's SMS flow): the owner must hear about a real
                # new job regardless of which employee booked it. Also not
                # send_hours_ok()-gated, same reasoning as _escalate() above
                # (VULN-0003, declined).
                delivered = notify_owner_of_booking(client, new_job, employee_name="Quote Chaser")
                record_owner_notification(
                    session,
                    client.id,
                    KIND_JOB_BOOKED,
                    SOURCE_RECOVERY_BOOKING,
                    build_owner_message(new_job, "Quote Chaser"),
                    delivered,
                )
        elif (
            pending
            and pending["name"] == "record_response"
            and pending["input"].get("intent") in ("not_interested", "unsubscribe")
        ):
            job.current_status = "declined"
            reply = "No problem, thanks for letting us know! We won't follow up further."
        job.updated_at = datetime.utcnow()
        session.add(job)
        session.commit()
        return reply

    # current_status == "pending": first reply after a sequence message
    result = agent.respond(
        client.to_config(),
        history,
        tools=[RECORD_RESPONSE_TOOL, ESCALATE_TOOL],
        system_prompt=build_recovery_reply_prompt(job),
        max_iters=2,
    )
    record_usage(session, client)
    pending = result["pending_tool_call"]

    if pending and pending["name"] == "escalate_to_owner":
        reason = (
            pending["input"].get("reason") or "Needs help the automated follow-up can't provide."
        )
        reply = _escalate(session, client, job, reason)
    else:
        intent = (
            pending["input"]["intent"] if pending and pending["name"] == "record_response" else None
        )
        if intent == "interested":
            provider = get_calendar_provider(client)
            slots = provider.get_available_slots(client.hours)
            job.offered_slots_json = json.dumps(slots)
            job.current_status = "awaiting_slot"
            slot_text = "; ".join(f"{i + 1}) {s}" for i, s in enumerate(slots))
            reply = f"Great! Which works best: {slot_text}?"
        elif intent in ("not_interested", "unsubscribe"):
            job.current_status = "declined"
            reply = "No problem, thanks for letting us know! We won't follow up further."
        else:
            reply = result["reply"] or "Thanks for the reply! Are you still interested in booking?"

    job.updated_at = datetime.utcnow()
    session.add(job)
    session.commit()
    return reply
