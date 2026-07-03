"""Referrals: automatic referral-nudge texts triggered off Job.completed_at.
Independent of Recovery — no campaigns, no manually-pasted customer list, no
multi-touch sequence. Every job marked done becomes automatically eligible
once the client has an incentive line set.
"""
from datetime import datetime, timedelta
from typing import List, Optional

from sqlmodel import Session, select

from channels import get_channel
from db_models import Client, Job, ReferralLead
from engine import AgentEngine
from referral_engine import (
    RECORD_REFERRAL_TOOL,
    REFERRAL_DELAY_DAYS,
    REFERRAL_MESSAGE_TEMPLATE,
    REFERRAL_REPLY_WINDOW_DAYS,
    build_referral_reply_prompt,
    render_referral_template,
)

agent = AgentEngine()
sms_channel = get_channel()


def send_due_referral_asks(session: Session) -> List[Job]:
    """Find every completed job old enough, not yet asked, whose client has
    an incentive set, and send the referral text. Meant to be called once a
    day (see recovery_tick.py) — safe to call more often since
    referral_sent_at gates re-sending."""
    sent: List[Job] = []
    cutoff = datetime.utcnow() - timedelta(days=REFERRAL_DELAY_DAYS)
    jobs = session.exec(
        select(Job).where(
            Job.completed_at.is_not(None),
            Job.completed_at <= cutoff,
            Job.referral_sent_at.is_(None),
        )
    ).all()

    for job in jobs:
        client = session.get(Client, job.client_id)
        if client is None or not client.referral_incentive or not job.callback_number:
            continue

        try:
            text = render_referral_template(
                REFERRAL_MESSAGE_TEMPLATE,
                customer_name=job.customer_name or "there",
                service_type=job.service_type,
                incentive=client.referral_incentive,
            )
            sms_channel.send(from_number=client.inbound_number or "", to_number=job.callback_number, body=text)
            job.referral_sent_at = datetime.utcnow()
            session.add(job)
            session.commit()
            sent.append(job)
        except Exception as e:
            print(f"Referrals: failed to send to job {job.id}: {e}")
            session.rollback()
            continue

    return sent


def find_active_referral_ask(session: Session, client_id: int, customer_phone: str) -> Optional[Job]:
    """A referral ask is 'active' — eligible to have an inbound reply routed to
    it — if it was sent within the last REFERRAL_REPLY_WINDOW_DAYS and hasn't
    already produced a captured lead. Time-bounded (unlike Recovery's
    status-based matching) because a referral ask has no ongoing status to
    track; without a bound, an unrelated text months later would still match
    "no lead captured yet" and get misrouted forever."""
    cutoff = datetime.utcnow() - timedelta(days=REFERRAL_REPLY_WINDOW_DAYS)
    jobs = session.exec(
        select(Job)
        .where(
            Job.client_id == client_id,
            Job.callback_number == customer_phone,
            Job.referral_sent_at.is_not(None),
            Job.referral_sent_at >= cutoff,
        )
        .order_by(Job.referral_sent_at.desc())
    ).all()

    for job in jobs:
        already_captured = session.exec(
            select(ReferralLead).where(ReferralLead.source_job_id == job.id)
        ).first()
        if already_captured is None:
            return job
    return None


def handle_referral_reply(session: Session, client: Client, job: Job, text: str) -> str:
    """Process an inbound reply to a referral ask. Always logs a ReferralLead,
    regardless of whether Claude can extract a clean name/phone from the
    reply — the raw text is never lost just because extraction was messy."""
    history = [{"role": "user", "content": [{"type": "text", "text": text}]}]
    result = agent.respond(
        client.to_config(),
        history,
        tools=[RECORD_REFERRAL_TOOL],
        system_prompt=build_referral_reply_prompt(),
        max_iters=2,
    )
    pending = result["pending_tool_call"]
    referred_name = None
    referred_phone = None
    if pending and pending["name"] == "record_referral":
        referred_name = pending["input"].get("referred_name")
        referred_phone = pending["input"].get("referred_phone")

    session.add(
        ReferralLead(
            client_id=client.id,
            source_job_id=job.id,
            # job.callback_number is Optional on Job, but never None here: this
            # function only runs for jobs find_active_referral_ask matched via
            # referral_sent_at, which send_due_referral_asks only ever sets
            # after confirming callback_number was truthy.
            asker_phone=job.callback_number,
            referred_name=referred_name,
            referred_phone=referred_phone,
            raw_reply_text=text,
        )
    )
    session.commit()
    return result["reply"] or "Thanks so much! We'll follow up with them directly."
