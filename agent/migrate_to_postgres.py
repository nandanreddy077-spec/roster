"""One-way migration: the SQLite volume database into Postgres.

Production runs SQLite on a Railway volume, which is single-writer, dies with
the volume, and forces the app to one uvicorn worker (railway.toml pins
--workers 1 for exactly that reason). Postgres removes all three limits and
turns locks.py's per-conversation lock from a process-local threading.Lock into
a real cross-process pg_advisory_lock.

HOW THE SCHEMA IS CREATED: SQLModel.metadata.create_all against Postgres, never
a translation of SQLite's DDL. The models are the source of truth, so the
resulting schema has the right column types, indexes and constraints by
construction — including the unique index on membershipoffer.source_job_id that
makes a duplicate membership offer impossible rather than merely unlikely.

THE BUG THIS EXISTS TO AVOID: SQLite integer primary keys are plain values;
Postgres ones are backed by a sequence. Copying rows preserves the ids but
leaves every sequence at 1, so the first insert after migration collides with
row 1 and the app starts throwing unique-violation errors on a database that
looked fine in every row count. reset_sequences fixes that, and
verify_migration proves it by asking each sequence for its next value.

Run:
    DATABASE_URL=postgresql://... python migrate_to_postgres.py /path/to/roster.db
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import db_models  # noqa: F401 — registers every table on SQLModel.metadata
from sqlalchemy import create_engine, inspect, text
from sqlmodel import SQLModel


class MigrationError(RuntimeError):
    """The migration could not be completed, or could not be proven correct."""


def sqlite_table_names(conn: sqlite3.Connection) -> List[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return sorted(r[0] for r in rows)


def sqlite_row_counts(path: Path) -> Dict[str, int]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in sqlite_table_names(conn)
        }
    finally:
        conn.close()


def postgres_row_counts(engine) -> Dict[str, int]:
    names = sorted(inspect(engine).get_table_names())
    with engine.connect() as conn:
        return {t: conn.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar_one() for t in names}


def copy_rows(sqlite_path: Path, engine) -> Dict[str, int]:
    """Copy every table, in metadata order so a child never lands before its
    parent and foreign keys hold throughout rather than being disabled and
    re-enabled around the load."""
    src = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    copied: Dict[str, int] = {}
    try:
        present = set(sqlite_table_names(src))
        with engine.begin() as conn:
            for table in SQLModel.metadata.sorted_tables:
                if table.name not in present:
                    continue
                rows = [dict(r) for r in src.execute(f"SELECT * FROM {table.name}")]
                copied[table.name] = len(rows)
                if not rows:
                    continue
                # Columns the model declares. A column that exists in SQLite but
                # not on the model is a leftover from a dropped field, and
                # carrying it over would silently resurrect dead data.
                known = {c.name for c in table.columns}
                payload = [{k: v for k, v in row.items() if k in known} for row in rows]
                conn.execute(table.insert(), payload)
    finally:
        src.close()
    return copied


def reset_sequences(engine) -> Dict[str, int]:
    """Advance each identity sequence past the highest id that was copied.

    Without this every sequence still points at 1 while row 1 already exists,
    so the first booking after the migration fails with a unique violation —
    on a database whose row counts all matched.
    """
    advanced: Dict[str, int] = {}
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            pk = list(table.primary_key.columns)
            if len(pk) != 1 or not pk[0].autoincrement:
                continue
            col = pk[0].name
            seq = conn.execute(
                text("SELECT pg_get_serial_sequence(:t, :c)"), {"t": table.name, "c": col}
            ).scalar()
            if seq is None:
                continue
            highest = conn.execute(
                text(f'SELECT COALESCE(MAX("{col}"), 0) FROM "{table.name}"')
            ).scalar_one()
            # is_called=true so the NEXT value is highest+1, not highest.
            conn.execute(text("SELECT setval(:s, :v, true)"), {"s": seq, "v": max(highest, 1)})
            advanced[table.name] = highest
    return advanced


def verify_migration(sqlite_path: Path, engine) -> Tuple[Dict[str, int], Dict[str, int], List[str]]:
    """Compare the two databases. Returns (sqlite counts, postgres counts,
    problems). A non-empty problems list means DO NOT CUT OVER."""
    before = sqlite_row_counts(sqlite_path)
    after = postgres_row_counts(engine)
    problems: List[str] = []

    for table_name in sorted(set(before) | set(after)):
        b, a = before.get(table_name), after.get(table_name)
        if b != a:
            problems.append(f"row count differs for {table_name}: sqlite={b} postgres={a}")

    # Sequences: the next id must be beyond every id already present, or the
    # first insert after cutover collides.
    with engine.connect() as conn:
        for table in SQLModel.metadata.sorted_tables:
            pk = list(table.primary_key.columns)
            if len(pk) != 1 or not pk[0].autoincrement:
                continue
            col = pk[0].name
            seq = conn.execute(
                text("SELECT pg_get_serial_sequence(:t, :c)"), {"t": table.name, "c": col}
            ).scalar()
            if seq is None:
                continue
            highest = conn.execute(
                text(f'SELECT COALESCE(MAX("{col}"), 0) FROM "{table.name}"')
            ).scalar_one()
            nextval = conn.execute(text("SELECT last_value FROM " + seq)).scalar_one()
            if highest and nextval < highest:
                problems.append(
                    f"sequence for {table.name}.{col} is behind the data: "
                    f"last_value={nextval} max id={highest}"
                )
    return before, after, problems


def migrate(sqlite_path: Path, database_url: str) -> Tuple[Dict[str, int], Dict[str, int]]:
    if not sqlite_path.exists():
        raise MigrationError(f"no database at {sqlite_path}")
    engine = create_engine(database_url)

    existing = inspect(engine).get_table_names()
    if existing:
        raise MigrationError(
            f"target already has {len(existing)} tables — refusing to migrate into a "
            "non-empty database, since a partial second run would duplicate rows"
        )

    SQLModel.metadata.create_all(engine)
    copy_rows(sqlite_path, engine)
    reset_sequences(engine)
    before, after, problems = verify_migration(sqlite_path, engine)
    if problems:
        raise MigrationError("verification failed:\n  " + "\n  ".join(problems))
    return before, after


if __name__ == "__main__":
    import os

    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set")
    src_counts, dst_counts = migrate(Path(sys.argv[1]), url)
    print(f"migrated {sum(dst_counts.values())} rows across {len(dst_counts)} tables — verified")
