"""The deployed app has exactly two faces: a public one (landing, signup flow,
webhooks) and a founder-only one (/clients dashboard, behind HTTP Basic).
These tests pin that boundary — the dashboard must never serve without
credentials, and the public surface must never *require* them."""
import base64

from fastapi.testclient import TestClient

import app as app_module
import db as db_module
import portal as portal_module


def _basic(password: str) -> dict:
    token = base64.b64encode(f"admin:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


# ---- Dashboard: locked without the right password ---------------------------


def test_clients_requires_auth(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "hunter2")
    client = TestClient(app_module.app)
    response = client.get("/clients")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Basic")


def test_clients_rejects_wrong_password(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "hunter2")
    client = TestClient(app_module.app)
    response = client.get("/clients", headers=_basic("wrong"))
    assert response.status_code == 401


def test_clients_allows_correct_password(monkeypatch, test_engine):
    monkeypatch.setenv("ADMIN_PASSWORD", "hunter2")
    monkeypatch.setattr(app_module, "engine", test_engine)
    client = TestClient(app_module.app)
    response = client.get("/clients", headers=_basic("hunter2"))
    assert response.status_code == 200


def test_clients_subpaths_are_protected(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "hunter2")
    client = TestClient(app_module.app)
    assert client.get("/clients/new").status_code == 401
    assert client.post("/clients/1/chat", data={"message": "hi"}).status_code == 401


def test_dashboard_fails_closed_without_password(monkeypatch):
    """Forgetting ADMIN_PASSWORD on a fresh deploy must lock the dashboard,
    not open it — client conversations are behind this door."""
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    client = TestClient(app_module.app)
    response = client.get("/clients")
    assert response.status_code == 503
    assert "ADMIN_PASSWORD" in response.text


# ---- Public surface: stays public -------------------------------------------


def test_landing_served_at_root(monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    client = TestClient(app_module.app)
    response = client.get("/")
    assert response.status_code == 200
    assert "Roster" in response.text
    assert response.headers["content-type"].startswith("text/html")


def test_landing_stylesheet_served(monkeypatch):
    client = TestClient(app_module.app)
    response = client.get("/styles.css")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")


def test_head_request_succeeds_with_empty_body(monkeypatch):
    """Crawlers/uptime checks probe with HEAD before GET; this FastAPI
    version doesn't add it to GET routes on its own (see HeadAsGetMiddleware)."""
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    client = TestClient(app_module.app)
    response = client.head("/")
    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-type"].startswith("text/html")


def test_robots_txt_points_crawlers_at_sitemap_and_blocks_dashboard(monkeypatch):
    client = TestClient(app_module.app)
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert "Disallow: /clients" in response.text
    assert "Sitemap: https://rosterhires.com/sitemap.xml" in response.text


def test_sitemap_lists_the_evergreen_pages(monkeypatch):
    client = TestClient(app_module.app)
    response = client.get("/sitemap.xml")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert "<loc>https://rosterhires.com/</loc>" in response.text
    assert "<loc>https://rosterhires.com/roster</loc>" in response.text


def test_the_login_page_is_public(monkeypatch):
    """The customer's own door. It replaced /signup as the public entry point
    when self-registration closed — /signup now just bounces to the landing
    (see test_signup_is_closed_but_not_behind_the_founder_gate)."""
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    client = TestClient(app_module.app)
    response = client.get("/login")
    assert response.status_code == 200
    assert "Roster" in response.text


def test_signup_is_closed_but_not_behind_the_founder_gate(monkeypatch, test_engine):
    """Self-registration is closed, so /signup redirects to the landing. It
    must redirect, not 401 — it's a public URL people have bookmarked, and the
    founder's HTTP-Basic gate must never be what they hit."""
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    response = client.post(
        "/signup",
        data={"email": "auth-test@example.com", "password": "hunter22"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == portal_module.SIGNUP_CLOSED_REDIRECT


def test_api_docs_are_not_published(monkeypatch):
    """FastAPI's defaults hand out a complete map of every founder and portal
    route to anyone who asks. The routes are authenticated, so this is not a
    hole — it's the floor plan, for free (2026-08-04 penetration test, rec 3).
    Roster has no external API consumers."""
    client = TestClient(app_module.app)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404, f"{path} is still published"


def test_sms_webhook_is_public(monkeypatch, test_engine):
    """Twilio can't do Basic auth — the webhook authenticates by routing to a
    known inbound number and must not be caught by the dashboard gate."""
    monkeypatch.setenv("ADMIN_PASSWORD", "hunter2")
    monkeypatch.setattr(app_module, "engine", test_engine)
    client = TestClient(app_module.app)
    response = client.post(
        "/webhook/sms",
        data={"From": "+15550001111", "To": "+15559998888", "Body": "hello"},
    )
    # Unknown number → polite TwiML fallback, but crucially not 401/503.
    assert response.status_code == 200
    assert "isn't set up" in response.text


def test_sms_webhook_stays_silent_once_trial_cap_exhausted(monkeypatch, test_engine):
    monkeypatch.setenv("ADMIN_PASSWORD", "hunter2")
    monkeypatch.setattr(app_module, "engine", test_engine)
    from sqlmodel import Session

    from db_models import Business

    with Session(test_engine) as session:
        session.add(
            Business(
                email="owner@example.com", password_hash="x", business_name="Ridgeline",
                trade="Plumbing", services_json="[]", hours="9-5", escalation_phone="555",
                inbound_number="+15559998888",
                trial_spend_cents=2200, trial_cap_cents=2000, trial_soft_buffer_cents=200,
            )
        )
        session.commit()

    client = TestClient(app_module.app)
    response = client.post(
        "/webhook/sms",
        data={"From": "+15550001111", "To": "+15559998888", "Body": "hello"},
    )
    assert response.status_code == 200
    assert "<Message>" not in response.text
