"""Database snapshots, and proof that each one is restorable.

Until now there was no backup of any kind. Production runs SQLite on a Railway
volume (verified 2026-08-11: no DATABASE_URL in the environment, roster.db
present at /data), Railway does not back volumes up, and the only file that
resembled a backup was a hand-made JSON dump of one business from six days
earlier. If that volume died, every customer's bookings died with it.

WHY NOT `cp roster.db backup.db`: the database runs in WAL mode, so at any
moment committed rows may live in roster.db-wal rather than the main file. A
plain copy can capture a torn database that opens fine and is silently missing
the last transactions. sqlite3's own backup API walks the paged database under
a read lock and produces a consistent snapshot with the WAL folded in, while
writers keep working.

A BACKUP NOBODY HAS RESTORED IS NOT A BACKUP. Every snapshot taken here is
immediately reopened, integrity-checked, and row-counted against the source
before it is allowed to count as success — see verify_backup. A snapshot that
fails verification is deleted rather than left to look like protection.

RESIDUAL RISK, stated plainly: these land on the same volume as the database.
That defends against corruption, a bad migration, or a mistaken delete. It does
NOT defend against losing the volume itself. Pulling a snapshot off-box is a
documented manual step in the runbook until there is somewhere off-site to put
it, and that gap is the reason the runbook exists.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Keep a month of dailies. The database is ~180 KB against a 4.6 GB volume, so
# retention costs nothing and the failure this protects against — noticing a
# corruption a fortnight late — is entirely plausible at pilot scale.
KEEP_SNAPSHOTS = 30

SNAPSHOT_PREFIX = "roster-"
SNAPSHOT_SUFFIX = ".db"


class BackupError(RuntimeError):
    """A snapshot could not be taken, or could not be proven restorable."""


def _table_names(conn: sqlite3.Connection) -> List[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return sorted(r[0] for r in rows)


def row_counts(db_path: Path) -> Dict[str, int]:
    """Every table and its row count. The comparison key for a restore drill:
    two databases that agree here hold the same records, whatever else differs.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in _table_names(conn)
        }
    finally:
        conn.close()


def verify_backup(snapshot: Path, source: Optional[Path] = None) -> Dict[str, int]:
    """Prove a snapshot is a database, is not corrupt, and holds what the
    source held. Raises BackupError rather than returning a bool: a caller that
    forgets to check a boolean ships an unverified backup, and this is the one
    place in the codebase where that must be impossible to do by accident.
    """
    if not snapshot.exists() or snapshot.stat().st_size == 0:
        raise BackupError(f"snapshot missing or empty: {snapshot}")

    # Every sqlite failure becomes a BackupError. A file that is not a database
    # at all raises DatabaseError on connect, and letting that escape would slip
    # straight past take_snapshot's `except BackupError` — leaving the corrupt
    # snapshot on disk, looking like protection.
    try:
        conn = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
        if result != "ok":
            raise BackupError(f"integrity check failed for {snapshot}: {result}")
        counts = row_counts(snapshot)
    except sqlite3.Error as e:
        raise BackupError(f"snapshot is not a readable database: {snapshot}: {e}") from e
    if source is not None:
        expected = row_counts(source)
        if counts != expected:
            differing = {
                t: (expected.get(t), counts.get(t))
                for t in set(expected) | set(counts)
                if expected.get(t) != counts.get(t)
            }
            raise BackupError(f"snapshot does not match source, (source, snapshot): {differing}")
    return counts


def take_snapshot(
    source: Path, dest_dir: Path, now: Optional[datetime] = None
) -> Tuple[Path, Dict[str, int]]:
    """Consistent snapshot of a live SQLite database, verified before returning.

    Uses sqlite3's backup API rather than a file copy so WAL content is folded
    in and writers are never blocked. A snapshot that fails verification is
    deleted — a corrupt file that looks like a backup is worse than no file,
    because it is only discovered during an actual restore.
    """
    if not source.exists():
        raise BackupError(f"no database at {source}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.utcnow()).strftime("%Y%m%dT%H%M%SZ")
    snapshot = dest_dir / f"{SNAPSHOT_PREFIX}{stamp}{SNAPSHOT_SUFFIX}"

    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(snapshot)
    try:
        src.backup(dst)
    except Exception as e:  # noqa: BLE001 — re-raised as BackupError below
        snapshot.unlink(missing_ok=True)
        raise BackupError(f"snapshot failed: {e}") from e
    finally:
        dst.close()
        src.close()

    try:
        counts = verify_backup(snapshot, source)
    except BackupError:
        snapshot.unlink(missing_ok=True)
        raise
    return snapshot, counts


def existing_snapshots(dest_dir: Path) -> List[Path]:
    if not dest_dir.exists():
        return []
    return sorted(dest_dir.glob(f"{SNAPSHOT_PREFIX}*{SNAPSHOT_SUFFIX}"))


def prune(dest_dir: Path, keep: int = KEEP_SNAPSHOTS) -> List[Path]:
    """Drop the oldest snapshots beyond `keep`. Names sort chronologically
    because the timestamp is ISO-ordered, so no stat() call is needed."""
    snapshots = existing_snapshots(dest_dir)
    removed = snapshots[: max(0, len(snapshots) - keep)]
    for path in removed:
        path.unlink(missing_ok=True)
    return removed


def run(now: Optional[datetime] = None) -> Optional[Path]:
    """Take today's snapshot. Called by the scheduler; safe to run by hand.

    Returns None on a Postgres deployment rather than raising: after the
    migration this module's job moves to the platform's own managed backups,
    and a scheduler tick must not start failing the day that happens.
    """
    from db import DATA_DIR, engine

    if engine.dialect.name != "sqlite":
        print("[backup] not a SQLite deployment — managed backups apply, skipping")
        return None

    source = DATA_DIR / "roster.db"
    dest_dir = Path(os.environ.get("ROSTER_BACKUP_DIR") or (DATA_DIR / "backups"))
    snapshot, counts = take_snapshot(source, dest_dir, now=now)
    removed = prune(dest_dir)
    total = sum(counts.values())
    print(
        f"[backup] {snapshot.name} verified — {total} rows across {len(counts)} tables, "
        f"{len(existing_snapshots(dest_dir))} kept, {len(removed)} pruned"
    )
    return snapshot


if __name__ == "__main__":
    run()
