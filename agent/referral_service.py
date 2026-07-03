"""Referrals: automatic referral-nudge texts triggered off Job.completed_at.
Independent of Recovery — no campaigns, no manually-pasted customer list, no
multi-touch sequence. Every job marked done becomes automatically eligible
once the client has an incentive line set.
"""
from datetime import datetime, timedelta
from typing import List

from sqlmodel import Session, select

from channels import get_channel
from db_models import Client, Job
from engine import AgentEngine
from referral_engine import REFERRAL_DELAY_DAYS, REFERRAL_MESSAGE_TEMPLATE, render_referral_template

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
