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
from recovery_service import enroll_completed_estimates, tick
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

    # Each phase runs independently (docs/PRODUCTION_READINESS.md P1-2). Before
    # this, one phase raising skipped every LATER phase in the same tick — so a
    # bug in Lead Qualifier silenced Reviews, Referral and Membership for an
    # hour until the next tick. Now a phase failure is recorded and the rest
    # still run; the heartbeat carries which phases failed so /health and
    # Sentry see a partial tick, not a clean one.
    failures: list[str] = []

    def _phase(name: str, fn) -> None:
        try:
            with Session(engine) as session:
                produced = fn(session)
            logger.info(f"{name} tick", extra={"produced": len(produced) if produced else 0})
        except Exception as e:  # noqa: BLE001 — one phase must not take down the tick
            logger.exception(f"{name} tick phase failed", extra={"phase": name})
            failures.append(f"{name}: {type(e).__name__}: {e}")

    _phase("Lead Qualifier", qualify_new_jobs)
    _phase("Dispatcher", recommend_dispatch)
    _phase("Quote Chaser enrollment", enroll_completed_estimates)

    # Enrollment/qualification/dispatch never contact a customer directly, so
    # only the sends below are gated on send_hours_ok. A skipped send is picked
    # up next tick.
    if send_hours_ok():
        _phase("Recovery sends", tick)
        _phase("Referrals", send_due_referral_asks)
        _phase("Reviews", send_due_review_requests)
        _phase("Review follow-ups", send_due_review_followups)
        # Last on purpose: the membership offer is the latest touch in the
        # post-completion sequence (see membership_engine.MEMBERSHIP_OFFER_DELAY_DAYS).
        _phase("Membership offers", send_due_membership_offers)
        _phase("Membership follow-ups", send_due_membership_followups)
    else:
        logger.info(
            "outside send hours (9am-8pm local, every mainland US timezone) — "
            "skipping Recovery/Referral/Reviews/Membership sends this tick"
        )

    if failures:
        _record_heartbeat(ok=False, error=" | ".join(failures))
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
