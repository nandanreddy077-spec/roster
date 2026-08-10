"""Membership Agent, end to end: the whole journey through the REAL entry
points, not through the service functions directly.

Two things can only be proven here and nowhere else:

  1. The production scheduler (recovery_tick.run) actually drives this
     employee, and respects the quiet-hours gate while doing it.
  2. An inbound text hitting /webhook/sms is routed to the membership
     classifier rather than to Frontdesk, Reviews or Referral — the routing
     chain in app.py is ordered by recency of the last outbound touch, and
     nothing else in the codebase tests that ordering.

The promise, stated as a number: a customer receives at most
    1 plan offer  +  1 nudge (only if they never replied)
and nothing at all after they answer.
"""
import importlib
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

import app as app_module
import db as db_module
import membership_engine
import membership_service
import portal as portal_module
import recovery_tick
import service as service_module
from conftest import StubAgent
from db_models import Business, Customer, Job, JobQualification, MembershipOffer
from deployment import deploy_role

CUSTOMER = "+15125550001"
BUSINESS_LINE = "+15125557777"
PLAN = "Comfort Club — $19/month, two tune-ups a year."


class Spy:
    def __init__(self):
        self.sent = []

    def send(self, from_number, to_number, body):
        self.sent.append({"to": to_number, "body": body})


@pytest.fixture
def membership(monkeypatch):
    """Delays collapsed to zero so a journey spanning a fortnight in
    production runs in one test — read at import time, exactly as production
    reads them at boot."""
    monkeypatch.setenv("MEMBERSHIP_OFFER_DELAY_DAYS", "0")
    monkeypatch.setenv("MEMBERSHIP_FOLLOWUP_DELAY_DAYS", "0")
    importlib.reload(membership_engine)
    importlib.reload(membership_service)
    spy = Spy()
    membership_service.sms_channel = spy
    membership_service.sent_log = spy.sent
    yield membership_service
    monkeypatch.undo()
    importlib.reload(membership_engine)
    importlib.reload(membership_service)


def _seed(session):
    business = Business(
        business_name="Ridgeline HVAC", trade="hvac", services_json="[]", hours="9-5",
        escalation_phone="+15125550149", inbound_number=BUSINESS_LINE,
        membership_plan=PLAN, trial_cap_cents=100000, email="lifecycle@test.io",
    )
    session.add(business)
    session.commit()
    session.refresh(business)
    deploy_role(session, business.id, "membership_agent")

    job = Job(
        business_id=business.id, customer_phone=CUSTOMER, customer_name="Dana Cruz",
        service_type="AC repair", urgency="routine", callback_number=CUSTOMER,
        completed_at=datetime.utcnow() - timedelta(days=1),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    session.add(JobQualification(
        business_id=business.id, source_job_id=job.id, job_type="repair",
        financing_candidate=False, membership_candidate=True, priority="normal",
        possible_spam=False, reasoning="test",
    ))
    session.commit()
    return business, job


# ---- the scheduler ---------------------------------------------------------

def test_the_production_scheduler_drives_the_membership_offer(test_engine, membership, monkeypatch):
    """recovery_tick.run is what cron actually calls. If this employee isn't
    wired into it, every unit test above is testing a function nobody runs."""
    monkeypatch.setattr(recovery_tick, "engine", test_engine)
    monkeypatch.setattr(recovery_tick, "init_db", lambda: None)
    monkeypatch.setattr(recovery_tick, "send_hours_ok", lambda: True)
    with Session(test_engine) as session:
        _seed(session)

    recovery_tick.run()

    assert len(membership.sent_log) >= 1
    assert any(PLAN in m["body"] for m in membership.sent_log)


def test_the_scheduler_sends_nothing_outside_quiet_hours(test_engine, membership, monkeypatch):
    """A maintenance-plan pitch at 3am is the fastest way to make an owner
    fire their AI employee."""
    monkeypatch.setattr(recovery_tick, "engine", test_engine)
    monkeypatch.setattr(recovery_tick, "init_db", lambda: None)
    monkeypatch.setattr(recovery_tick, "send_hours_ok", lambda: False)
    with Session(test_engine) as session:
        _seed(session)

    recovery_tick.run()

    assert membership.sent_log == []
    # And nothing was claimed either — a suppressed send must be retryable,
    # not silently consumed.
    with Session(test_engine) as session:
        assert session.exec(select(MembershipOffer)).all() == []


def test_repeated_scheduler_runs_never_double_text(test_engine, membership, monkeypatch):
    """The production failure mode: cron fires twice, or a founder runs the
    tick by hand while cron is running."""
    monkeypatch.setattr(recovery_tick, "engine", test_engine)
    monkeypatch.setattr(recovery_tick, "init_db", lambda: None)
    monkeypatch.setattr(recovery_tick, "send_hours_ok", lambda: True)
    with Session(test_engine) as session:
        _seed(session)

    for _ in range(5):
        recovery_tick.run()

    offers = [m for m in membership.sent_log if PLAN in m["body"]]
    nudges = [m for m in membership.sent_log if "circling back" in m["body"]]
    assert len(offers) == 1, membership.sent_log
    assert len(nudges) == 1, membership.sent_log
    assert len(membership.sent_log) == 2


# ---- the webhook -----------------------------------------------------------

def _sms_client(test_engine, monkeypatch, intent):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    membership_service.agent = StubAgent({
        "reply": None,
        "pending_tool_call": {"name": "record_membership_reply",
                              "input": {"intent": intent}},
    })

    class FrontdeskShouldNotRun:
        def respond(self, *a, **k):
            raise AssertionError(
                "the reply reached Frontdesk instead of the membership classifier")

    monkeypatch.setattr(service_module, "agent", FrontdeskShouldNotRun())
    return TestClient(app_module.app)


def test_a_reply_to_the_offer_reaches_the_membership_classifier(test_engine, membership, monkeypatch):
    """The routing assertion. Frontdesk explodes if it is reached, so this
    fails loudly rather than silently degrading to a generic chat reply."""
    with Session(test_engine) as session:
        _seed(session)
        membership_service.send_due_membership_offers(session)

    client = _sms_client(test_engine, monkeypatch, "accepted")
    import notifications
    notifications._owner_channel = Spy()

    r = client.post("/webhook/sms", data={
        "From": CUSTOMER, "To": BUSINESS_LINE, "Body": "yes sign me up",
        "MessageSid": "SMmember1",
    })

    assert r.status_code == 200
    assert "passed it to the office" in r.text
    with Session(test_engine) as session:
        offer = session.exec(select(MembershipOffer)).one()
        assert offer.outcome == "accepted"
        customer = session.exec(
            select(Customer).where(Customer.phone == CUSTOMER)).one()
        assert customer.plan_notes


def test_after_answering_the_customer_talks_to_frontdesk_again(test_engine, membership, monkeypatch):
    """A settled offer must stop hijacking the conversation — otherwise a
    customer who declined a plan can never book another job by text."""
    with Session(test_engine) as session:
        _seed(session)
        membership_service.send_due_membership_offers(session)

    client = _sms_client(test_engine, monkeypatch, "declined")
    client.post("/webhook/sms", data={
        "From": CUSTOMER, "To": BUSINESS_LINE, "Body": "no thanks",
        "MessageSid": "SMmember2",
    })

    # Second text: Frontdesk must handle it, so we give it a real stub now.
    monkeypatch.setattr(service_module, "agent", StubAgent({
        "reply": "Sure — what's going on with the unit?",
        "jobs": [], "new_messages": [], "pending_tool_call": None,
    }))
    r = client.post("/webhook/sms", data={
        "From": CUSTOMER, "To": BUSINESS_LINE, "Body": "actually my AC is out again",
        "MessageSid": "SMmember3",
    })

    assert "what's going on with the unit" in r.text


def test_stop_at_the_webhook_unsubscribes_and_ends_the_sequence(test_engine, membership, monkeypatch):
    """The full opt-out path, through the real endpoint: the nudge that would
    otherwise have gone out must not."""
    with Session(test_engine) as session:
        _seed(session)
        membership_service.send_due_membership_offers(session)

    client = _sms_client(test_engine, monkeypatch, "unsubscribe")
    r = client.post("/webhook/sms", data={
        "From": CUSTOMER, "To": BUSINESS_LINE, "Body": "STOP",
        "MessageSid": "SMmember4",
    })

    assert "unsubscribed" in r.text
    with Session(test_engine) as session:
        assert membership_service.send_due_membership_followups(session) == []
        offer = session.exec(select(MembershipOffer)).one()
        assert offer.outcome == "unsubscribed"
