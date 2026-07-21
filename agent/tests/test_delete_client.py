"""Founder-admin permanent delete of a business, requested live 2026-07-21
after a debugging session left a dead-end test client (an xAI-registered
number whose signing secret was unrecoverable) with no way to remove it."""
from sqlmodel import Session, select

import app as app_module
from conftest import DASH_AUTH
from db_models import (
    Business, Customer, Employee, Event, Job, Message,
    RecoveryCampaign, RecoveryJob, RecoveryMessageLog, ReferralLead,
)
from starlette.testclient import TestClient


def _fully_populated_client(test_engine) -> int:
    """A business with at least one row in every child table the delete
    route touches, so the cascade can actually be proven, not just assumed."""
    with Session(test_engine) as s:
        b = Business(business_name="Doomed Plumbing", trade="plumbing")
        s.add(b); s.commit(); s.refresh(b)
        bid = b.id

        c = Customer(business_id=bid, phone="+15550001111", name="Test Customer")
        s.add(c); s.commit(); s.refresh(c)

        s.add(Message(business_id=bid, customer_id=c.id, role="user", content_json='"hi"'))

        j = Job(business_id=bid, customer_id=c.id, service_type="drain cleaning", urgency="routine")
        s.add(j); s.commit(); s.refresh(j)

        s.add(Employee(business_id=bid, role_key="frontdesk"))
        s.add(Event(business_id=bid, type="job_booked"))
        s.add(ReferralLead(business_id=bid, source_job_id=j.id, asker_phone="+15550001111", raw_reply_text="yes, my friend Bob"))

        camp = RecoveryCampaign(business_id=bid, face="quote", name="June quotes", customer_list_json="[]")
        s.add(camp); s.commit(); s.refresh(camp)

        rj = RecoveryJob(campaign_id=camp.id, business_id=bid, customer_phone="+15550001111", service_type="AC install")
        s.add(rj); s.commit(); s.refresh(rj)

        s.add(RecoveryMessageLog(recovery_job_id=rj.id, message_day=1, message_text="Still interested?"))
        s.commit()
        return bid


def test_delete_client_removes_business_and_every_related_row(test_engine, monkeypatch):
    monkeypatch.setattr(app_module, "engine", test_engine)
    bid = _fully_populated_client(test_engine)
    client = TestClient(app_module.app, headers=DASH_AUTH)

    r = client.post(f"/clients/{bid}/delete", data={"confirm_name": "Doomed Plumbing"}, follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"] == "/clients"
    with Session(test_engine) as s:
        assert s.get(Business, bid) is None
        assert s.exec(select(Customer).where(Customer.business_id == bid)).all() == []
        assert s.exec(select(Message).where(Message.business_id == bid)).all() == []
        assert s.exec(select(Job).where(Job.business_id == bid)).all() == []
        assert s.exec(select(Employee).where(Employee.business_id == bid)).all() == []
        assert s.exec(select(Event).where(Event.business_id == bid)).all() == []
        assert s.exec(select(ReferralLead).where(ReferralLead.business_id == bid)).all() == []
        assert s.exec(select(RecoveryCampaign).where(RecoveryCampaign.business_id == bid)).all() == []
        assert s.exec(select(RecoveryJob).where(RecoveryJob.business_id == bid)).all() == []
        assert s.exec(select(RecoveryMessageLog)).all() == []  # only row was under this business


def test_client_detail_404s_after_delete_instead_of_crashing(test_engine, monkeypatch):
    """Caught live: visiting a just-deleted business's page (stale tab, old
    bookmark, double-click on Delete) 500'd -- client_detail() passed a None
    client straight into the template, which crashed reading
    client.trial_spend_cents. Deleting a client makes this a real, easy path
    to hit, not just a typo'd URL."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    bid = _fully_populated_client(test_engine)
    client = TestClient(app_module.app, headers=DASH_AUTH)
    client.post(f"/clients/{bid}/delete", data={"confirm_name": "Doomed Plumbing"})

    r = client.get(f"/clients/{bid}", headers=DASH_AUTH)

    assert r.status_code == 404


def test_delete_client_rejects_wrong_confirmation_name(test_engine, monkeypatch):
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        b = Business(business_name="Doomed Plumbing"); s.add(b); s.commit(); s.refresh(b)
        bid = b.id
    client = TestClient(app_module.app, headers=DASH_AUTH)

    r = client.post(f"/clients/{bid}/delete", data={"confirm_name": "wrong name"})

    assert r.status_code == 400
    with Session(test_engine) as s:
        assert s.get(Business, bid) is not None


def test_delete_client_404_for_missing_client(test_engine, monkeypatch):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client = TestClient(app_module.app, headers=DASH_AUTH)

    r = client.post("/clients/999999/delete", data={"confirm_name": "anything"})

    assert r.status_code == 404


def test_delete_client_requires_admin_auth(test_engine, monkeypatch):
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        b = Business(business_name="Doomed Plumbing"); s.add(b); s.commit(); s.refresh(b)
        bid = b.id
    client = TestClient(app_module.app)

    r = client.post(f"/clients/{bid}/delete", data={"confirm_name": "Doomed Plumbing"})

    assert r.status_code == 401
    with Session(test_engine) as s:
        assert s.get(Business, bid) is not None
