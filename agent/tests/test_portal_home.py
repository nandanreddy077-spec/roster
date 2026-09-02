"""Home — the customer dashboard's front door. Renders a single
BriefingWorkspace (workspace.py). No template contains summarisation logic;
the route never touches DepartmentInterest or metrics.py directly.

This page WAS /v2/dashboard/briefing. On 2026-09-01 the founder collapsed
five destinations to three: Overview, Departments and the Briefing were three
renderings of the same department cards, and the Briefing was the only one of
the three that answered a question, so it moved to the front door and the
other two collapsed into it. These tests moved with it, unchanged except for
the URL — the behaviour they guard did not change, only where it lives."""

import app as app_module
import portal
from auth import hash_password
from db_models import Business, Job, OwnerNotification
from deployment import deploy_role
from sqlmodel import Session

_EMAIL = iter(f"bp-{n}@test.io" for n in range(1000))


def _client_for(test_engine, monkeypatch):
    from starlette.testclient import TestClient

    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(portal, "engine", test_engine)
    email = next(_EMAIL)
    with Session(test_engine) as s:
        b = Business(
            business_name="Ridgeline Plumbing",
            trade="Plumbing",
            email=email,
            password_hash=hash_password("pw12345"),
        )
        s.add(b)
        s.commit()
        s.refresh(b)
        bid = b.id
    client = TestClient(app_module.app)
    client.post("/login", data={"email": email, "password": "pw12345"})
    return client, bid


def test_requires_a_session(test_engine, monkeypatch):
    from starlette.testclient import TestClient

    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(portal, "engine", test_engine)

    r = TestClient(app_module.app).get("/v2/dashboard", follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_the_old_briefing_url_still_lands_on_home(test_engine, monkeypatch):
    """The URL is in owners' text messages and browser history. A 404 there
    would read as the product breaking, not as a menu getting shorter."""
    from starlette.testclient import TestClient

    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(portal, "engine", test_engine)

    r = TestClient(app_module.app).get("/v2/dashboard/briefing", follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"] == portal.DASHBOARD_HOME


def test_a_brand_new_business_sees_an_honest_summary(test_engine, monkeypatch):
    client, _ = _client_for(test_engine, monkeypatch)

    r = client.get("/v2/dashboard")

    assert r.status_code == 200
    assert "Nothing staffed yet" in r.text


def test_home_is_headed_by_the_owners_own_business(test_engine, monkeypatch):
    """Replaces the old provisional-Briefing-label guard. The page no longer
    carries a Roster-invented name at all — it is headed by the thing the
    owner actually recognises."""
    client, _ = _client_for(test_engine, monkeypatch)

    r = client.get("/v2/dashboard")

    assert "Ridgeline Plumbing" in r.text


def test_what_needs_the_owner_is_visually_separate_from_what_went_well(test_engine, monkeypatch):
    """The old Briefing flattened attention / working_well / growth into one
    undifferentiated <ul>, so an escalation looked exactly like a booked-jobs
    count. The view model has always sorted by kind; the page now renders that
    distinction instead of discarding it."""
    from db_models import OwnerNotification

    client, bid = _client_for(test_engine, monkeypatch)
    with Session(test_engine) as s:
        deploy_role(s, bid, "frontdesk")
        s.add(
            OwnerNotification(
                business_id=bid,
                kind="escalation",
                source="sms",
                message="Customer asked something Frontdesk could not answer",
            )
        )
        s.commit()

    body = client.get("/v2/dashboard").text

    assert "Needs you" in body
    assert "portal-attention" in body


def test_a_quiet_day_says_so_rather_than_showing_an_empty_block(test_engine, monkeypatch):
    client, bid = _client_for(test_engine, monkeypatch)
    with Session(test_engine) as s:
        deploy_role(s, bid, "frontdesk")
        s.commit()

    body = client.get("/v2/dashboard").text

    assert "Nothing needs you right now" in body
    assert "Needs you" not in body


def test_every_headline_number_states_its_time_frame(test_engine, monkeypatch):
    """A bare count with no window was the most-asked "what does this even
    mean?" on the old dashboard. metrics defaults since=None, so the honest
    caption is the lifetime one — never an invented week."""
    client, bid = _client_for(test_engine, monkeypatch)
    with Session(test_engine) as s:
        deploy_role(s, bid, "frontdesk")
        s.add(
            Job(
                business_id=bid,
                customer_phone="+15125550009",
                service_type="Drain cleaning",
                urgency="routine",
            )
        )
        s.commit()

    body = client.get("/v2/dashboard").text

    assert "Jobs booked" in body
    assert portal.METRIC_WINDOW in body


def test_an_active_department_links_into_its_workspace(test_engine, monkeypatch):
    client, bid = _client_for(test_engine, monkeypatch)
    with Session(test_engine) as s:
        deploy_role(s, bid, "frontdesk")
        s.add(
            Job(
                business_id=bid,
                customer_phone="+15125550001",
                service_type="Drain cleaning",
                urgency="routine",
            )
        )
        s.commit()

    body = client.get("/v2/dashboard").text

    assert 'href="/v2/dashboard/departments/customer_service"' in body
    assert "Jobs booked" in body


def test_a_growth_nudge_links_into_expansion(test_engine, monkeypatch):
    client, _ = _client_for(test_engine, monkeypatch)

    body = client.get("/v2/dashboard").text

    assert 'href="/v2/dashboard/departments/sales/expand"' in body


def test_viewing_the_briefing_never_records_interest(test_engine, monkeypatch):
    """A growth nudge is a suggestion to read, not an action the page takes on
    the owner's behalf (Phase 3's guard, exercised through this route)."""
    from db_models import DepartmentInterest
    from sqlmodel import select

    client, bid = _client_for(test_engine, monkeypatch)

    client.get("/v2/dashboard")

    with Session(test_engine) as s:
        assert s.exec(select(DepartmentInterest)).all() == []


def test_recent_notifications_appear(test_engine, monkeypatch):
    client, bid = _client_for(test_engine, monkeypatch)
    with Session(test_engine) as s:
        s.add(
            OwnerNotification(
                business_id=bid,
                kind="job_booked",
                source="sms_booking",
                message="Frontdesk just booked a job",
            )
        )
        s.commit()

    body = client.get("/v2/dashboard").text

    assert "Job booked" in body


def test_no_upgrade_or_pricing_language_appears(test_engine, monkeypatch):
    client, _ = _client_for(test_engine, monkeypatch)

    body = client.get("/v2/dashboard").text

    for word in ("upgrade", "plan", "$", "price", "billing"):
        assert word.lower() not in body.lower(), f"{word!r} makes this feel like a sales page"
