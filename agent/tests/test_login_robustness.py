"""The login page must not be turn-into-a-500-able, and must not lose the
backup door when someone mistypes.

Both found 2026-09-01, both on the one page an owner reaches when something
has already gone wrong.
"""

import app as app_module
import portal
from auth import hash_password, verify_password
from db_models import Business
from sqlmodel import Session
from starlette.testclient import TestClient

# bcrypt raises above 72 bytes rather than truncating (changed in 4.0), and
# both auth functions passed the raw form value straight to the C call.
TOO_LONG = "x" * 200


def test_an_over_long_password_is_wrong_not_fatal():
    stored = hash_password("correct horse battery staple")

    assert verify_password(TOO_LONG, stored) is False


def test_an_over_long_password_can_still_be_set_and_verified():
    """Truncation, not rejection — so an account whose hash was made under
    bcrypt's older truncating behaviour keeps working."""
    stored = hash_password(TOO_LONG)

    assert verify_password(TOO_LONG, stored) is True
    assert verify_password(TOO_LONG + "different-tail", stored) is True  # both truncate the same


def test_a_malformed_hash_is_a_failed_login_not_a_crash():
    assert verify_password("anything", "not-a-bcrypt-hash") is False
    assert verify_password("anything", "") is False


def test_a_unicode_password_over_the_limit_does_not_crash():
    """The truncation lands at a byte offset and may split a multi-byte
    character. bcrypt hashes bytes, so that is fine — it must not raise."""
    emoji = "🔥" * 40  # 160 bytes

    stored = hash_password(emoji)

    assert verify_password(emoji, stored) is True


def _login(test_engine, monkeypatch, password):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(portal, "engine", test_engine)
    with Session(test_engine) as s:
        s.add(
            Business(
                business_name="Kestrel",
                trade="plumbing",
                email="owner@kestrel.test",
                password_hash=hash_password("pw12345"),
            )
        )
        s.commit()
    return TestClient(app_module.app).post(
        "/login", data={"email": "owner@kestrel.test", "password": password}
    )


def test_the_login_page_survives_an_over_long_password(test_engine, monkeypatch):
    """End to end: before the fix this was an unhandled 500 on a public page."""
    r = _login(test_engine, monkeypatch, TOO_LONG)

    assert r.status_code == 400
    assert "Invalid email or password" in r.text


def test_a_failed_login_keeps_the_google_sign_in_door(test_engine, monkeypatch):
    """google_enabled was passed on the GET but not on the error path, so a
    mistyped password silently removed the Google button — the backup door for
    an owner whose access link expired, gone at the one moment they need it."""
    monkeypatch.setattr(portal, "google_enabled", lambda: True)

    r = _login(test_engine, monkeypatch, "wrong-password")

    assert r.status_code == 400
    assert "google" in r.text.lower()
