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

import sys
from datetime import datetime

import backup
from channels import send_hours_ok
from db import engine, init_db
from db_models import SchedulerHeartbeat
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


def _record_heartbeat(ok: bool, error: str | None) -> None:
    """Best-effort by design, same rule as every owner alert in this codebase:
    a heartbeat write failing must never mask (or cause) a tick failure. If
    this itself raises, /health simply reads a stale timestamp next time,
    which is an honest, visible signal rather than a crash."""
    try:
        with Session(engine) as session:
            row = session.get(SchedulerHeartbeat, 1)
            if row is None:
                row = SchedulerHeartbeat(id=1)
            row.last_tick_at = datetime.utcnow()
            row.ok = ok
            row.error = error
            session.add(row)
            session.commit()
    except Exception as e:  # noqa: BLE001 — see docstring
        print(f"[heartbeat] FAILED to record: {e}", file=sys.stderr)


def run():
    init_db()
    # First, and outside the session: a snapshot is only worth taking before
    # anything else in this tick can change data. Best-effort by design — a
    # failed backup must never stop customers being served, but it is printed
    # loudly enough to notice, and the /health endpoint reports snapshot age.
    try:
        backup.run()
    except Exception as e:  # noqa: BLE001 — a backup must never break the tick
        print(f"[backup] FAILED: {e}", file=sys.stderr)

    try:
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
    except Exception as e:
        # Recorded, then re-raised: scheduler.py's own try/except still logs
        # and keeps the loop alive exactly as before. The heartbeat exists so
        # /health can see a failed tick too, not just a missing one.
        _record_heartbeat(ok=False, error=f"{type(e).__name__}: {e}")
        raise
    else:
        _record_heartbeat(ok=True, error=None)


if __name__ == "__main__":
    # No-op when the in-process scheduler already called this via app.py's
    # import — only matters for a standalone `python recovery_tick.py` run
    # (the crontab form this module's docstring still documents).
    from logging_config import configure_logging

    configure_logging()
    run()
