"""Cron entry point: enroll due leads, qualify new jobs, and send any due
outbound messages across Recovery, Referral, and Reviews.

Run once a day, e.g. via crontab:
  0 9 * * * cd /path/to/agent && .venv/bin/python recovery_tick.py >> recovery.log 2>&1
Safe to run more than once a day — every step here is idempotent, gated by
its own timestamp/link column (last_sent_day, referral_sent_at,
review_requested_at, source_job_id).
"""
from sqlmodel import Session

from db import engine, init_db
from lead_qualifier_service import qualify_new_jobs
from recovery_service import enroll_completed_estimates, tick
from referral_service import send_due_referral_asks
from review_service import send_due_review_followups, send_due_review_requests


def run():
    init_db()
    with Session(engine) as session:
        qualified = qualify_new_jobs(session)
        print(f"Lead Qualifier: qualified {len(qualified)} job(s).")
        enrolled = enroll_completed_estimates(session)
        print(f"Quote Chaser: enrolled {len(enrolled)} estimate(s).")
        sent = tick(session)
        print(f"Recovery tick: sent {len(sent)} message(s).")
        referral_sent = send_due_referral_asks(session)
        print(f"Referrals: sent {len(referral_sent)} message(s).")
        review_sent = send_due_review_requests(session)
        print(f"Reviews: sent {len(review_sent)} message(s).")
        review_followup_sent = send_due_review_followups(session)
        print(f"Review follow-ups: sent {len(review_followup_sent)} message(s).")


if __name__ == "__main__":
    run()
