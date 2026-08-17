"""The owner's calendar controls: reachable, honest, and never a dead control.

ARCHITECTURE.md invariant 9 — no dead controls: the connect button only renders
when Google OAuth is actually configured, because a button that silently does
nothing is worse than no button.
"""

import itertools

import app as app_module
import portal
from auth import hash_password
from db_models import Business
from fastapi.testclient import TestClient
from sqlmodel import Session

_EMAIL = (f"cal{i}@test.io" for i in itertools.count())


def _logged_in(test_engine, connected: bool = False):
    email = next(_EMAIL)
    with Session(test_engine) as s:
        b = Business(
            business_name="Ridgeline Plumbing",
            trade="Plumbing",
            hours="Mon-Sat 7am-7pm",
            email=email,
            password_hash=hash_password("pw12345"),
            timezone="America/New_York",
            google_refresh_token="stored-token" if connected else None,
        )
        s.add(b)
        s.commit()
        s.refresh(b)
        bid = b.id
    client = TestClient(app_module.app)
    client.post("/login", data={"email": email, "password": "pw12345"})
    return client, bid


# ---- routes ---------------------------------------------------------------


def test_the_calendar_routes_exist(session):
    """A route that 404s is a dead control by another name."""
    client = TestClient(app_module.app)
    with client:
        for path in (
            "/v2/dashboard/settings/calendar/connect",
            "/v2/dashboard/settings/calendar/callback",
        ):
            assert client.get(path, follow_redirects=False).status_code != 404, path
        assert (
            client.post(
                "/v2/dashboard/settings/calendar/disconnect", follow_redirects=False
            ).status_code
            != 404
        )


def test_calendar_routes_require_login(session):
    """Availability is business data — an anonymous request must not reach it."""
    client = TestClient(app_module.app)
    with client:
        for path in (
            "/v2/dashboard/settings/calendar/connect",
            "/v2/dashboard/settings/calendar/callback",
        ):
            resp = client.get(path, follow_redirects=False)
            assert resp.status_code == 303, path
            assert resp.headers["location"] == "/login", path


# ---- what the owner actually sees -----------------------------------------


def test_an_unconnected_business_is_told_roster_will_not_guess(test_engine, monkeypatch):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(portal, "engine", test_engine)
    monkeypatch.setattr(portal, "google_enabled", lambda: True)
    client, _ = _logged_in(test_engine, connected=False)

    html = client.get("/v2/dashboard/settings").text

    assert "Your calendar" in html
    assert "Not connected" in html
    assert "never guess a time" in html
    assert "/v2/dashboard/settings/calendar/connect" in html


def test_the_connect_button_is_hidden_when_oauth_is_not_configured(test_engine, monkeypatch):
    """Invariant 9: no dead controls. Without credentials the button would go
    nowhere, so it must not render at all."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(portal, "engine", test_engine)
    monkeypatch.setattr(portal, "google_enabled", lambda: False)
    client, _ = _logged_in(test_engine, connected=False)

    html = client.get("/v2/dashboard/settings").text

    assert "Your calendar" in html
    assert "/v2/dashboard/settings/calendar/connect" not in html
    assert "isn't available yet" in html


def test_a_connected_business_sees_read_only_stated_plainly(test_engine, monkeypatch):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(portal, "engine", test_engine)
    monkeypatch.setattr(portal, "google_enabled", lambda: True)
    client, _ = _logged_in(test_engine, connected=True)

    html = client.get("/v2/dashboard/settings").text

    assert "Connected" in html
    assert "read-only" in html
    assert "never adds, moves or deletes" in html
    assert "/v2/dashboard/settings/calendar/disconnect" in html


def test_disconnect_clears_the_token_and_stops_availability(test_engine, monkeypatch):
    """Disconnect must revoke Roster's access, not just hide the UI."""
    from calendar_provider import UnconnectedCalendarProvider, get_calendar_provider

    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(portal, "engine", test_engine)
    client, bid = _logged_in(test_engine, connected=True)

    client.post("/v2/dashboard/settings/calendar/disconnect", follow_redirects=False)

    with Session(test_engine) as s:
        business = s.get(Business, bid)
        assert business.google_refresh_token is None
        assert business.google_calendar_connected_at is None
        assert isinstance(get_calendar_provider(business), UnconnectedCalendarProvider)


# ---- honesty of the notices ------------------------------------------------


def test_every_calendar_notice_says_what_actually_happened():
    """A failed connect must never read as a quiet success — the owner would
    wait for availability that is never coming."""
    assert set(portal.CALENDAR_NOTICES) == {
        "connected",
        "disconnected",
        "failed",
        "no_refresh_token",
        "unavailable",
    }
    for key, text in portal.CALENDAR_NOTICES.items():
        assert text.strip(), key
    assert "not connected" in portal.CALENDAR_NOTICES["no_refresh_token"].lower()
    assert "nothing was changed" in portal.CALENDAR_NOTICES["failed"].lower()
