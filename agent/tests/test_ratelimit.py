"""Login brute-force + public-form spam throttle (docs/PRODUCTION_READINESS.md
P1-6). Deliberately small — a process-local sliding window, not a dependency.
"""

import app as app_module
import db as db_module
import portal as portal_module
import ratelimit
from auth import hash_password
from conftest import DASH_AUTH
from db_models import AccessRequest, Business
from fastapi.testclient import TestClient
from sqlmodel import Session, select


def test_allow_lets_the_first_n_through_then_blocks():
    for _ in range(3):
        assert ratelimit.allow("k", 3, 60) is True
    assert ratelimit.allow("k", 3, 60) is False


def test_a_blocked_key_stays_blocked_until_the_window_passes(monkeypatch):
    t = [1000.0]
    for _ in range(3):
        assert ratelimit.allow("k", 3, 60, now=t[0]) is True
    assert ratelimit.allow("k", 3, 60, now=t[0]) is False
    # still inside the window
    assert ratelimit.allow("k", 3, 60, now=t[0] + 59) is False
    # window elapsed — the old hits age out
    assert ratelimit.allow("k", 3, 60, now=t[0] + 61) is True


def test_keys_are_independent():
    for _ in range(3):
        ratelimit.allow("a", 3, 60)
    assert ratelimit.allow("a", 3, 60) is False
    assert ratelimit.allow("b", 3, 60) is True


def _wire(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)


def test_login_throttles_after_repeated_failures(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    with Session(test_engine) as s:
        s.add(
            Business(
                business_name="Co",
                trade="HVAC",
                email="o@s.com",
                password_hash=hash_password("correct-horse"),
            )
        )
        s.commit()
    client = TestClient(app_module.app)

    for _ in range(10):
        r = client.post(
            "/login", data={"email": "o@s.com", "password": "wrong"}, follow_redirects=False
        )
        assert r.status_code == 400  # invalid creds, not yet throttled

    r = client.post(
        "/login", data={"email": "o@s.com", "password": "wrong"}, follow_redirects=False
    )
    assert r.status_code == 429

    # Even the CORRECT password is refused while throttled — the point is to
    # stop the guessing, and a real owner waits a few minutes.
    r = client.post(
        "/login", data={"email": "o@s.com", "password": "correct-horse"}, follow_redirects=False
    )
    assert r.status_code == 429


def test_request_access_spam_is_dropped_but_looks_like_success(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    client = TestClient(app_module.app)

    for _ in range(5):
        r = client.post(
            "/request-access", data={"name": "A", "phone": "555"}, follow_redirects=False
        )
        assert r.headers["location"] == "/thanks"

    r = client.post(
        "/request-access", data={"name": "spammer", "phone": "555"}, follow_redirects=False
    )
    assert r.headers["location"] == "/thanks"  # identical response

    with Session(test_engine) as s:
        assert len(s.exec(select(AccessRequest)).all()) == 5  # the 6th wrote nothing


def test_founder_console_is_not_rate_limited(monkeypatch, test_engine):
    """The founder does bulk work in /clients — Basic-auth'd, trusted, not a
    surface a stranger can reach. Must not get throttled."""
    _wire(monkeypatch, test_engine)
    client = TestClient(app_module.app)
    for _ in range(30):
        assert client.get("/clients", headers=DASH_AUTH).status_code == 200
