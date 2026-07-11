from fastapi.testclient import TestClient
from sqlmodel import Session, select

import app as app_module
import db as db_module
import google_auth
import portal as portal_module
from db_models import Client


class _FakeRequest:
    """Minimal stand-in for the parts of Request the helper touches."""

    def __init__(self):
        self.session = {}


def _wire(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)


# ---- google_enabled ---------------------------------------------------------

def test_google_disabled_when_env_unset(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    assert google_auth.google_enabled() is False


def test_google_enabled_needs_both_vars(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id-only")
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    assert google_auth.google_enabled() is False
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    assert google_auth.google_enabled() is True


# ---- find-or-create helper --------------------------------------------------

def test_new_email_creates_passwordless_client_into_onboarding(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    request = _FakeRequest()
    with Session(test_engine) as session:
        resp = portal_module._login_or_create_by_email(request, session, "  New.Owner@Example.COM ")
        assert resp.headers["location"] == "/onboarding/business"

    with Session(test_engine) as session:
        created = session.exec(select(Client).where(Client.email == "new.owner@example.com")).first()
    assert created is not None
    assert created.password_hash is None  # Google is their sign-in
    assert request.session["client_id"] == created.id


def test_existing_live_client_goes_to_dashboard(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    with Session(test_engine) as session:
        session.add(Client(email="live@example.com", frontdesk_live=True))
        session.commit()

    request = _FakeRequest()
    with Session(test_engine) as session:
        resp = portal_module._login_or_create_by_email(request, session, "live@example.com")
    assert resp.headers["location"] == "/dashboard"


def test_existing_unfinished_client_resumes_onboarding(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    with Session(test_engine) as session:
        session.add(Client(email="wip@example.com", frontdesk_live=False))
        session.commit()

    request = _FakeRequest()
    with Session(test_engine) as session:
        resp = portal_module._login_or_create_by_email(request, session, "wip@example.com")
    assert resp.headers["location"] == "/onboarding/business"


def test_google_login_matches_existing_password_account_by_email(monkeypatch, test_engine):
    """A shop that signed up with email+password and later uses Google lands in
    the SAME account — one account per email, never a duplicate."""
    _wire(monkeypatch, test_engine)
    with Session(test_engine) as session:
        session.add(Client(email="both@example.com", password_hash="bcrypt-hash-here"))
        session.commit()
        original_id = session.exec(select(Client).where(Client.email == "both@example.com")).first().id

    request = _FakeRequest()
    with Session(test_engine) as session:
        portal_module._login_or_create_by_email(request, session, "both@example.com")
        count = len(session.exec(select(Client).where(Client.email == "both@example.com")).all())
    assert count == 1
    assert request.session["client_id"] == original_id


# ---- unconfigured routes degrade gracefully ---------------------------------

def test_google_login_route_redirects_when_unconfigured(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    client = TestClient(app_module.app)
    resp = client.get("/auth/google/login", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_google_callback_route_redirects_when_unconfigured(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    client = TestClient(app_module.app)
    resp = client.get("/auth/google/callback", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


# ---- button visibility follows configuration --------------------------------

def test_button_hidden_when_unconfigured(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    client = TestClient(app_module.app)
    assert "Continue with Google" not in client.get("/signup").text
    assert "Continue with Google" not in client.get("/login").text


def test_button_shown_when_configured(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    client = TestClient(app_module.app)
    assert "Continue with Google" in client.get("/signup").text
    assert "Continue with Google" in client.get("/login").text
