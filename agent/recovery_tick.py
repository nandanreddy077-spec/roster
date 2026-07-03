"""Cron entry point: send any due Recovery sequence messages and referral asks.

Run once a day, e.g. via crontab:
  0 9 * * * cd /path/to/agent && .venv/bin/python recovery_tick.py >> recovery.log 2>&1
Safe to run more than once a day — tick() and send_due_referral_asks() each
only ever send once per job (tracked by last_sent_day / referral_sent_at).
"""
from sqlmodel import Session

from db import engine, init_db
from recovery_service import tick
from referral_service import send_due_referral_asks


def run():
    init_db()
    with Session(engine) as session:
        sent = tick(session)
        print(f"Recovery tick: sent {len(sent)} message(s).")
        referral_sent = send_due_referral_asks(session)
        print(f"Referrals: sent {len(referral_sent)} message(s).")


if __name__ == "__main__":
    run()
