"""Cron entry point: enroll due leads, qualify new jobs, plan dispatch, and
send any due outbound messages across Recovery, Referral, Reviews and
Membership.

Run once a day, e.g. via crontab:
  0 9 * * * cd /path/to/agent && .venv/bin/python recovery_tick.py >> recovery.log 2>&1
Safe to run more than once a day — every step here is idempotent, gated by
its own timestamp/link column (last_sent_day, referral_sent_at,
review_requested_at, source_job_id) or, for Membership, by a unique index that
makes a duplicate claim impossible rather than merely unlikely.
"""

from channels import send_hours_ok
from db import engine, init_db
from dispatcher_service import recommend_dispatch
from lead_qualifier_service import qualify_new_jobs
from membership_service import (
    send_due_membership_followups,
    send_due_membership_offers,
)
from recovery_service import enroll_completed_estimates, tick
from referral_service import send_due_referral_asks
from review_service import send_due_review_followups, send_due_review_requests
from sqlmodel import Session


def run():
    init_db()
    with Session(engine) as session:
        qualified = qualify_new_jobs(session)
        print(f"Lead Qualifier: qualified {len(qualified)} job(s).")
        planned = recommend_dispatch(session)
        print(f"Dispatcher: planned {len(planned)} job(s).")
        enrolled = enroll_completed_estimates(session)
        print(f"Quote Chaser: enrolled {len(enrolled)} estimate(s).")
        # Enrollment/qualification/dispatch never contact a customer directly,
        # so only the calls below — the ones that actually send a text — are
        # gated on send_hours_ok. A skipped send is picked up next tick.
        if send_hours_ok():
            sent = tick(session)
            print(f"Recovery tick: sent {len(sent)} message(s).")
            referral_sent = send_due_referral_asks(session)
            print(f"Referrals: sent {len(referral_sent)} message(s).")
            review_sent = send_due_review_requests(session)
            print(f"Reviews: sent {len(review_sent)} message(s).")
            review_followup_sent = send_due_review_followups(session)
            print(f"Review follow-ups: sent {len(review_followup_sent)} message(s).")
            # Last on purpose: the membership offer is the latest touch in the
            # post-completion sequence (day 7, after the review ask on day 1,
            # the referral ask on day 4 and the review nudge around day 5 —
            # see membership_engine.MEMBERSHIP_OFFER_DELAY_DAYS).
            membership_sent = send_due_membership_offers(session)
            print(f"Membership Agent: sent {len(membership_sent)} offer(s).")
            membership_followup_sent = send_due_membership_followups(session)
            print(f"Membership follow-ups: sent {len(membership_followup_sent)} message(s).")
        else:
            print(
                "Outside send hours (9am-8pm local, every mainland US timezone) — "
                "skipping Recovery/Referral/Reviews/Membership sends this tick."
            )


if __name__ == "__main__":
    run()
