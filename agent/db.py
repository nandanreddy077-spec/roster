import os
from pathlib import Path

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

DATA_DIR = Path(os.environ.get("ROSTER_DATA_DIR") or (Path(__file__).parent / "data"))
DATA_DIR.mkdir(exist_ok=True, parents=True)

# check_same_thread=False: voice/SMS requests now run their DB + Claude work in a
# worker thread (see app.py's use of run_in_threadpool) so more than one call can
# be in flight per process without blocking the event loop; each checkout may
# therefore be used from a thread other than the one that created it.
engine = create_engine(
    f"sqlite:///{DATA_DIR / 'roster.db'}",
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _enable_wal(dbapi_connection, _):
    # WAL lets readers and a writer proceed concurrently instead of locking the
    # whole file per transaction — needed once multiple threads/workers hit the
    # same SQLite file at once (concurrent calls all logging jobs/messages).
    dbapi_connection.execute("PRAGMA journal_mode=WAL")


def init_db():
    SQLModel.metadata.create_all(engine)
    _migrate_add_columns()


def _migrate_add_columns():
    """SQLModel.create_all() creates missing *tables* but never adds a new
    *column* to a table that already exists. For columns introduced after a
    table was first created (e.g. Client.requested_roster), add them here,
    ignoring the "duplicate column" error when they're already present. Keeps
    an existing roster.db working without a manual migration step."""
    from sqlalchemy import text

    statements = (
        "ALTER TABLE client ADD COLUMN requested_roster VARCHAR",
        "ALTER TABLE client ADD COLUMN email VARCHAR",
        "ALTER TABLE client ADD COLUMN password_hash VARCHAR",
        "ALTER TABLE client ADD COLUMN tone VARCHAR DEFAULT 'professional and friendly'",
        "ALTER TABLE client ADD COLUMN source VARCHAR",
        "ALTER TABLE client ADD COLUMN source_prompt_dismissed BOOLEAN DEFAULT 0",
        "ALTER TABLE client ADD COLUMN frontdesk_live BOOLEAN DEFAULT 0",
        "ALTER TABLE client ADD COLUMN activated_at DATETIME",
        "ALTER TABLE client ADD COLUMN trial_spend_cents INTEGER DEFAULT 0",
        "ALTER TABLE client ADD COLUMN trial_cap_cents INTEGER DEFAULT 2000",
        "ALTER TABLE client ADD COLUMN trial_soft_buffer_cents INTEGER DEFAULT 200",
    )
    with engine.connect() as conn:
        for ddl in statements:
            try:
                conn.execute(text(ddl))
                conn.commit()
            except Exception:
                conn.rollback()


def get_session():
    with Session(engine) as session:
        yield session
