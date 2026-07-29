"""Reviews: automatic review-request texts triggered off Job.completed_at,
after waiting REVIEW_DELAY_DAYS so the ask doesn't feel instant. Independent
of Recovery/Referral — no campaigns, no manually-pasted list, mirroring
referral_service.py's own shape.
"""
from datetime import datetime, timedelta
from typing import List

from sqlmodel import Session, select

from channels import get_channel
from db_models import Business, Job
from review_engine import (
    REVIEW_DELAY_DAYS,
    REVIEW_FOLLOWUP_DELAY_DAYS,
    REVIEW_FOLLOWUP_MESSAGE_TEMPLATE,
    REVIEW_MESSAGE_TEMPLATE,
    render_review_template,
)

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

    Does NOT yet check for a customer reply — ReviewReply and reply-routing
    land in a later PR, which will add "no ReviewReply exists for this job"
    as an additional condition here. Until then this is a pure elapsed-time
    follow-up (documented, not a silently-incomplete feature)."""
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
