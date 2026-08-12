"""Milestone A: the owner can act on a booking from their phone.

The bug this file exists for: `_process_inbound_sms` resolved the business
from `To` and then treated `From` as a customer, unconditionally. An owner
replying to any Roster alert fell through the whole routing cascade into
Frontdesk, which tried to book THEM a job. Nothing in the codebase knew the
owner's own number was special.
"""

import app as app_module
import db as db_module
import portal as portal_module
import pytest
from db_models import BOOKING_CONFIRMED, Business, Job
from fastapi.testclient import TestClient
from sqlmodel import Session, select

OWNER = "+15125550149"
CUSTOMER = "+15125550001"
BUSINESS_LINE = "+15125557777"


class Spy:
    def __init__(self):
        self.sent = []

    def send(self, from_number, to_number, body):
        self.sent.append({"to": to_number, "body": body})


class ExplodingAgent:
    """Frontdesk must never run for an owner's text. If it does, this raises
    rather than quietly booking the owner a job."""

    def respond(self, *a, **k):
        raise AssertionError("Frontdesk ran for an owner's SMS — routing bug")


@pytest.fixture
def sms(test_engine, monkeypatch):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    spy = Spy()
    monkeypatch.setattr(app_module, "sms_channel", spy)
    import booking_manager
    import notifications

    monkeypatch.setattr(notifications, "_owner_channel", spy)
    monkeypatch.setattr(booking_manager, "sms_channel", spy)
    return TestClient(app_module.app), spy


def _seed(test_engine, **job_overrides):
    with Session(test_engine) as session:
        business = Business(
            business_name="Ridgeline HVAC",
            trade="hvac",
            services_json="[]",
            hours="9-5",
            escalation_phone=OWNER,
            inbound_number=BUSINESS_LINE,
            trial_cap_cents=100000,
            email="owner-replies@test.io",
        )
        session.add(business)
        session.commit()
        session.refresh(business)
        fields = {
            "business_id": business.id,
            "customer_phone": CUSTOMER,
            "customer_name": "Dana Cruz",
            "service_type": "AC not cooling",
            "urgency": "same_day",
            "callback_number": CUSTOMER,
            "preferred_window": "Thursday morning",
        }
        fields.update(job_overrides)
        job = Job(**fields)
        session.add(job)
        session.commit()
        session.refresh(job)
        return business.id, job.id


def _post(client, body, from_number=OWNER, sid="SMowner1"):
    return client.post(
        "/webhook/sms",
        data={"From": from_number, "To": BUSINESS_LINE, "Body": body, "MessageSid": sid},
    )


# ---- the routing bug -------------------------------------------------------


def test_an_owners_sms_never_reaches_frontdesk(test_engine, sms, monkeypatch):
    """The whole bug in one assertion. ExplodingAgent raises if Frontdesk runs."""
    import service as service_module

    monkeypatch.setattr(service_module, "agent", ExplodingAgent())
    _business_id, job_id = _seed(test_engine)
    client, _spy = sms

    response = _post(client, f"Y{job_id}")

    assert response.status_code == 200


def test_an_owners_sms_never_books_the_owner_a_job(test_engine, sms, monkeypatch):
    """The visible symptom: Roster created a Job row for its own customer."""
    import service as service_module

    monkeypatch.setattr(service_module, "agent", ExplodingAgent())
    business_id, job_id = _seed(test_engine)
    client, _spy = sms

    _post(client, f"Y{job_id}")

    with Session(test_engine) as session:
        owner_jobs = session.exec(
            select(Job).where(Job.business_id == business_id, Job.customer_phone == OWNER)
        ).all()
    assert owner_jobs == []


def test_a_customer_with_the_same_number_at_another_business_is_still_a_customer(
    test_engine, sms, monkeypatch
):
    """Owner recognition is per-business. The same handset is an owner at one
    shop and an ordinary customer at another, and must not be silently
    promoted across the tenant boundary."""
    replies = []

    class Recording:
        def respond(self, *a, **k):
            replies.append(1)
            return {
                "reply": "Happy to help!",
                "jobs": [],
                "new_messages": [],
                "pending_tool_call": None,
            }

    import service as service_module

    monkeypatch.setattr(service_module, "agent", Recording())
    _seed(test_engine)
    other_line = "+15125556666"
    with Session(test_engine) as session:
        other = Business(
            business_name="Other Plumbing",
            trade="plumbing",
            services_json="[]",
            hours="9-5",
            escalation_phone="+15125559999",
            inbound_number=other_line,
            trial_cap_cents=100000,
            email="other@test.io",
        )
        session.add(other)
        session.commit()
    client, _spy = sms

    client.post(
        "/webhook/sms",
        data={"From": OWNER, "To": other_line, "Body": "my sink leaks", "MessageSid": "SMx1"},
    )

    assert replies == [1]  # Frontdesk DID run — correct at the other business


# ---- confirm ---------------------------------------------------------------


def test_owner_replying_Y_with_a_job_id_confirms_that_booking(test_engine, sms):
    _business_id, job_id = _seed(test_engine)
    client, _spy = sms

    _post(client, f"Y{job_id}")

    with Session(test_engine) as session:
        assert session.get(Job, job_id).booking_status == BOOKING_CONFIRMED


def test_confirming_texts_the_customer(test_engine, sms):
    _business_id, job_id = _seed(test_engine)
    client, spy = sms

    _post(client, f"Y{job_id}")

    to_customer = [m for m in spy.sent if m["to"] == CUSTOMER]
    assert len(to_customer) == 1
    assert "Thursday morning" in to_customer[0]["body"]


def test_the_owner_is_told_the_customer_was_notified(test_engine, sms):
    """Requirement 4: the owner gets confirmation that the loop actually
    closed, not just that the state changed."""
    _business_id, job_id = _seed(test_engine)
    client, spy = sms

    response = _post(client, f"Y{job_id}")

    assert "Dana Cruz" in response.text
    assert "notified" in response.text.lower() or "texted" in response.text.lower()


def test_confirming_twice_texts_the_customer_once(test_engine, sms):
    """Idempotency, from the only angle the customer can perceive it."""
    _business_id, job_id = _seed(test_engine)
    client, spy = sms

    _post(client, f"Y{job_id}", sid="SMa")
    _post(client, f"Y{job_id}", sid="SMb")

    assert len([m for m in spy.sent if m["to"] == CUSTOMER]) == 1


# ---- reject ----------------------------------------------------------------


def test_owner_replying_N_cancels_the_booking(test_engine, sms):
    from db_models import BOOKING_CANCELLED

    _business_id, job_id = _seed(test_engine)
    client, _spy = sms

    _post(client, f"N{job_id}")

    with Session(test_engine) as session:
        assert session.get(Job, job_id).booking_status == BOOKING_CANCELLED


def test_rejecting_tells_the_customer_without_claiming_anything(test_engine, sms):
    from booking_language import assert_no_confirmation_claim

    _business_id, job_id = _seed(test_engine)
    client, spy = sms

    _post(client, f"N{job_id}")

    to_customer = [m for m in spy.sent if m["to"] == CUSTOMER]
    assert len(to_customer) == 1
    assert_no_confirmation_claim(to_customer[0]["body"])


# ---- suggest another window ------------------------------------------------


def test_owner_texting_a_window_proposes_it_to_the_customer(test_engine, sms):
    from db_models import BOOKING_PROPOSED

    _business_id, job_id = _seed(test_engine)
    client, spy = sms

    _post(client, f"{job_id} Friday 8am-12pm")

    with Session(test_engine) as session:
        job = session.get(Job, job_id)
        assert job.booking_status == BOOKING_PROPOSED
        assert job.preferred_window == "Friday 8am-12pm"
    to_customer = [m for m in spy.sent if m["to"] == CUSTOMER]
    assert "Friday 8am-12pm" in to_customer[0]["body"]


def test_a_proposed_window_is_never_described_as_confirmed(test_engine, sms):
    from booking_language import assert_no_confirmation_claim

    _business_id, job_id = _seed(test_engine)
    client, spy = sms

    _post(client, f"{job_id} Friday 8am-12pm")

    to_customer = [m for m in spy.sent if m["to"] == CUSTOMER]
    assert_no_confirmation_claim(to_customer[0]["body"])


# ---- bare Y / N ------------------------------------------------------------


def test_a_bare_Y_confirms_when_exactly_one_booking_is_pending(test_engine, sms):
    _business_id, job_id = _seed(test_engine)
    client, _spy = sms

    _post(client, "Y")

    with Session(test_engine) as session:
        assert session.get(Job, job_id).booking_status == BOOKING_CONFIRMED


def test_a_bare_Y_with_two_pending_bookings_asks_which_and_confirms_neither(test_engine, sms):
    business_id, first_id = _seed(test_engine)
    with Session(test_engine) as session:
        second = Job(
            business_id=business_id,
            customer_phone="+15125550002",
            service_type="Furnace",
            urgency="routine",
            callback_number="+15125550002",
            preferred_window="Monday",
        )
        session.add(second)
        session.commit()
        session.refresh(second)
        second_id = second.id
    client, spy = sms

    response = _post(client, "Y")

    with Session(test_engine) as session:
        assert session.get(Job, first_id).booking_status != BOOKING_CONFIRMED
        assert session.get(Job, second_id).booking_status != BOOKING_CONFIRMED
    assert str(first_id) in response.text and str(second_id) in response.text
    assert [m for m in spy.sent if m["to"] == CUSTOMER] == []


# ---- tenant isolation ------------------------------------------------------


def test_an_owner_cannot_confirm_another_businesss_job(test_engine, sms):
    _business_id, job_id = _seed(test_engine)
    with Session(test_engine) as session:
        other = Business(
            business_name="Other Plumbing",
            trade="plumbing",
            services_json="[]",
            hours="9-5",
            escalation_phone="+15125559999",
            inbound_number="+15125556666",
            email="other-iso@test.io",
        )
        session.add(other)
        session.commit()
        session.refresh(other)
        foreign = Job(
            business_id=other.id,
            customer_phone="+15125550003",
            service_type="Water heater",
            urgency="routine",
            callback_number="+15125550003",
        )
        session.add(foreign)
        session.commit()
        session.refresh(foreign)
        foreign_id = foreign.id
    client, _spy = sms

    _post(client, f"Y{foreign_id}")

    with Session(test_engine) as session:
        assert session.get(Job, foreign_id).booking_status != BOOKING_CONFIRMED
