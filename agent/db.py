import fcntl
import logging
import os
from pathlib import Path

from sqlalchemy import event, text
from sqlmodel import Session, SQLModel, create_engine

logger = logging.getLogger(__name__)

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
            db_url = "postgresql://" + db_url[len("postgres://") :]
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
    _backfill_pipeline_stage()
    _backfill_job_origin()
    # Order matters: duplicates must be gone before the unique index is built.
    _dedupe_employees()
    _migrate_add_indexes()


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
            cols = (
                {c["name"] for c in inspect(engine).get_columns(tbl)}
                if tbl in inspect(engine).get_table_names()
                else set()
            )
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
        "ALTER TABLE business ADD COLUMN pipeline_stage VARCHAR DEFAULT 'lead'",
        "ALTER TABLE job ADD COLUMN review_requested_at DATETIME",
        "ALTER TABLE job ADD COLUMN owner_alerted_at DATETIME",
        "ALTER TABLE job ADD COLUMN preferred_window VARCHAR",
        "ALTER TABLE business ADD COLUMN service_area VARCHAR DEFAULT ''",
        "ALTER TABLE customer ADD COLUMN plan_notes VARCHAR",
        "ALTER TABLE job ADD COLUMN review_followup_sent_at DATETIME",
        "ALTER TABLE job ADD COLUMN is_estimate BOOLEAN DEFAULT 0",
        "ALTER TABLE recoveryjob ADD COLUMN source_job_id INTEGER",
        "ALTER TABLE recoveryjob ADD COLUMN escalation_reason VARCHAR",
        "ALTER TABLE business ADD COLUMN voice_provisioning_error VARCHAR",
        "ALTER TABLE business ADD COLUMN membership_plan VARCHAR",
        "ALTER TABLE job ADD COLUMN origin VARCHAR DEFAULT 'inbound'",
        "ALTER TABLE job ADD COLUMN value_cents INTEGER",
        "ALTER TABLE business ADD COLUMN billing_state VARCHAR DEFAULT 'trial'",
        "ALTER TABLE job ADD COLUMN booking_status VARCHAR DEFAULT 'requested'",
        "ALTER TABLE job ADD COLUMN confirmed_at DATETIME",
        "ALTER TABLE job ADD COLUMN cancelled_at DATETIME",
        "ALTER TABLE job ADD COLUMN owner_proposed_at DATETIME",
        "ALTER TABLE job ADD COLUMN booking_notes VARCHAR",
        "ALTER TABLE customer ADD COLUMN opted_out_at DATETIME",
        "ALTER TABLE business ADD COLUMN provisioning_started_at DATETIME",
    )
    with engine.connect() as conn:
        for ddl in statements:
            try:
                conn.execute(text(ddl))
                conn.commit()
            except Exception:
                conn.rollback()


def _backfill_job_origin(engine=None):
    """Label the escalation Jobs that predate the `origin` column.

    Without this every historical alert_owner row keeps the 'inbound' default
    and goes on being counted as a booked job — the exact number the new
    column exists to stop overstating. Matched on the service_type
    bookings.record_escalation writes, which is the only marker those rows
    ever had. Idempotent: re-running sets the same rows to the same value."""
    from bookings import ESCALATION_SERVICE_TYPE
    from db_models import ORIGIN_ESCALATION, ORIGIN_INBOUND, Job
    from sqlmodel import Session, select

    eng = engine if engine is not None else globals()["engine"]
    with Session(eng) as s:
        stale = s.exec(
            select(Job).where(
                Job.service_type == ESCALATION_SERVICE_TYPE,
                Job.origin == ORIGIN_INBOUND,
            )
        ).all()
        for job in stale:
            job.origin = ORIGIN_ESCALATION
            s.add(job)
        if stale:
            s.commit()


def _backfill_customers(engine=None):
    """Backfill Customer records from existing Job and Message rows with
    customer_phone values, and link those rows to their customers by ID.
    Idempotent: skips rows that already have customer_id set."""
    from db_models import Customer, Job, Message
    from sqlmodel import Session, select

    eng = engine if engine is not None else globals()["engine"]
    with Session(eng) as s:
        for model in (Job, Message):
            for r in s.exec(select(model)).all():
                phone = getattr(r, "customer_phone", None)
                if not phone or r.customer_id is not None:
                    continue
                existing = s.exec(
                    select(Customer).where(
                        Customer.business_id == r.business_id, Customer.phone == phone
                    )
                ).first()
                if not existing:
                    existing = Customer(business_id=r.business_id, phone=phone)
                    s.add(existing)
                    s.commit()
                    s.refresh(existing)
                r.customer_id = existing.id
                s.add(r)
        s.commit()


def _backfill_employees(engine=None):
    """Catches up any Employee row a live hire couldn't create at the time
    (e.g. rows from before roster_hire() started creating them directly) —
    not the primary path for a fresh hire, see portal.py's roster_hire()."""
    import json as _json

    from db_models import Business, Employee
    from roles import role_key_for
    from sqlmodel import Session, select

    eng = engine if engine is not None else globals()["engine"]
    with Session(eng) as s:
        for b in s.exec(select(Business)).all():
            have = {
                e.role_key
                for e in s.exec(select(Employee).where(Employee.business_id == b.id)).all()
            }
            if b.frontdesk_live and "frontdesk" not in have:
                s.add(Employee(business_id=b.id, role_key="frontdesk", display_name="Receptionist"))
                have.add("frontdesk")
            requested = getattr(b, "requested_roster", None)
            if requested:
                for role in _json.loads(requested):
                    key = role_key_for(role)
                    if key and key not in have:
                        s.add(Employee(business_id=b.id, role_key=key, display_name=role))
                        have.add(key)
        s.commit()


def _backfill_pipeline_stage(engine=None):
    """A business that was already running before pipeline_stage existed must
    not appear stuck at 'lead' in the founder's pipeline. Idempotent: only
    touches rows still sitting at the default."""
    from db_models import Business
    from sqlmodel import Session, select

    eng = engine if engine is not None else globals()["engine"]
    with Session(eng) as s:
        changed = False
        for b in s.exec(select(Business).where(Business.frontdesk_live == True)).all():  # noqa: E712
            if b.pipeline_stage == "lead":
                b.pipeline_stage = "live"
                s.add(b)
                changed = True
        if changed:
            s.commit()


def _dedupe_employees(engine=None) -> list:
    """Remove duplicate (business_id, role_key) Employee rows, keeping the
    LOWEST id — the oldest, and the one any historical reference would point
    at. Returns the ids removed so the migration is auditable rather than
    silent.

    Deterministic and safe because nothing has ever written Employee.status
    (there is no pause/resume/fire path anywhere), so no duplicate can carry
    state that another lacks. Must run BEFORE _migrate_add_indexes: the unique
    index cannot be created while violations exist.
    """
    from db_models import Employee
    from sqlmodel import Session, select

    eng = engine if engine is not None else globals()["engine"]
    removed = []
    with Session(eng) as s:
        seen = set()
        for e in s.exec(select(Employee).order_by(Employee.id)).all():
            key = (e.business_id, e.role_key)
            if key in seen:
                removed.append(e.id)
                s.delete(e)
            else:
                seen.add(key)
        if removed:
            s.commit()
            logger.warning("removed duplicate employee rows", extra={"employee_ids": removed})
    return removed


def _migrate_add_indexes(engine=None):
    """Indexes added to tables that ALREADY exist. SQLModel.create_all() only
    builds indexes as part of creating a table, so a constraint added to an
    existing model never reaches an existing database without this (see
    docs/superpowers/specs/2026-07-29-phase-4-deployment-path-audit.md F2 —
    verified empirically, not assumed).

    Idempotent via IF NOT EXISTS. Run only after _dedupe_employees.
    """
    from sqlalchemy import text

    eng = engine if engine is not None else globals()["engine"]
    statements = (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_employee_business_role "
        "ON employee (business_id, role_key)",
        # Membership Agent's dedup guarantee. create_all() builds this with the
        # table on a fresh database; this statement is what puts it on a
        # database where membershipoffer already exists from an earlier deploy.
        # Without it the "claim is the insert" contract in db_models silently
        # degrades to "both ticks insert, both text the customer".
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_membership_offer_source_job "
        "ON membershipoffer (source_job_id)",
        # The per-PERSON half of the same guarantee: one plan offer per
        # customer, not one per completed job. Enforced here because the
        # service's own check is a read-then-insert, which two concurrent tick
        # processes can both pass for two different jobs of the same customer.
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_membership_offer_business_customer "
        "ON membershipoffer (business_id, customer_phone)",
    )
    with eng.connect() as conn:
        for ddl in statements:
            try:
                conn.execute(text(ddl))
                conn.commit()
            except Exception as e:
                # A pre-existing violation is the real failure mode here, and it
                # must be loud: the constraint silently not existing is exactly
                # the state this migration exists to end.
                logger.error("failed to create index", exc_info=e, extra={"ddl": ddl})
                conn.rollback()


def get_session():
    with Session(engine) as session:
        yield session
