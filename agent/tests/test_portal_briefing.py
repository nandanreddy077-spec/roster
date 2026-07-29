"""The Briefing route and template — renders a single BriefingWorkspace
(workspace.py). No template contains summarisation logic; the route never
touches DepartmentInterest or metrics.py directly."""
from sqlmodel import Session

import app as app_module
import portal
from auth import hash_password
from db_models import Business, Job, OwnerNotification
from deployment import deploy_role

_EMAIL = iter(f"bp-{n}@test.io" for n in range(1000))


def _client_for(test_engine, monkeypatch):
    from starlette.testclient import TestClient

    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(portal, "engine", test_engine)
    email = next(_EMAIL)
    with Session(test_engine) as s:
        b = Business(business_name="Ridgeline Plumbing", trade="Plumbing",
                     email=email, password_hash=hash_password("pw12345"))
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

    r = TestClient(app_module.app).get("/v2/dashboard/briefing", follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_a_brand_new_business_sees_an_honest_summary(test_engine, monkeypatch):
    client, _ = _client_for(test_engine, monkeypatch)

    r = client.get("/v2/dashboard/briefing")

    assert r.status_code == 200
    assert "Nothing staffed yet" in r.text


def test_the_label_never_hardcodes_the_provisional_name(test_engine, monkeypatch):
    """test_briefing_label.py already guards this globally; this just proves
    the new template actually renders the Jinja global rather than nothing."""
    client, _ = _client_for(test_engine, monkeypatch)

    r = client.get("/v2/dashboard/briefing")

    assert portal.BRIEFING_LABEL in r.text


def test_an_active_department_links_into_its_workspace(test_engine, monkeypatch):
    client, bid = _client_for(test_engine, monkeypatch)
    with Session(test_engine) as s:
        deploy_role(s, bid, "frontdesk")
        s.add(Job(business_id=bid, customer_phone="+15125550001",
                   service_type="Drain cleaning", urgency="routine"))
        s.commit()

    body = client.get("/v2/dashboard/briefing").text

    assert 'href="/v2/dashboard/departments/customer_service"' in body
    assert "Jobs booked" in body


def test_a_growth_nudge_links_into_expansion(test_engine, monkeypatch):
    client, _ = _client_for(test_engine, monkeypatch)

    body = client.get("/v2/dashboard/briefing").text

    assert 'href="/v2/dashboard/departments/sales/expand"' in body


def test_viewing_the_briefing_never_records_interest(test_engine, monkeypatch):
    """A growth nudge is a suggestion to read, not an action the page takes on
    the owner's behalf (Phase 3's guard, exercised through this route)."""
    from sqlmodel import select

    from db_models import DepartmentInterest

    client, bid = _client_for(test_engine, monkeypatch)

    client.get("/v2/dashboard/briefing")

    with Session(test_engine) as s:
        assert s.exec(select(DepartmentInterest)).all() == []


def test_recent_notifications_appear(test_engine, monkeypatch):
    client, bid = _client_for(test_engine, monkeypatch)
    with Session(test_engine) as s:
        s.add(OwnerNotification(business_id=bid, kind="job_booked", source="sms_booking",
                                 message="Frontdesk just booked a job"))
        s.commit()

    body = client.get("/v2/dashboard/briefing").text

    assert "Job booked" in body


def test_no_upgrade_or_pricing_language_appears(test_engine, monkeypatch):
    client, _ = _client_for(test_engine, monkeypatch)

    body = client.get("/v2/dashboard/briefing").text

    for word in ("upgrade", "plan", "$", "price", "billing"):
        assert word.lower() not in body.lower(), f"{word!r} makes this feel like a sales page"
