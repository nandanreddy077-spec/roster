"""Setting a client up used to mean remembering which of five scattered panels
still had work in it. These tests pin the checklist that replaced that, and the
one rule it exists to enforce: nothing is green unless a real column says so."""
import base64

from fastapi.testclient import TestClient
from sqlmodel import Session, select

import app as app_module
import db as db_module
import portal as portal_module
from app import _setup_checklist
from db_models import Business, Job


def _basic() -> dict:
    return {"Authorization": "Basic " + base64.b64encode(b"admin:hunter2").decode()}


def _wire(monkeypatch, test_engine):
    monkeypatch.setenv("ADMIN_PASSWORD", "hunter2")
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)


def _step(checklist, label):
    return next(s for s in checklist if s["label"] == label)


# ---- The checklist ----------------------------------------------------------


def test_a_brand_new_business_is_honest_about_having_nothing_done():
    business = Business(business_name="Ridgeline", trade="Plumbing", hours="Mon-Sat 7-7")
    checklist = _setup_checklist(business, [], real_job_count=0)
    assert _step(checklist, "Business details captured")["done"] is True
    assert [s["label"] for s in checklist if not s["done"]] == [
        "AI phone number provisioned",
        "Live voice registered with xAI",
        "A department staffed",
        "Owner can reach their dashboard",
        "Answered a real customer",
    ]


def test_an_sms_only_number_does_not_claim_voice_is_wired():
    """The failure mode this catches: a number bought, xAI registration
    silently failed, and the founder tells a customer voice is live."""
    business = Business(inbound_number="+15125550123", xai_phone_number=None)
    checklist = _setup_checklist(business, [], real_job_count=0)
    assert _step(checklist, "AI phone number provisioned")["done"] is True
    assert _step(checklist, "Live voice registered with xAI")["done"] is False


def test_test_bookings_never_satisfy_answered_a_real_customer():
    """real_job_count comes from metrics.booked_jobs, which already excludes
    test threads — this pins that the checklist uses that number and not a
    raw Job count."""
    business = Business()
    assert _setup_checklist(business, [], real_job_count=0)[-1]["done"] is False
    assert _setup_checklist(business, [], real_job_count=1)[-1]["done"] is True


def test_a_partially_staffed_department_counts_as_staffed():
    class _Status:
        state = "partial"

    business = Business()
    checklist = _setup_checklist(business, [{"status": _Status()}], real_job_count=0)
    assert _step(checklist, "A department staffed")["done"] is True


def test_the_checklist_renders_on_the_client_page(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    with Session(test_engine) as session:
        session.add(Business(business_name="Ridgeline", trade="Plumbing"))
        session.commit()

    client = TestClient(app_module.app)
    body = client.get("/clients/1", headers=_basic()).text
    assert "AI phone number provisioned" in body
    assert "Answered a real customer" in body


# ---- Owner email + access link ----------------------------------------------


def test_creating_a_client_stores_the_owner_email(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    client = TestClient(app_module.app)
    client.post(
        "/clients/new",
        headers=_basic(),
        data={
            "business_name": "Ridgeline", "trade": "Plumbing", "services": "Drains",
            "hours": "Mon-Sat", "pricing_faq": "$99", "escalation_phone": "5125550100",
            "owner_email": "Owner@Ridgeline.com",
        },
        follow_redirects=False,
    )
    with Session(test_engine) as session:
        assert session.exec(select(Business)).first().email == "owner@ridgeline.com"


def test_a_duplicate_owner_email_keeps_the_business_and_reports_it(monkeypatch, test_engine):
    """A whole discovery call's notes must not be lost to a typo'd email."""
    _wire(monkeypatch, test_engine)
    with Session(test_engine) as session:
        session.add(Business(email="taken@example.com"))
        session.commit()

    client = TestClient(app_module.app)
    response = client.post(
        "/clients/new",
        headers=_basic(),
        data={
            "business_name": "Ridgeline", "trade": "Plumbing", "services": "Drains",
            "hours": "Mon-Sat", "pricing_faq": "$99", "escalation_phone": "5125550100",
            "owner_email": "taken@example.com",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "access_error" in response.headers["location"]
    with Session(test_engine) as session:
        created = session.exec(select(Business).where(Business.business_name == "Ridgeline")).first()
        assert created is not None
        assert created.email is None


def test_the_founder_can_set_the_owner_email_later(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    with Session(test_engine) as session:
        session.add(Business(business_name="Ridgeline"))
        session.commit()

    client = TestClient(app_module.app)
    client.post(
        "/clients/1/owner-email", headers=_basic(),
        data={"owner_email": "owner@ridgeline.com"}, follow_redirects=False,
    )
    with Session(test_engine) as session:
        assert session.get(Business, 1).email == "owner@ridgeline.com"


def test_setting_an_email_already_on_another_business_is_refused(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    with Session(test_engine) as session:
        session.add(Business(business_name="Other", email="taken@example.com"))
        session.add(Business(business_name="Ridgeline"))
        session.commit()

    client = TestClient(app_module.app)
    response = client.post(
        "/clients/2/owner-email", headers=_basic(),
        data={"owner_email": "taken@example.com"}, follow_redirects=False,
    )
    assert "access_error" in response.headers["location"]
    with Session(test_engine) as session:
        assert session.get(Business, 2).email is None


def test_the_client_page_offers_a_working_access_link(monkeypatch, test_engine):
    """End to end, the thing the founder actually does: open the client page,
    copy the link, and have the owner land on their dashboard."""
    _wire(monkeypatch, test_engine)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://rosterhires.com")
    with Session(test_engine) as session:
        session.add(Business(business_name="Ridgeline"))
        session.commit()

    founder = TestClient(app_module.app)
    body = founder.get("/clients/1", headers=_basic()).text
    link = body.split('class="access-link-field" value="')[1].split('"')[0]
    assert link.startswith("https://rosterhires.com/access/")

    owner = TestClient(app_module.app)
    response = owner.get(link.replace("https://rosterhires.com", ""), follow_redirects=False)
    assert response.headers["location"] == portal_module.DASHBOARD_HOME
