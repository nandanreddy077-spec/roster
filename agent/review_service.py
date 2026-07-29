"""Reviews: automatic review-request texts triggered off Job.completed_at,
after waiting REVIEW_DELAY_DAYS so the ask doesn't feel instant. Independent
of Recovery/Referral — no campaigns, no manually-pasted list, mirroring
referral_service.py's own shape.
"""
from datetime import datetime, timedelta
from typing import List, Optional

from sqlmodel import Session, select

from channels import get_channel
from db_models import Business, Job, ReviewReply
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
from trial_cap import can_respond, record_usage

agent = AgentEngine()
sms_channel = get_channel()


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
        if client is None or not client.review_link or not job.callback_number:
            continue

        try:
            text = render_review_template(
                REVIEW_MESSAGE_TEMPLATE,
                business_name=client.business_name,
                review_link=client.review_link,
            )
            sms_channel.send(from_number=client.inbound_number or "", to_number=job.callback_number, body=text)
            job.review_requested_at = datetime.utcnow()
            session.add(job)
            session.commit()
            sent.append(job)
        except Exception as e:
            print(f"Reviews: failed to send review request for job {job.id}: {e}")
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
            sms_channel.send(from_number=client.inbound_number or "", to_number=job.callback_number, body=text)
            job.review_followup_sent_at = datetime.utcnow()
            session.add(job)
            session.commit()
            sent.append(job)
        except Exception as e:
            print(f"Reviews: failed to send review follow-up for job {job.id}: {e}")
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
        return None

    history = [{"role": "user", "content": [{"type": "text", "text": text}]}]
    result = agent.respond(
        client.to_config(),
        history,
        tools=[RECORD_REVIEW_REPLY_TOOL],
        system_prompt=build_review_reply_prompt(),
        max_iters=2,
    )
    record_usage(session, client)
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
            session, client.id, KIND_ESCALATION, SOURCE_NEGATIVE_REVIEW_REPLY,
            build_escalation_message(client, job.callback_number, reason), alerted,
        )

    return OUTCOME_REPLIES.get(outcome, OUTCOME_REPLIES["unclear"])
