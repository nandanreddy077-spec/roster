import fcntl
import os
from pathlib import Path

from sqlalchemy import event, text
from sqlmodel import Session, SQLModel, create_engine

DATA_DIR = Path(os.environ.get("ROSTER_DATA_DIR") or (Path(__file__).parent / "data"))
DATA_DIR.mkdir(exist_ok=True, parents=True)


def resolve_engine_config(environ) -> tuple:
    """(url, connect_args, engine_kwargs) for the configured database.

    Production: set DATABASE_URL to a Postgres URL — SQLite's single-writer
    model cannot survive this app's write concurrency (multiple uvicorn
    workers + per-call threads + the recovery cron all writing at once), and
    a local file dies with the container on platforms without a mounted
    volume. SQLite remains the zero-setup dev/test fallback.
    """
    db_url = environ.get("DATABASE_URL")
    if db_url:
        # Heroku/Railway hand out postgres:// which SQLAlchemy 2.x rejects.
        if db_url.startswith("postgres://"):
            db_url = "postgresql://" + db_url[len("postgres://"):]
        # pool_pre_ping: a recycled/underlying-dropped connection is detected
        # and replaced instead of failing the first webhook after idle.
        return db_url, {}, {"pool_pre_ping": True}
    # check_same_thread=False: voice/SMS requests run their DB + Claude work in
    # worker threads (see app.py's run_in_threadpool), so a checkout may be
    # used from a thread other than the one that created it.
    return (
        f"sqlite:///{DATA_DIR / 'roster.db'}",
        {"check_same_thread": False},
        {},
    )


_url, _connect_args, _kwargs = resolve_engine_config(os.environ)
engine = create_engine(_url, connect_args=_connect_args, **_kwargs)

if engine.dialect.name == "sqlite":
    @event.listens_for(engine, "connect")
    def _enable_wal(dbapi_connection, _):
        # WAL lets readers and a writer proceed concurrently instead of locking
        # the whole file per transaction — needed once multiple threads/workers
        # hit the same SQLite file at once. (SQLite-only pragma.)
        dbapi_connection.execute("PRAGMA journal_mode=WAL")


# Arbitrary-but-fixed advisory lock key for cross-worker schema init on Postgres.
_PG_INIT_LOCK_KEY = 727272001


def init_db():
    # uvicorn runs multiple worker processes (--workers 4), each importing
    # app.py and calling init_db() → create_all() on the SAME database at
    # once. That races: worker A creates `customer`, then worker B's create_all
    # (whose existence-check already returned "missing") fails with
    # "table customer already exists" and the worker crashes on boot. Serialize
    # the whole schema init so exactly one worker does the work and the rest
    # find it already done — create_all, the migrations, and the backfills are
    # all idempotent, so they no-op. SQLite: cross-process file lock.
    # Postgres: pg_advisory_lock (works across hosts, not just one box).
    if engine.dialect.name == "sqlite":
        lock_path = DATA_DIR / ".init.lock"
        with open(lock_path, "w") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                _init_db_locked()
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)
    else:
        with engine.connect() as conn:
            conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _PG_INIT_LOCK_KEY})
            try:
                _init_db_locked()
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _PG_INIT_LOCK_KEY})


def _init_db_locked():
    # Must run before create_all(): create_all() would otherwise create a
    # fresh *empty* `business` table first (since one doesn't exist under
    # that name yet), which flips this migration's "business" not in tables"
    # guard to false before the real rename ever runs — silently orphaning
    # every existing row in the old `client` table behind an empty one.
    _migrate_rename_client_to_business()
    SQLModel.metadata.create_all(engine)
    _migrate_add_columns()
    _backfill_customers()
    _backfill_employees()


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
            try:
                conn.execute(text("ALTER TABLE client RENAME TO business"))
                conn.commit()
            except Exception:
                conn.rollback()
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
        "ALTER TABLE message ADD COLUMN external_id VARCHAR",
    )
    with engine.connect() as conn:
        for ddl in statements:
            try:
                conn.execute(text(ddl))
                conn.commit()
            except Exception:
                conn.rollback()


def _backfill_customers(engine=None):
    """Backfill Customer records from existing Job and Message rows with
    customer_phone values, and link those rows to their customers by ID.
    Idempotent: skips rows that already have customer_id set."""
    from sqlmodel import Session, select
    from db_models import Customer, Job, Message

    eng = engine if engine is not None else globals()["engine"]
    with Session(eng) as s:
        for model in (Job, Message):
            for r in s.exec(select(model)).all():
                phone = getattr(r, "customer_phone", None)
                if not phone or r.customer_id is not None:
                    continue
                existing = s.exec(
                    select(Customer).where(Customer.business_id == r.business_id, Customer.phone == phone)
                ).first()
                if not existing:
                    existing = Customer(business_id=r.business_id, phone=phone)
                    s.add(existing); s.commit(); s.refresh(existing)
                r.customer_id = existing.id; s.add(r)
        s.commit()


def _backfill_employees(engine=None):
    import json as _json
    from sqlmodel import Session, select
    from db_models import Business, Employee
    eng = engine if engine is not None else globals()["engine"]
    with Session(eng) as s:
        for b in s.exec(select(Business)).all():
            have = {e.role_key for e in s.exec(select(Employee).where(Employee.business_id == b.id)).all()}
            if b.frontdesk_live and "frontdesk" not in have:
                s.add(Employee(business_id=b.id, role_key="frontdesk", display_name="Receptionist"))
                have.add("frontdesk")
            requested = getattr(b, "requested_roster", None)
            if requested:
                ROLE_KEYS = {
                    "frontdesk": "frontdesk", "receptionist": "frontdesk",
                    "quote chaser": "quote_chaser", "retention manager": "retention",
                    "reviews": "reviews",
                }
                for role in _json.loads(requested):
                    name = role.strip().lower()
                    key = ROLE_KEYS.get(name, name.replace(" ", "_"))
                    if key and key not in have:
                        s.add(Employee(business_id=b.id, role_key=key, display_name=role))
                        have.add(key)
        s.commit()


def get_session():
    with Session(engine) as session:
        yield session
