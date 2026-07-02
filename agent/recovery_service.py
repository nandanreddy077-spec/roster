"""Revenue Recovery: standalone outbound SMS agent that chases unsold quotes and
lapsed customers. Independent of the Frontdesk agent — Recovery owns its own
intake, reply handling, and booking, so a client can run Recovery without ever
enabling Frontdesk.
"""
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlmodel import Session, select

from calendar_provider import get_calendar_provider
from channels import get_channel
from db_models import Client, Job, RecoveryCampaign, RecoveryJob, RecoveryMessageLog
from engine import AgentEngine
from recovery_engine import (
    CONFIRM_SLOT_TOOL,
    RECORD_RESPONSE_TOOL,
    SEQUENCE_DAYS,
    TEMPLATES,
    build_recovery_reply_prompt,
    render_template,
)

agent = AgentEngine()
sms_channel = get_channel()

ACTIVE_STATUSES = ("pending", "awaiting_slot")


def create_campaign(
    session: Session,
    client: Client,
    face: str,
    name: str,
    customers: List[Dict[str, Any]],
    template_overrides: Optional[Dict[str, str]] = None,
) -> RecoveryCampaign:
    """`customers` is a list of dicts with keys: phone, name (optional),
    service_type, estimate_amount (optional), days_since (optional)."""
    campaign = RecoveryCampaign(
        client_id=client.id,
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
                client_id=client.id,
                customer_phone=c["phone"],
                customer_name=c.get("name"),
                service_type=c["service_type"],
                estimate_amount=c.get("estimate_amount"),
                days_since=c.get("days_since"),
            )
        )
    session.commit()
    return campaign


def find_active_recovery_job(session: Session, client_id: int, customer_phone: str) -> Optional[RecoveryJob]:
    return session.exec(
        select(RecoveryJob).where(
            RecoveryJob.client_id == client_id,
            RecoveryJob.customer_phone == customer_phone,
            RecoveryJob.current_status.in_(ACTIVE_STATUSES),
        )
    ).first()


def _next_due_day(job: RecoveryJob, elapsed_days: int) -> Optional[int]:
    """Latest unsent sequence day whose threshold has passed. Returns the
    furthest one (not the first) so a job that missed several thresholds
    because tick() didn't run for a while jumps straight to where it should be,
    instead of replaying the whole backlog as a burst of texts."""
    candidate = None
    for day in SEQUENCE_DAYS:
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
    it only ever sends a given sequence day's message once (tracked by
    last_sent_day)."""
    sent: List[RecoveryJob] = []
    jobs = session.exec(select(RecoveryJob).where(RecoveryJob.current_status == "pending")).all()

    for job in jobs:
        campaign = session.get(RecoveryCampaign, job.campaign_id)
        if campaign is None or not campaign.is_active:
            continue
        elapsed = (datetime.utcnow() - campaign.started_at).days
        due_day = _next_due_day(job, elapsed)

        if due_day is None:
            if job.last_sent_day == SEQUENCE_DAYS[-1] and elapsed > SEQUENCE_DAYS[-1]:
                job.current_status = "no_response"
                job.updated_at = datetime.utcnow()
                session.add(job)
                session.commit()
            continue

        client = session.get(Client, job.client_id)
        template = campaign.template_overrides.get(str(due_day)) or TEMPLATES[campaign.face][due_day]
        text = render_template(
            template,
            customer_name=job.customer_name or "there",
            service_type=job.service_type,
            estimate_amount=job.estimate_amount or "",
            days_since=job.days_since or "",
        )

        sms_channel.send(from_number=client.inbound_number or "", to_number=job.customer_phone, body=text)
        session.add(RecoveryMessageLog(recovery_job_id=job.id, message_day=due_day, message_text=text))
        job.last_sent_day = due_day
        job.updated_at = datetime.utcnow()
        session.add(job)
        session.commit()
        sent.append(job)

    return sent


def handle_recovery_reply(session: Session, client: Client, job: RecoveryJob, text: str) -> str:
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

    history = [{"role": "user", "content": [{"type": "text", "text": text}]}]

    if job.current_status == "awaiting_slot":
        result = agent.respond(
            client.to_config(),
            history,
            tools=[CONFIRM_SLOT_TOOL],
            system_prompt=build_recovery_reply_prompt(job, offered_slots=job.offered_slots),
            max_iters=2,
        )
        reply = result["reply"] or "Sorry, could you confirm which time works — the first, second, or third option?"
        pending = result["pending_tool_call"]
        if pending and pending["name"] == "confirm_slot":
            idx = pending["input"]["slot_index"]
            slots = job.offered_slots
            if isinstance(idx, int) and 0 <= idx < len(slots):
                chosen = slots[idx]
                new_job = Job(
                    client_id=client.id,
                    customer_phone=job.customer_phone,
                    customer_name=job.customer_name,
                    service_type=job.service_type,
                    urgency="routine",
                    callback_number=job.customer_phone,
                    notes=f"Booked via Revenue Recovery for {chosen}",
                )
                session.add(new_job)
                session.commit()
                session.refresh(new_job)
                job.booked_job_id = new_job.id
                job.current_status = "booked"
                reply = f"Perfect, you're booked for {chosen}! We'll text you a reminder. Any questions, just reply here."
        job.updated_at = datetime.utcnow()
        session.add(job)
        session.commit()
        return reply

    # current_status == "pending": first reply after a sequence message
    result = agent.respond(
        client.to_config(),
        history,
        tools=[RECORD_RESPONSE_TOOL],
        system_prompt=build_recovery_reply_prompt(job),
        max_iters=2,
    )
    pending = result["pending_tool_call"]
    intent = pending["input"]["intent"] if pending and pending["name"] == "record_response" else None

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
