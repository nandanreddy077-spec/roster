import app as app_module
import db as db_module
import google_auth
import portal as portal_module
from conftest import provisioned_business
from db_models import Business
from fastapi.testclient import TestClient
from sqlmodel import Session, select


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


# ---- login-only helper (creation removed 2026-08-06) ------------------------


def test_an_unknown_email_creates_nothing(monkeypatch, test_engine):
    """Google used to create a Business on first sight, which made it the
    SECOND public self-registration door — closing /signup alone would have
    left it wide open, since every Google account is a verified email."""
    _wire(monkeypatch, test_engine)
    request = _FakeRequest()
    with Session(test_engine) as session:
        resp = portal_module._login_by_email(request, session, "  Stranger@Example.COM ")

    assert resp.status_code == 400
    assert "client_id" not in request.session
    with Session(test_engine) as session:
        assert session.exec(select(Business)).all() == []


def test_an_email_the_founder_put_on_a_business_signs_in(monkeypatch, test_engine):
    """The founder setting the owner's address on the client page is what makes
    Google work for them — a second door into an EXISTING business."""
    _wire(monkeypatch, test_engine)
    business_id = provisioned_business(test_engine, email="owner@example.com")

    request = _FakeRequest()
    with Session(test_engine) as session:
        resp = portal_module._login_by_email(request, session, "Owner@Example.com ")

    assert resp.headers["location"] == portal_module.DASHBOARD_HOME
    assert request.session["client_id"] == business_id


def test_a_provisioned_shop_is_never_sent_to_onboarding(monkeypatch, test_engine):
    """frontdesk_live is False on every founder-provisioned business. It used
    to decide routing, so these owners were bounced into a wizard that no
    longer exists."""
    _wire(monkeypatch, test_engine)
    provisioned_business(test_engine, email="wip@example.com", frontdesk_live=False)

    request = _FakeRequest()
    with Session(test_engine) as session:
        resp = portal_module._login_by_email(request, session, "wip@example.com")
    assert resp.headers["location"] == portal_module.DASHBOARD_HOME


def test_google_matches_an_existing_password_account_by_email(monkeypatch, test_engine):
    """One account per email, never a duplicate."""
    _wire(monkeypatch, test_engine)
    business_id = provisioned_business(
        test_engine, email="both@example.com", password_hash="bcrypt-hash-here"
    )

    request = _FakeRequest()
    with Session(test_engine) as session:
        portal_module._login_by_email(request, session, "both@example.com")
        count = len(
            session.exec(select(Business).where(Business.email == "both@example.com")).all()
        )
    assert count == 1
    assert request.session["client_id"] == business_id


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
    assert "Continue with Google" not in client.get("/login").text


def test_button_shown_when_configured(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    client = TestClient(app_module.app)
    assert "Continue with Google" in client.get("/login").text


# ---- the callback route itself, not just its helper --------------------------


def test_configured_callback_signs_in_a_known_owner(monkeypatch, test_engine):
    """Drives the real route. The helper tests above all call _login_by_email
    directly, so when it was renamed the callback kept calling the old name and
    every test still passed — the route would have raised NameError on the
    first real Google sign-in."""
    _wire(monkeypatch, test_engine)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    provisioned_business(test_engine, email="owner@example.com")

    class _FakeGoogle:
        async def authorize_access_token(self, request):
            return {"userinfo": {"email": "owner@example.com", "email_verified": True}}

    monkeypatch.setattr(
        portal_module, "get_oauth", lambda: type("_O", (), {"google": _FakeGoogle()})()
    )

    client = TestClient(app_module.app)
    response = client.get("/auth/google/callback", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == portal_module.DASHBOARD_HOME


def test_configured_callback_turns_away_an_unknown_email(monkeypatch, test_engine):
    """Google must not mint a business — the second self-registration door."""
    _wire(monkeypatch, test_engine)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")

    class _FakeGoogle:
        async def authorize_access_token(self, request):
            return {"userinfo": {"email": "stranger@example.com", "email_verified": True}}

    monkeypatch.setattr(
        portal_module, "get_oauth", lambda: type("_O", (), {"google": _FakeGoogle()})()
    )

    client = TestClient(app_module.app)
    response = client.get("/auth/google/callback", follow_redirects=False)
    assert response.status_code == 400
    with Session(test_engine) as session:
        assert session.exec(select(Business)).all() == []
