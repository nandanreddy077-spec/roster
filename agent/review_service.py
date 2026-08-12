"""Reviews: automatic review-request texts triggered off Job.completed_at,
after waiting REVIEW_DELAY_DAYS so the ask doesn't feel instant. Independent
of Recovery/Referral — no campaigns, no manually-pasted list, mirroring
referral_service.py's own shape.
"""

import logging
from datetime import datetime, timedelta
from typing import List, Optional

from channels import get_channel
from db_models import Business, Job, ReviewReply
from employee_outcome import CUSTOMER_FALLBACK_MESSAGE, report_employee_blocked, report_if_failed
from engine import AgentEngine
from notifications import (
    KIND_ESCALATION,
    SOURCE_NEGATIVE_REVIEW_REPLY,
    build_escalation_message,
    notify_owner_of_escalation,
    record_owner_notification,
)
from review_engine import (
    OUTCOME_REPLIES,
    RECORD_REVIEW_REPLY_TOOL,
    REVIEW_DELAY_DAYS,
    REVIEW_FOLLOWUP_DELAY_DAYS,
    REVIEW_FOLLOWUP_MESSAGE_TEMPLATE,
    REVIEW_MESSAGE_TEMPLATE,
    REVIEW_REPLY_WINDOW_DAYS,
    build_review_reply_prompt,
    render_review_template,
)
from runner import is_active
from sqlmodel import Session, select
from trial_cap import can_respond, record_usage

agent = AgentEngine()
sms_channel = get_channel()
logger = logging.getLogger(__name__)


def send_due_review_requests(session: Session) -> List[Job]:
    """Find every completed job old enough, not yet asked, whose client has
    a review_link set, and send the review-request text. Meant to be called
    once a day (see recovery_tick.py) — safe to call more often since
    review_requested_at gates re-sending."""
    sent: List[Job] = []
    cutoff = datetime.utcnow() - timedelta(days=REVIEW_DELAY_DAYS)
    jobs = session.exec(
        select(Job).where(
            Job.completed_at.is_not(None),
            Job.completed_at <= cutoff,
            Job.review_requested_at.is_(None),
        )
    ).all()

    for job in jobs:
        client = session.get(Business, job.business_id)
        if client is None or not job.callback_number:
            continue
        # Reviews only works for a business that actually hired it. The
        # Employee row IS the deployment record (ARCHITECTURE.md invariant 8);
        # review_link alone is configuration, not consent to text customers.
        # ponytail: one Employee query per due job — fine at this volume,
        # hoist to a per-business set if the daily due list ever gets large.
        #
        # ORDER MATTERS (Milestone B): the hired check comes BEFORE the
        # review_link check. Not hired is not blocked — a business that never
        # asked for Reviews must not be nagged about a link it has no reason
        # to set.
        if not is_active(session, client, "reviews"):
            continue
        if not client.review_link:
            report_employee_blocked(
                session,
                client,
                "reviews",
                "missing_review_link",
                "no review link is set, so review requests can't go out",
            )
            continue

        try:
            text = render_review_template(
                REVIEW_MESSAGE_TEMPLATE,
                business_name=client.business_name,
                review_link=client.review_link,
            )
            sms_channel.send(
                from_number=client.inbound_number or "", to_number=job.callback_number, body=text
            )
            job.review_requested_at = datetime.utcnow()
            session.add(job)
            session.commit()
            sent.append(job)
        except Exception as e:
            logger.error(
                "Reviews failed to send review request",
                exc_info=e,
                extra={"business_id": job.business_id, "job_id": job.id},
            )
            session.rollback()
            continue

    return sent


def send_due_review_followups(session: Session) -> List[Job]:
    """Find every review-requested job past REVIEW_FOLLOWUP_DELAY_DAYS with
    no follow-up sent yet, and send the one polite nudge. Never a second
    follow-up — review_followup_sent_at gates it, a structural cap of one,
    not a runtime "have we already annoyed them" check.

    Skips any job whose customer already replied with a classified outcome
    other than "unclear" — they've told us where things stand, so a nudge
    would be redundant at best and tone-deaf at worst (e.g. re-asking someone
    who said they were unhappy). "unclear" is the one outcome that leaves the
    door open for the scheduled nudge, since it didn't actually tell us
    anything."""
    sent: List[Job] = []
    cutoff = datetime.utcnow() - timedelta(days=REVIEW_FOLLOWUP_DELAY_DAYS)
    jobs = session.exec(
        select(Job).where(
            Job.review_requested_at.is_not(None),
            Job.review_requested_at <= cutoff,
            Job.review_followup_sent_at.is_(None),
        )
    ).all()

    for job in jobs:
        client = session.get(Business, job.business_id)
        if client is None or not client.review_link or not job.callback_number:
            continue
        if not is_active(session, client, "reviews"):
            continue
        settled_reply = session.exec(
            select(ReviewReply).where(
                ReviewReply.source_job_id == job.id,
                ReviewReply.outcome != "unclear",
            )
        ).first()
        if settled_reply is not None:
            continue

        try:
            text = render_review_template(
                REVIEW_FOLLOWUP_MESSAGE_TEMPLATE,
                business_name=client.business_name,
                review_link=client.review_link,
            )
            sms_channel.send(
                from_number=client.inbound_number or "", to_number=job.callback_number, body=text
            )
            job.review_followup_sent_at = datetime.utcnow()
            session.add(job)
            session.commit()
            sent.append(job)
        except Exception as e:
            logger.error(
                "Reviews failed to send review follow-up",
                exc_info=e,
                extra={"business_id": job.business_id, "job_id": job.id},
            )
            session.rollback()
            continue

    return sent


def find_active_review_ask(session: Session, client_id: int, customer_phone: str) -> Optional[Job]:
    """A review ask is 'active' — eligible to have an inbound reply routed to
    it — if it was sent within the last REVIEW_REPLY_WINDOW_DAYS and hasn't
    already produced a classified reply. Mirrors find_active_referral_ask's
    exact precedent: one classification attempt per ask, no re-routing once a
    ReviewReply exists (of any outcome, including "unclear" — the routing
    layer, unlike the follow-up scheduler, never gets a second try)."""
    cutoff = datetime.utcnow() - timedelta(days=REVIEW_REPLY_WINDOW_DAYS)
    jobs = session.exec(
        select(Job)
        .where(
            Job.business_id == client_id,
            Job.callback_number == customer_phone,
            Job.review_requested_at.is_not(None),
            Job.review_requested_at >= cutoff,
        )
        .order_by(Job.review_requested_at.desc())
    ).all()

    for job in jobs:
        already_replied = session.exec(
            select(ReviewReply).where(ReviewReply.source_job_id == job.id)
        ).first()
        if already_replied is None:
            return job
    return None


def handle_review_reply(session: Session, client: Business, job: Job, text: str) -> Optional[str]:
    """Process an inbound reply to a review ask. Always logs a ReviewReply,
    regardless of classification outcome — the raw text is never lost. A
    "negative" outcome pages the owner using the exact same escalation
    machinery Frontdesk's alert_owner tool already uses (build_escalation_message
    / notify_owner_of_escalation / record_owner_notification), tagged with a
    dedicated source so it's told apart from a live-call escalation."""
    if not can_respond(client):
        session.add(
            ReviewReply(
                business_id=client.id,
                source_job_id=job.id,
                customer_phone=job.callback_number,
                outcome="unclear",
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
        tools=[RECORD_REVIEW_REPLY_TOOL],
        system_prompt=build_review_reply_prompt(),
        max_iters=2,
    )
    record_usage(session, client)
    report_if_failed(session, client, "reviews", result)
    pending = result["pending_tool_call"]
    outcome = "unclear"
    if pending and pending["name"] == "record_review_reply":
        outcome = pending["input"].get("outcome") or "unclear"

    session.add(
        ReviewReply(
            business_id=client.id,
            source_job_id=job.id,
            customer_phone=job.callback_number,
            outcome=outcome,
            raw_reply_text=text,
        )
    )
    session.commit()

    if outcome == "negative":
        reason = "Customer replied negatively to a review request."
        alerted = notify_owner_of_escalation(client, job.callback_number, reason)
        record_owner_notification(
            session,
            client.id,
            KIND_ESCALATION,
            SOURCE_NEGATIVE_REVIEW_REPLY,
            build_escalation_message(client, job.callback_number, reason),
            alerted,
        )

    return OUTCOME_REPLIES.get(outcome, OUTCOME_REPLIES["unclear"])
