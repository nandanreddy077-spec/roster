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

import logging
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
from recovery_service import enroll_cancelled_jobs, enroll_completed_estimates, tick
from referral_service import send_due_referral_asks
from review_service import send_due_review_followups, send_due_review_requests
from sqlmodel import Session

logger = logging.getLogger(__name__)


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
        logger.error("failed to record scheduler heartbeat", exc_info=e)


def run():
    init_db()
    # First, and outside the session: a snapshot is only worth taking before
    # anything else in this tick can change data. Best-effort by design — a
    # failed backup must never stop customers being served, but it is logged
    # loudly enough to notice, and the /health endpoint reports snapshot age.
    try:
        backup.run()
    except Exception as e:  # noqa: BLE001 — a backup must never break the tick
        logger.error("backup failed", exc_info=e)

    try:
        with Session(engine) as session:
            qualified = qualify_new_jobs(session)
            logger.info("Lead Qualifier tick", extra={"qualified": len(qualified)})
            planned = recommend_dispatch(session)
            logger.info("Dispatcher tick", extra={"planned": len(planned)})
            enrolled = enroll_completed_estimates(session)
            logger.info("Quote Chaser tick", extra={"enrolled": len(enrolled)})
            # Rebook rides the same machinery as Quote Chaser's enrollment and
            # is equally contact-free: it only creates rows. The actual texting
            # happens in tick() below, which IS send_hours_ok-gated.
            rebooked = enroll_cancelled_jobs(session)
            logger.info("Rebook tick", extra={"enrolled": len(rebooked)})
            # Enrollment/qualification/dispatch never contact a customer directly,
            # so only the calls below — the ones that actually send a text — are
            # gated on send_hours_ok. A skipped send is picked up next tick.
            if send_hours_ok():
                sent = tick(session)
                logger.info("Recovery tick sent", extra={"sent": len(sent)})
                referral_sent = send_due_referral_asks(session)
                logger.info("Referrals tick sent", extra={"sent": len(referral_sent)})
                review_sent = send_due_review_requests(session)
                logger.info("Reviews tick sent", extra={"sent": len(review_sent)})
                review_followup_sent = send_due_review_followups(session)
                logger.info(
                    "Review follow-ups tick sent", extra={"sent": len(review_followup_sent)}
                )
                # Last on purpose: the membership offer is the latest touch in the
                # post-completion sequence (day 7, after the review ask on day 1,
                # the referral ask on day 4 and the review nudge around day 5 —
                # see membership_engine.MEMBERSHIP_OFFER_DELAY_DAYS).
                membership_sent = send_due_membership_offers(session)
                logger.info("Membership Agent tick sent", extra={"sent": len(membership_sent)})
                membership_followup_sent = send_due_membership_followups(session)
                logger.info(
                    "Membership follow-ups tick sent",
                    extra={"sent": len(membership_followup_sent)},
                )
            else:
                logger.info(
                    "outside send hours (9am-8pm local, every mainland US timezone) — "
                    "skipping Recovery/Referral/Reviews/Membership sends this tick"
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
    # (the crontab form this module's docstring still documents). A crontab
    # tick crashing silently at 3am is exactly the case Sentry exists for.
    import os

    from logging_config import configure_logging
    from sentry_config import configure_sentry

    configure_logging()
    configure_sentry(os.environ)
    run()
