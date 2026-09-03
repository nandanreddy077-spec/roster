"""Referrals: automatic referral-nudge texts triggered off Job.completed_at.
Independent of Recovery — no campaigns, no manually-pasted customer list, no
multi-touch sequence. Every job marked done becomes automatically eligible
once the client has an incentive line set.
"""

import logging
from datetime import datetime, timedelta
from typing import List, Optional

import optout
from channels import get_channel, sms_deliverable
from db_models import Business, Job, ReferralLead
from employee_outcome import CUSTOMER_FALLBACK_MESSAGE, report_employee_blocked, report_if_failed
from engine import AgentEngine
from referral_engine import (
    RECORD_REFERRAL_TOOL,
    REFERRAL_DELAY_DAYS,
    REFERRAL_MESSAGE_TEMPLATE,
    REFERRAL_REPLY_WINDOW_DAYS,
    build_referral_reply_prompt,
    render_referral_template,
)
from runner import is_active
from sqlmodel import Session, select
from trial_cap import can_respond, record_usage

agent = AgentEngine()
sms_channel = get_channel()
logger = logging.getLogger(__name__)


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
        client = session.get(Business, job.business_id)
        if client is None or not job.callback_number:
            continue
        # Same deployment invariant as Reviews and Recovery: the Employee row
        # IS the deployment record, and `referral_incentive` is configuration,
        # not consent to text a business's customers. Referral's registry
        # status is `planned`, which deploy_role refuses to deploy — so this
        # gate makes the registry, the console and the tick agree instead of
        # contradicting each other. Graduating the registry entry is what
        # turns it back on, deliberately.
        if not is_active(session, client, "referral"):
            continue
        if not sms_deliverable(client):  # P1-5 — held until the campaign is approved
            continue
        # ORDER MATTERS (Milestone B): hired first, then configuration. Dead
        # code while Referral's registry status is `planned` (is_active can
        # never be True), and deliberately written anyway — the day it
        # graduates, it must not graduate into the silent-skip bug its three
        # sibling employees just had removed.
        if not client.referral_incentive:
            report_employee_blocked(
                session,
                client,
                "referral",
                "missing_referral_incentive",
                "no referral incentive is set, so referral asks can't go out",
            )
            continue

        # Nobody who asked us to stop gets a proactive text, no matter which
        # employee is running. One check, one record — see optout.py.
        if optout.is_opted_out(session, client.id, job.callback_number):
            continue
        try:
            text = render_referral_template(
                REFERRAL_MESSAGE_TEMPLATE,
                customer_name=job.customer_name or "there",
                service_type=job.service_type,
                incentive=client.referral_incentive,
            )
            sms_channel.send(
                from_number=client.inbound_number or "", to_number=job.callback_number, body=text
            )
            job.referral_sent_at = datetime.utcnow()
            session.add(job)
            session.commit()
            sent.append(job)
        except Exception as e:
            logger.error(
                "Referrals failed to send",
                exc_info=e,
                extra={"business_id": job.business_id, "job_id": job.id},
            )
            session.rollback()
            continue

    return sent


def find_active_referral_ask(
    session: Session, client_id: int, customer_phone: str
) -> Optional[Job]:
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
            Job.business_id == client_id,
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


def handle_referral_reply(session: Session, client: Business, job: Job, text: str) -> Optional[str]:
    """Process an inbound reply to a referral ask. Always logs a ReferralLead,
    regardless of whether Claude can extract a clean name/phone from the
    reply — the raw text is never lost just because extraction was messy."""
    # Trial cap: past the soft buffer, skip the paid extraction call — but
    # still persist the raw reply so the "raw text is never lost" guarantee
    # holds even when we can't run extraction.
    if not can_respond(client):
        session.add(
            ReferralLead(
                business_id=client.id,
                source_job_id=job.id,
                asker_phone=job.callback_number,
                referred_name=None,
                referred_phone=None,
                raw_reply_text=text,
            )
        )
        session.commit()
        # Milestone B: an honest sentence, never silence. The cap stops us
        # spending on a model call, not on answering — this reply rides the
        # TwiML webhook response, so it costs nothing to send.
        return CUSTOMER_FALLBACK_MESSAGE

    history = [{"role": "user", "content": [{"type": "text", "text": text}]}]
    result = agent.respond(
        client.to_config(),
        history,
        tools=[RECORD_REFERRAL_TOOL],
        system_prompt=build_referral_reply_prompt(),
        max_iters=2,
    )
    record_usage(session, client)
    report_if_failed(session, client, "referral", result)
    pending = result["pending_tool_call"]
    referred_name = None
    referred_phone = None
    if pending and pending["name"] == "record_referral":
        referred_name = pending["input"].get("referred_name")
        referred_phone = pending["input"].get("referred_phone")

    session.add(
        ReferralLead(
            business_id=client.id,
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
