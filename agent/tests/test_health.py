"""/health -- there was no way to ask this process "are you actually working"
before this. An outage was previously discovered by a customer, not by the
platform. Three checks, two different consequences: database unreachable is
the one hard failure (503, worth Railway restarting over); a stale scheduler
heartbeat or an old backup is a soft signal (still 200, detail in the body --
restarting fixes neither and risks a restart loop over noise)."""

from datetime import datetime, timedelta

import app as app_module
from db_models import SchedulerHeartbeat
from fastapi.testclient import TestClient
from sqlmodel import Session


def test_health_is_public_no_auth_required(test_engine, monkeypatch):
    """Railway's healthcheck has no credentials -- the same public-surface
    rule test_public_surface.py pins for the rest of this boundary."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    client = TestClient(app_module.app)

    r = client.get("/health")

    assert r.status_code in (200, 503)  # reachable at all, no 401


def test_healthy_when_database_reachable_and_tick_recent(test_engine, monkeypatch):
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        s.add(SchedulerHeartbeat(id=1, last_tick_at=datetime.utcnow(), ok=True))
        s.commit()
    client = TestClient(app_module.app)

    r = client.get("/health")

    body = r.json()
    assert r.status_code == 200
    assert body["status"] == "ok"
    assert body["checks"]["database"]["ok"] is True
    assert body["checks"]["scheduler"]["ok"] is True


def test_the_one_hard_failure_is_database_unreachable(monkeypatch):
    """A database that can't be reached means every route is already broken
    -- the one case worth a 503 and a container restart."""
    from sqlalchemy import create_engine

    broken = create_engine("sqlite:////nonexistent-directory/does-not-exist.db")
    monkeypatch.setattr(app_module, "engine", broken)
    client = TestClient(app_module.app)

    r = client.get("/health")

    body = r.json()
    assert r.status_code == 503
    assert body["status"] == "down"
    assert body["checks"]["database"]["ok"] is False


def test_a_stale_scheduler_heartbeat_is_reported_but_stays_200(test_engine, monkeypatch):
    """Restarting the container would not fix a stuck scheduler any faster
    than the next tick would, and turning this into a hard failure risks a
    restart loop over what might just be one slow tick."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        s.add(
            SchedulerHeartbeat(id=1, last_tick_at=datetime.utcnow() - timedelta(hours=5), ok=True)
        )
        s.commit()
    client = TestClient(app_module.app)

    r = client.get("/health")

    body = r.json()
    assert r.status_code == 200
    assert body["checks"]["scheduler"]["ok"] is False
    assert body["checks"]["scheduler"]["age_seconds"] > 3600


def test_a_failed_last_tick_is_reported_but_stays_200(test_engine, monkeypatch):
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        s.add(
            SchedulerHeartbeat(
                id=1, last_tick_at=datetime.utcnow(), ok=False, error="RuntimeError: boom"
            )
        )
        s.commit()
    client = TestClient(app_module.app)

    r = client.get("/health")

    body = r.json()
    assert r.status_code == 200
    assert body["checks"]["scheduler"]["ok"] is False
    assert body["checks"]["scheduler"]["last_tick_error"] == "RuntimeError: boom"


def test_no_tick_ever_recorded_is_reported_but_stays_200(test_engine, monkeypatch):
    """A fresh deploy before its first scheduler tick must not read as down."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    client = TestClient(app_module.app)

    r = client.get("/health")

    body = r.json()
    assert r.status_code == 200
    assert body["checks"]["scheduler"]["ok"] is False
    assert "no tick" in body["checks"]["scheduler"]["detail"]
