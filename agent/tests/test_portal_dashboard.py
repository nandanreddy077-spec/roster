import json
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session, select

import app as app_module
import db as db_module
import portal as portal_module
import service as service_module
from conftest import StubAgent
from conftest import login_as, provisioned_business
from db_models import Business, Job


def _stub_reply(text: str):
    """A canned agent result shaped like AgentEngine.respond()'s output."""
    return StubAgent(
        {
            "reply": text,
            "new_messages": [{"role": "assistant", "content": [{"type": "text", "text": text}]}],
            "jobs": [],
            "pending_tool_call": None,
        }
    )


def _fully_onboarded_client(client: TestClient, monkeypatch, test_engine):
    """A live shop, built the way one actually comes into being now: the
    founder provisions it and hands over an access link. The self-serve
    signup + wizard this used to walk is retired (2026-08-06)."""
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    business_id = provisioned_business(test_engine, frontdesk_live=True)
    login_as(client, business_id)
    return business_id


def test_dashboard_requires_login(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_dashboard_untested_shows_ready_not_live(monkeypatch, test_engine):
    """A freshly onboarded client has NOT tested — the dashboard must say
    'Ready', never 'Working'/live, no matter what they typed in onboarding."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    _fully_onboarded_client(client, monkeypatch, test_engine)

    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "Ready" in response.text
    assert "before you trust it" in response.text
    assert "Working" not in response.text
    assert "Quote Chaser" in response.text  # ready-to-hire card


def test_dashboard_activity_lists_real_jobs(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    _fully_onboarded_client(client, monkeypatch, test_engine)

    with Session(test_engine) as session:
        owner = session.exec(select(Business).where(Business.email == "owner@example.com")).first()
        session.add(Job(business_id=owner.id, service_type="drain cleaning", urgency="routine"))
        session.commit()

    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "drain cleaning" in response.text


def test_dashboard_test_message_earns_working_status(monkeypatch, test_engine):
    """The honest-status hinge: a test message that gets a real reply is what
    flips 'Ready' → 'Working'. Form submission alone never does."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    monkeypatch.setattr(
        service_module, "agent", _stub_reply("Yes — we do same-day drain cleaning!")
    )
    client = TestClient(app_module.app)
    _fully_onboarded_client(client, monkeypatch, test_engine)

    with Session(test_engine) as session:
        owner = session.exec(select(Business).where(Business.email == "owner@example.com")).first()
        assert owner.tested_at is None

    client.post("/dashboard/test", data={"message": "Do you do same-day drain cleaning?"})

    with Session(test_engine) as session:
        owner = session.exec(select(Business).where(Business.email == "owner@example.com")).first()
        assert owner.tested_at is not None

    response = client.get("/dashboard")
    assert "Working" in response.text
    assert "Do you do same-day drain cleaning?" in response.text  # owner's test message
    assert "Yes — we do same-day drain cleaning!" in response.text  # the reply, shown in the panel


def test_dashboard_test_requires_login(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    response = client.post("/dashboard/test", data={"message": "hi"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_roster_hire_queues_quote_chaser(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    _fully_onboarded_client(client, monkeypatch, test_engine)

    response = client.post("/roster/hire", data={"role": "Quote Chaser"}, follow_redirects=False)
    assert response.status_code == 303

    with Session(test_engine) as session:
        owner = session.exec(select(Business).where(Business.email == "owner@example.com")).first()
        assert json.loads(owner.requested_roster) == ["Quote Chaser"]

    response = client.get("/dashboard")
    assert "Retention Manager" in response.text  # now the next hire-next card


def test_roster_hire_rejects_out_of_order_role(monkeypatch, test_engine):
    """Can't hire Retention Manager before Quote Chaser — enforces sequencing."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    _fully_onboarded_client(client, monkeypatch, test_engine)

    client.post("/roster/hire", data={"role": "Retention Manager"})

    with Session(test_engine) as session:
        owner = session.exec(select(Business).where(Business.email == "owner@example.com")).first()
        assert owner.requested_roster is None


def test_retention_manager_hire_form_blocked_before_quote_chaser(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    _fully_onboarded_client(client, monkeypatch, test_engine)

    response = client.get("/roster/hire/retention-manager", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == portal_module.DASHBOARD_HOME


def test_retention_manager_hire_saves_review_link_and_referral_incentive(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    _fully_onboarded_client(client, monkeypatch, test_engine)
    client.post("/roster/hire", data={"role": "Quote Chaser"})

    response = client.get("/roster/hire/retention-manager")
    assert response.status_code == 200
    assert "review link" in response.text.lower()

    response = client.post(
        "/roster/hire/retention-manager",
        data={"review_link": "https://g.page/r/test", "referral_incentive": "$25 off"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == portal_module.DASHBOARD_HOME

    with Session(test_engine) as session:
        owner = session.exec(select(Business).where(Business.email == "owner@example.com")).first()
        assert json.loads(owner.requested_roster) == ["Quote Chaser", "Retention Manager"]
        assert owner.review_link == "https://g.page/r/test"
        assert owner.referral_incentive == "$25 off"


def test_retention_manager_hire_skippable(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    _fully_onboarded_client(client, monkeypatch, test_engine)
    client.post("/roster/hire", data={"role": "Quote Chaser"})

    client.post("/roster/hire/retention-manager", data={})

    with Session(test_engine) as session:
        owner = session.exec(select(Business).where(Business.email == "owner@example.com")).first()
        assert json.loads(owner.requested_roster) == ["Quote Chaser", "Retention Manager"]
        assert owner.review_link is None
        assert owner.referral_incentive is None


def test_dashboard_review_link_and_referral_incentive_editable_anytime(monkeypatch, test_engine):
    """Not gated on Retention Manager being hired — matches how the sending
    code actually checks these fields (independent of requested_roster)."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    _fully_onboarded_client(client, monkeypatch, test_engine)

    client.post("/dashboard/review-link", data={"review_link": "https://g.page/r/test"})
    client.post("/dashboard/referral-incentive", data={"referral_incentive": "$25 off"})

    with Session(test_engine) as session:
        owner = session.exec(select(Business).where(Business.email == "owner@example.com")).first()
        assert owner.review_link == "https://g.page/r/test"
        assert owner.referral_incentive == "$25 off"

    response = client.get("/dashboard")
    assert "https://g.page/r/test" in response.text
    assert "$25 off" in response.text


def test_source_banner_hidden_before_three_days(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    _fully_onboarded_client(client, monkeypatch, test_engine)

    response = client.get("/dashboard")
    assert "Where" not in response.text or "hear about Roster" not in response.text


def test_source_banner_shown_after_three_days(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    _fully_onboarded_client(client, monkeypatch, test_engine)

    with Session(test_engine) as session:
        owner = session.exec(select(Business).where(Business.email == "owner@example.com")).first()
        owner.activated_at = datetime.utcnow() - timedelta(days=4)
        session.add(owner)
        session.commit()

    response = client.get("/dashboard")
    assert "hear about Roster" in response.text


def test_dismissing_source_banner_hides_it_going_forward(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    _fully_onboarded_client(client, monkeypatch, test_engine)

    with Session(test_engine) as session:
        owner = session.exec(select(Business).where(Business.email == "owner@example.com")).first()
        owner.activated_at = datetime.utcnow() - timedelta(days=4)
        session.add(owner)
        session.commit()

    client.post("/dashboard/source", data={"source": "A friend recommended us"})

    with Session(test_engine) as session:
        owner = session.exec(select(Business).where(Business.email == "owner@example.com")).first()
        assert owner.source == "A friend recommended us"
        assert owner.source_prompt_dismissed is True

    response = client.get("/dashboard")
    assert "hear about Roster" not in response.text
