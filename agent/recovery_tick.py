"""Cron entry point: send any due Recovery sequence messages.

Run once a day, e.g. via crontab:
  0 9 * * * cd /path/to/agent && .venv/bin/python recovery_tick.py >> recovery.log 2>&1
Safe to run more than once a day — tick() only sends each sequence day once.
"""
from sqlmodel import Session

from db import engine, init_db
from recovery_service import tick


def run():
    init_db()
    with Session(engine) as session:
        sent = tick(session)
        print(f"Recovery tick: sent {len(sent)} message(s).")


if __name__ == "__main__":
    run()
