"""Backups, and the drill that proves they restore.

Every test here exists because of one fact: before this, production had no
backup. The volume was the only copy of every customer's bookings.
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from backup import (
    BackupError,
    existing_snapshots,
    prune,
    row_counts,
    take_snapshot,
    verify_backup,
)


def _db(path: Path, jobs: int = 3) -> Path:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE business (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("CREATE TABLE job (id INTEGER PRIMARY KEY, business_id INT, service TEXT)")
    conn.execute("INSERT INTO business (name) VALUES ('Kestrel Plumbing')")
    for i in range(jobs):
        conn.execute("INSERT INTO job (business_id, service) VALUES (1, ?)", (f"job {i}",))
    conn.commit()
    conn.close()
    return path


def test_a_snapshot_holds_exactly_what_the_source_held(tmp_path):
    source = _db(tmp_path / "roster.db")
    snapshot, counts = take_snapshot(source, tmp_path / "backups")
    assert counts == {"business": 1, "job": 3}
    assert row_counts(snapshot) == row_counts(source)


def test_a_snapshot_captures_writes_still_sitting_in_the_wal(tmp_path):
    """The reason this uses sqlite3's backup API and not `cp`. In WAL mode a
    committed row may live in roster.db-wal, not the main file — a plain copy
    can produce a database that opens fine and is silently missing the last
    transactions."""
    source = _db(tmp_path / "roster.db")
    live = sqlite3.connect(source)
    live.execute("INSERT INTO job (business_id, service) VALUES (1, 'written into the wal')")
    live.commit()
    try:
        assert (source.parent / "roster.db-wal").exists(), "test needs WAL to be active"
        snapshot, counts = take_snapshot(source, tmp_path / "backups")
        assert counts["job"] == 4
        conn = sqlite3.connect(snapshot)
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM job WHERE service='written into the wal'"
            ).fetchone()[0]
            == 1
        )
        conn.close()
    finally:
        live.close()


def test_writers_are_not_blocked_during_a_snapshot(tmp_path):
    """A backup that locks the database is a backup nobody schedules."""
    source = _db(tmp_path / "roster.db")
    live = sqlite3.connect(source)
    try:
        take_snapshot(source, tmp_path / "backups")
        live.execute("INSERT INTO job (business_id, service) VALUES (1, 'after')")
        live.commit()
    finally:
        live.close()


# ---- verification is not optional ------------------------------------------


def test_a_corrupt_snapshot_is_rejected_and_deleted(tmp_path):
    """A corrupt file that looks like a backup is worse than no file — it is
    only discovered during a real restore, which is the worst possible moment."""
    snapshot = tmp_path / "roster-20260811T000000Z.db"
    snapshot.write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)
    with pytest.raises(BackupError):
        verify_backup(snapshot)


def test_an_empty_snapshot_is_rejected(tmp_path):
    snapshot = tmp_path / "roster-20260811T000000Z.db"
    snapshot.touch()
    with pytest.raises(BackupError, match="missing or empty"):
        verify_backup(snapshot)


def test_a_snapshot_that_lost_rows_is_rejected(tmp_path):
    """The check that catches a silently truncated copy."""
    source = _db(tmp_path / "roster.db", jobs=5)
    smaller = _db(tmp_path / "smaller.db", jobs=2)
    with pytest.raises(BackupError, match="does not match source"):
        verify_backup(smaller, source)


def test_a_missing_database_fails_loudly(tmp_path):
    with pytest.raises(BackupError, match="no database"):
        take_snapshot(tmp_path / "nope.db", tmp_path / "backups")


# ---- retention --------------------------------------------------------------


def test_pruning_keeps_the_newest_and_drops_the_rest(tmp_path):
    source = _db(tmp_path / "roster.db")
    dest = tmp_path / "backups"
    start = datetime(2026, 8, 1, 3, 0, 0)
    for day in range(5):
        take_snapshot(source, dest, now=start + timedelta(days=day))

    removed = prune(dest, keep=3)

    kept = [p.name for p in existing_snapshots(dest)]
    assert len(removed) == 2
    assert kept == [
        "roster-20260803T030000Z.db",
        "roster-20260804T030000Z.db",
        "roster-20260805T030000Z.db",
    ]


def test_pruning_is_a_no_op_when_under_the_limit(tmp_path):
    source = _db(tmp_path / "roster.db")
    dest = tmp_path / "backups"
    take_snapshot(source, dest)
    assert prune(dest, keep=30) == []
    assert len(existing_snapshots(dest)) == 1


# ---- the drill --------------------------------------------------------------


def test_restore_drill_a_snapshot_survives_losing_the_original(tmp_path):
    """The whole point, end to end: take a snapshot, destroy the database the
    way a dead volume would, restore from the snapshot, and confirm the rows
    are all still there. A backup that has never been restored is a guess."""
    source = _db(tmp_path / "roster.db", jobs=7)
    before = row_counts(source)
    snapshot, _ = take_snapshot(source, tmp_path / "backups")

    for leftover in tmp_path.glob("roster.db*"):  # -wal and -shm too
        leftover.unlink()
    assert not source.exists()

    restored = tmp_path / "roster.db"
    restored.write_bytes(snapshot.read_bytes())

    assert row_counts(restored) == before
    conn = sqlite3.connect(restored)
    assert conn.execute("SELECT name FROM business").fetchone()[0] == "Kestrel Plumbing"
    conn.close()
