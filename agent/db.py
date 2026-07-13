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
    # Must run before create_all(): create_all() would otherwise create a
    # fresh *empty* `business` table first (since one doesn't exist under
    # that name yet), which flips this migration's "business" not in tables"
    # guard to false before the real rename ever runs — silently orphaning
    # every existing row in the old `client` table behind an empty one.
    _migrate_rename_client_to_business()
    SQLModel.metadata.create_all(engine)
    _migrate_add_columns()


def _migrate_rename_client_to_business():
    """Task 2 (platform-foundation) renamed the `Client` aggregate root to
    `Business`, including the `client_id` FK columns on its child tables.
    Fresh databases have no `client` table at all yet (nothing for
    get_table_names() to find), so these guards correctly no-op there and
    create_all() below lays down the new schema directly; this function only
    does real work against an existing roster.db that still has the old
    `client` table / `client_id` columns."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    tables = set(insp.get_table_names())
    with engine.connect() as conn:
        if "client" in tables and "business" not in tables:
            conn.execute(text("ALTER TABLE client RENAME TO business"))
            conn.commit()
        for tbl in ("job", "message", "recoverycampaign", "recoveryjob", "referrallead"):
            cols = {c["name"] for c in inspect(engine).get_columns(tbl)} if tbl in inspect(engine).get_table_names() else set()
            if "client_id" in cols and "business_id" not in cols:
                try:
                    conn.execute(text(f"ALTER TABLE {tbl} RENAME COLUMN client_id TO business_id"))
                    conn.commit()
                except Exception:
                    conn.rollback()


def _migrate_add_columns():
    """SQLModel.create_all() creates missing *tables* but never adds a new
    *column* to a table that already exists. For columns introduced after a
    table was first created (e.g. Business.requested_roster), add them here,
    ignoring the "duplicate column" error when they're already present. Keeps
    an existing roster.db working without a manual migration step."""
    from sqlalchemy import text

    statements = (
        "ALTER TABLE business ADD COLUMN requested_roster VARCHAR",
        "ALTER TABLE business ADD COLUMN email VARCHAR",
        "ALTER TABLE business ADD COLUMN password_hash VARCHAR",
        "ALTER TABLE business ADD COLUMN tone VARCHAR DEFAULT 'professional and friendly'",
        "ALTER TABLE business ADD COLUMN source VARCHAR",
        "ALTER TABLE business ADD COLUMN source_prompt_dismissed BOOLEAN DEFAULT 0",
        "ALTER TABLE business ADD COLUMN frontdesk_live BOOLEAN DEFAULT 0",
        "ALTER TABLE business ADD COLUMN activated_at DATETIME",
        "ALTER TABLE business ADD COLUMN tested_at DATETIME",
        "ALTER TABLE business ADD COLUMN trial_spend_cents INTEGER DEFAULT 0",
        "ALTER TABLE business ADD COLUMN trial_cap_cents INTEGER DEFAULT 2000",
        "ALTER TABLE business ADD COLUMN trial_soft_buffer_cents INTEGER DEFAULT 200",
        "ALTER TABLE business ADD COLUMN trial_cap_notified BOOLEAN DEFAULT 0",
        "ALTER TABLE business ADD COLUMN answer_mode VARCHAR DEFAULT 'backup'",
        "ALTER TABLE business ADD COLUMN business_phone VARCHAR DEFAULT ''",
        "ALTER TABLE job ADD COLUMN customer_id INTEGER",
        "ALTER TABLE message ADD COLUMN customer_id INTEGER",
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
