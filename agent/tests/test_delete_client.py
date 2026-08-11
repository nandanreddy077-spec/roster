"""Founder-admin permanent delete of a business, requested live 2026-07-21
after a debugging session left a dead-end test client (an xAI-registered
number whose signing secret was unrecoverable) with no way to remove it."""

import app as app_module
from conftest import DASH_AUTH
from db_models import (
    Business,
    Customer,
    DepartmentInterest,
    DispatchPlan,
    Employee,
    Event,
    Job,
    JobQualification,
    MembershipOffer,
    Message,
    OwnerNotification,
    RecoveryCampaign,
    RecoveryJob,
    RecoveryMessageLog,
    ReferralLead,
    ReviewReply,
)
from sqlmodel import Session, select
from starlette.testclient import TestClient


def _fully_populated_client(test_engine) -> int:
    """A business with at least one row in every child table the delete
    route touches, so the cascade can actually be proven, not just assumed."""
    with Session(test_engine) as s:
        b = Business(business_name="Doomed Plumbing", trade="plumbing")
        s.add(b)
        s.commit()
        s.refresh(b)
        bid = b.id

        c = Customer(business_id=bid, phone="+15550001111", name="Test Customer")
        s.add(c)
        s.commit()
        s.refresh(c)

        s.add(Message(business_id=bid, customer_id=c.id, role="user", content_json='"hi"'))

        j = Job(business_id=bid, customer_id=c.id, service_type="drain cleaning", urgency="routine")
        s.add(j)
        s.commit()
        s.refresh(j)

        s.add(Employee(business_id=bid, role_key="frontdesk"))
        s.add(Event(business_id=bid, type="job_booked"))
        # Added 2026-08-11: these four reference Job as well as Business, and
        # were missing from the cascade entirely. SQLite's silence (no
        # PRAGMA foreign_keys=ON) let it through undetected; migrating to
        # Postgres raised ForeignKeyViolation on this exact fixture shape and
        # the business survived un-deleted. Fixed in the route; this is what
        # would have failed first.
        s.add(
            DispatchPlan(
                business_id=bid,
                source_job_id=j.id,
                dispatch_priority="normal",
                scheduling_window="tomorrow",
                requires_dispatch_review=False,
                dispatch_reason="X",
            )
        )
        s.add(
            JobQualification(
                business_id=bid,
                source_job_id=j.id,
                job_type="repair",
                financing_candidate=False,
                membership_candidate=False,
                priority="normal",
                possible_spam=False,
                reasoning="X",
            )
        )
        s.add(MembershipOffer(business_id=bid, source_job_id=j.id, customer_phone="+15550001111"))
        s.add(
            ReviewReply(
                business_id=bid,
                source_job_id=j.id,
                customer_phone="+15550001111",
                outcome="left_review",
                raw_reply_text="great job",
            )
        )
        # Added Phase 4a (audit F7): the cascade predated both of these tables,
        # and SQLite enforces no foreign keys here (db.py sets no PRAGMA), so
        # the orphans they left behind were silent.
        s.add(DepartmentInterest(business_id=bid, department_key="finance"))
        s.add(
            OwnerNotification(
                business_id=bid,
                kind="job_booked",
                source="sms_booking",
                message="m",
                delivered=True,
            )
        )
        s.add(
            ReferralLead(
                business_id=bid,
                source_job_id=j.id,
                asker_phone="+15550001111",
                raw_reply_text="yes, my friend Bob",
            )
        )

        camp = RecoveryCampaign(
            business_id=bid, face="quote", name="June quotes", customer_list_json="[]"
        )
        s.add(camp)
        s.commit()
        s.refresh(camp)

        rj = RecoveryJob(
            campaign_id=camp.id,
            business_id=bid,
            customer_phone="+15550001111",
            service_type="AC install",
        )
        s.add(rj)
        s.commit()
        s.refresh(rj)

        s.add(
            RecoveryMessageLog(
                recovery_job_id=rj.id, message_day=1, message_text="Still interested?"
            )
        )
        s.commit()
        return bid


def test_delete_client_removes_business_and_every_related_row(test_engine, monkeypatch):
    monkeypatch.setattr(app_module, "engine", test_engine)
    bid = _fully_populated_client(test_engine)
    client = TestClient(app_module.app, headers=DASH_AUTH)

    r = client.post(
        f"/clients/{bid}/delete", data={"confirm_name": "Doomed Plumbing"}, follow_redirects=False
    )

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
        assert (
            s.exec(select(RecoveryCampaign).where(RecoveryCampaign.business_id == bid)).all() == []
        )
        assert s.exec(select(RecoveryJob).where(RecoveryJob.business_id == bid)).all() == []
        assert s.exec(select(RecoveryMessageLog)).all() == []  # only row was under this business
        assert (
            s.exec(select(DepartmentInterest).where(DepartmentInterest.business_id == bid)).all()
            == []
        )
        assert (
            s.exec(select(OwnerNotification).where(OwnerNotification.business_id == bid)).all()
            == []
        )
        assert s.exec(select(DispatchPlan).where(DispatchPlan.business_id == bid)).all() == []
        assert (
            s.exec(select(JobQualification).where(JobQualification.business_id == bid)).all() == []
        )
        assert s.exec(select(MembershipOffer).where(MembershipOffer.business_id == bid)).all() == []
        assert s.exec(select(ReviewReply).where(ReviewReply.business_id == bid)).all() == []


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
        b = Business(business_name="Doomed Plumbing")
        s.add(b)
        s.commit()
        s.refresh(b)
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
        b = Business(business_name="Doomed Plumbing")
        s.add(b)
        s.commit()
        s.refresh(b)
        bid = b.id
    client = TestClient(app_module.app)

    r = client.post(f"/clients/{bid}/delete", data={"confirm_name": "Doomed Plumbing"})

    assert r.status_code == 401
    with Session(test_engine) as s:
        assert s.get(Business, bid) is not None
