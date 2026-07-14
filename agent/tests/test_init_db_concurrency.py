import threading

from db import init_db


def test_concurrent_init_db_does_not_crash():
    """Regression for the boot crash: uvicorn's 4 workers each call init_db()
    on the same SQLite file at once and raced on create_all
    ("table customer already exists"), crashing workers on boot. init_db now
    serializes behind a cross-process file lock, so concurrent calls must all
    complete without raising."""
    errors = []

    def run():
        try:
            init_db()
        except Exception as e:  # noqa: BLE001 — the test's whole point is "did it raise?"
            errors.append(repr(e))

    threads = [threading.Thread(target=run) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"init_db raced under concurrency: {errors}"
