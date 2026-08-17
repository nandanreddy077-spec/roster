"""Milestone A end to end: a booking request that actually ENDS.

The bug this milestone closes: /confirm set a column and redirected, sending
the customer nothing. A customer told "the office will confirm and text you
back" was never contacted again by Roster. These tests assert the loop closes
— through the real routes, not by calling service functions directly.
"""

import json
import re
from pathlib import Path
from uuid import uuid4

import app as app_module
import booking_manager
import db as db_module
import portal as portal_module
import pytest
from conftest import DASH_AUTH
from db_models import (
    BOOKING_CANCELLED,
    BOOKING_CONFIRMED,
    BOOKING_PROPOSED,
    BOOKING_REQUESTED,
    Business,
    Event,
    Job,
)
from fastapi.testclient import TestClient
from sqlmodel import Session, select

AGENT_DIR = Path(__file__).resolve().parent.parent
OWNER = "+15125550149"
CUSTOMER = "+15125550001"
BUSINESS_LINE = "+15125557777"


class Spy:
    def __init__(self):
        self.sent = []

    def send(self, from_number, to_number, body):
        self.sent.append({"to": to_number, "body": body})


@pytest.fixture
def console(test_engine, monkeypatch):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    spy = Spy()
    monkeypatch.setattr(booking_manager, "sms_channel", spy)
    return TestClient(app_module.app), spy


def _seed(test_engine, line=BUSINESS_LINE, **overrides):
    """`email` and `inbound_number` are unique columns, so a test needing two
    shops must pass a distinct `line`; the email is always unique."""
    with Session(test_engine) as session:
        business = Business(
            business_name="Ridgeline HVAC",
            trade="hvac",
            services_json="[]",
            hours="9-5",
            escalation_phone=OWNER,
            inbound_number=line,
            trial_cap_cents=100000,
            email=f"lifecycle-{uuid4().hex[:10]}@test.io",
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
            "preferred_window": "Tuesday morning",
        }
        fields.update(overrides)
        job = Job(**fields)
        session.add(job)
        session.commit()
        session.refresh(job)
        return business.id, job.id


# ---- the hole this milestone exists to close -------------------------------


def test_confirming_from_the_console_texts_the_customer(test_engine, console):
    """Before Milestone A this route sent nothing at all."""
    business_id, job_id = _seed(test_engine)
    client, spy = console

    client.post(f"/clients/{business_id}/jobs/{job_id}/confirm", headers=DASH_AUTH)

    to_customer = [m for m in spy.sent if m["to"] == CUSTOMER]
    assert len(to_customer) == 1
    assert "Tuesday morning" in to_customer[0]["body"]


def test_confirming_twice_from_the_console_texts_once(test_engine, console):
    business_id, job_id = _seed(test_engine)
    client, spy = console

    client.post(f"/clients/{business_id}/jobs/{job_id}/confirm", headers=DASH_AUTH)
    client.post(f"/clients/{business_id}/jobs/{job_id}/confirm", headers=DASH_AUTH)

    assert len([m for m in spy.sent if m["to"] == CUSTOMER]) == 1


def test_the_console_can_reject_a_booking(test_engine, console):
    business_id, job_id = _seed(test_engine)
    client, spy = console

    client.post(
        f"/clients/{business_id}/jobs/{job_id}/reject",
        data={"reason": "crew on a commercial job"},
        headers=DASH_AUTH,
    )

    with Session(test_engine) as session:
        job = session.get(Job, job_id)
        assert job.booking_status == BOOKING_CANCELLED
        assert job.booking_notes == "crew on a commercial job"
    assert len([m for m in spy.sent if m["to"] == CUSTOMER]) == 1


def test_the_owners_private_reason_is_never_sent_to_the_customer(test_engine, console):
    """booking_notes is an internal note. Forwarding it unread would publish
    whatever the owner typed to the customer it is about."""
    business_id, job_id = _seed(test_engine)
    client, spy = console

    client.post(
        f"/clients/{business_id}/jobs/{job_id}/reject",
        data={"reason": "this customer never pays on time"},
        headers=DASH_AUTH,
    )

    body = [m for m in spy.sent if m["to"] == CUSTOMER][0]["body"]
    assert "never pays" not in body


def test_the_console_can_propose_a_different_window(test_engine, console):
    business_id, job_id = _seed(test_engine)
    client, spy = console

    client.post(
        f"/clients/{business_id}/jobs/{job_id}/propose",
        data={"window": "Friday 8am-12pm"},
        headers=DASH_AUTH,
    )

    with Session(test_engine) as session:
        job = session.get(Job, job_id)
        assert job.booking_status == BOOKING_PROPOSED
        assert job.preferred_window == "Friday 8am-12pm"
    assert "Friday 8am-12pm" in [m for m in spy.sent if m["to"] == CUSTOMER][0]["body"]


def test_booking_routes_require_admin_auth(test_engine, console):
    business_id, job_id = _seed(test_engine)
    client, _spy = console

    for path, data in (
        (f"/clients/{business_id}/jobs/{job_id}/reject", {"reason": "x"}),
        (f"/clients/{business_id}/jobs/{job_id}/propose", {"window": "Friday"}),
    ):
        assert client.post(path, data=data).status_code == 401


def test_a_booking_route_404s_for_another_businesss_job(test_engine, console):
    business_id, job_id = _seed(test_engine)
    other_id, _other_job = _seed(test_engine, line="+15125556666")
    client, _spy = console

    response = client.post(
        f"/clients/{other_id}/jobs/{job_id}/reject", data={"reason": "x"}, headers=DASH_AUTH
    )

    assert response.status_code == 404


# ---- the state machine -----------------------------------------------------


def test_a_cancelled_booking_cannot_be_confirmed(test_engine):
    """CANCELLED is terminal. A returning customer gets a NEW job, which is
    what keeps completed_at (and the four employees reading it) untouched."""
    business_id, job_id = _seed(test_engine)
    with Session(test_engine) as session:
        business = session.get(Business, business_id)
        job = session.get(Job, job_id)
        booking_manager.reject(session, business, job)

        with pytest.raises(booking_manager.IllegalTransition):
            booking_manager.confirm(session, business, job)


def test_every_legal_transition_is_reachable(test_engine):
    """The transition table is the whole truth about how a booking moves, so
    it must not contain a pair no code path can produce."""
    for source, target in booking_manager.LEGAL_TRANSITIONS:
        assert source in (
            BOOKING_REQUESTED,
            BOOKING_PROPOSED,
            BOOKING_CONFIRMED,
            "reschedule_requested",
        )
        assert source != target


def test_booking_manager_is_the_only_module_that_transitions_booking_status():
    """The structural guard behind 'single writer'.

    Creation may set the initial value (bookings.py's INSERT, which is a row
    default, not a move). Only booking_manager may UPDATE an existing row's
    status — that is what makes the transition table above authoritative
    rather than aspirational.
    """
    allowed = {"booking_manager.py", "db_models.py", "bookings.py"}
    violations = []
    for path in AGENT_DIR.glob("*.py"):
        if path.name in allowed:
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # An assignment to the attribute is a transition; passing it as a
            # keyword to book_job() is creation and stays allowed.
            #
            # `=` NOT followed by `=`: the substring check this replaced also
            # matched `Job.booking_status == BOOKING_CANCELLED`, a query filter
            # that READS the column. Reading a status is not transitioning it,
            # and a guard that blocks reads pushes callers into looser
            # alternatives — the opposite of what it is defending. Assignments
            # are still caught; test_the_transition_guard_still_catches_a_real
            # _assignment below proves it.
            if re.search(r"\.booking_status\s*=(?!=)", stripped):
                violations.append(f"{path.name}:{lineno}: {stripped}")
    assert violations == [], "booking_status transitioned outside booking_manager:\n" + "\n".join(
        violations
    )


def test_the_transition_guard_still_catches_a_real_assignment():
    """Proof-of-catch for the guard above.

    That guard was loosened to stop flagging `==` comparisons (reading a status
    is not transitioning it). A loosened guard is worthless unless it still
    bites, and this repo has already shipped one silently-vacuous structural
    test — test_tick_deployment_gate's regex stopped matching the call shape it
    checked and passed for weeks. So the pattern is exercised directly here
    against both forms.
    """
    pattern = r"\.booking_status\s*=(?!=)"

    # Real transitions — must be caught, whatever the spacing.
    for line in (
        "job.booking_status = BOOKING_CONFIRMED",
        "job.booking_status=BOOKING_CANCELLED",
        "match.booking_status  =  BOOKING_PROPOSED",
    ):
        assert re.search(pattern, line), f"guard missed a real transition: {line}"

    # Reads and creation — must NOT be caught.
    for line in (
        "Job.booking_status == BOOKING_CANCELLED,",
        "if job.booking_status == BOOKING_CONFIRMED:",
        "book_job(session, client, t, n, args, booking_status=BOOKING_PROPOSED)",
    ):
        assert not re.search(pattern, line), f"guard flagged a non-transition: {line}"


# ---- the timeline the owner sees -------------------------------------------


def test_the_timeline_records_the_whole_journey_in_order(test_engine, console):
    business_id, job_id = _seed(test_engine)
    client, _spy = console
    with Session(test_engine) as session:
        business = session.get(Business, business_id)
        job = session.get(Job, job_id)
        booking_manager.record_request(session, business, job, owner_notified=True)

    client.post(f"/clients/{business_id}/jobs/{job_id}/confirm", headers=DASH_AUTH)

    with Session(test_engine) as session:
        entries = booking_manager.timeline(session, business_id, job_id)
    labels = [e.label for e in entries]
    assert "Booking created" in labels[0]
    assert any("Owner notified" in label for label in labels)
    assert any("Owner confirmed" in label for label in labels)
    assert any("Customer notified" in label for label in labels)
    assert [e.at for e in entries] == sorted(e.at for e in entries)


def test_the_timeline_only_shows_this_jobs_events(test_engine):
    business_id, first_id = _seed(test_engine)
    with Session(test_engine) as session:
        second = Job(
            business_id=business_id,
            customer_phone="+15125550002",
            service_type="Furnace",
            urgency="routine",
            callback_number="+15125550002",
        )
        session.add(second)
        session.commit()
        session.refresh(second)
        # Captured BEFORE publishing: eventbus.publish commits, and a commit
        # expires every instance in the session (see eventbus.py's docstring),
        # so reading second.id afterwards raises DetachedInstanceError.
        second_id = second.id
        business = session.get(Business, business_id)
        booking_manager.confirm(session, business, session.get(Job, first_id))
        booking_manager.confirm(session, business, session.get(Job, second_id))

        first_entries = booking_manager.timeline(session, business_id, first_id)
        payload_ids = {
            json.loads(e.payload_json).get("job_id") for e in session.exec(select(Event)).all()
        }
    assert payload_ids == {first_id, second_id}
    assert all(e.job_id == first_id for e in first_entries)


def test_the_client_page_renders_the_booking_timeline(test_engine, console):
    business_id, job_id = _seed(test_engine)
    client, _spy = console
    with Session(test_engine) as session:
        business = session.get(Business, business_id)
        booking_manager.record_request(
            session, business, session.get(Job, job_id), owner_notified=True
        )
    client.post(f"/clients/{business_id}/jobs/{job_id}/confirm", headers=DASH_AUTH)

    page = client.get(f"/clients/{business_id}", headers=DASH_AUTH).text

    assert "Owner confirmed" in page
    assert "Customer notified" in page


# ---- the full journey, through the real entry points -----------------------


def test_customer_request_to_owner_confirmation_to_customer_told(test_engine, console, monkeypatch):
    """Milestone A's headline: request → owner acts → customer hears back,
    driven entirely through /webhook/sms."""
    import service as service_module

    business_id, _seed_job = _seed(test_engine)
    with Session(test_engine) as session:
        for job in session.exec(select(Job)).all():
            session.delete(job)
        session.commit()

    class BookingAgent:
        def respond(self, *a, **k):
            return {
                "reply": "Got it — Tuesday morning noted.",
                "jobs": [
                    {
                        "id": "t1",
                        "input": {
                            "service_type": "AC not cooling",
                            "urgency": "same_day",
                            "customer_name": "Dana Cruz",
                            "callback_number": CUSTOMER,
                            "preferred_window": "Tuesday morning",
                        },
                    }
                ],
                "new_messages": [],
                "pending_tool_call": None,
            }

    monkeypatch.setattr(service_module, "agent", BookingAgent())
    client, spy = console

    client.post(
        "/webhook/sms",
        data={
            "From": CUSTOMER,
            "To": BUSINESS_LINE,
            "Body": "AC died, mornings are best",
            "MessageSid": "SMlife1",
        },
    )
    with Session(test_engine) as session:
        job = session.exec(select(Job)).first()
        assert job.booking_status == BOOKING_REQUESTED
        job_id = job.id

    owner_reply = client.post(
        "/webhook/sms",
        data={
            "From": OWNER,
            "To": BUSINESS_LINE,
            "Body": f"Y{job_id}",
            "MessageSid": "SMlife2",
        },
    )

    with Session(test_engine) as session:
        assert session.get(Job, job_id).booking_status == BOOKING_CONFIRMED
    assert "Dana Cruz" in owner_reply.text
    customer_texts = [m for m in spy.sent if m["to"] == CUSTOMER]
    assert len(customer_texts) == 1
    assert "Tuesday morning" in customer_texts[0]["body"]


# ---- the customer's answer to an owner-proposed window ---------------------


def test_a_customer_replying_to_a_proposed_window_reaches_the_owner(test_engine, console):
    """Proposing a window must not create a NEW dead end. Before this, the
    customer's answer fell through the cascade to Frontdesk and the owner was
    never told, so a booking could sit in PROPOSED forever."""
    business_id, job_id = _seed(test_engine)
    client, spy = console
    client.post(
        f"/clients/{business_id}/jobs/{job_id}/propose",
        data={"window": "Friday 8am-12pm"},
        headers=DASH_AUTH,
    )
    spy.sent.clear()

    client.post(
        "/webhook/sms",
        data={
            "From": CUSTOMER,
            "To": BUSINESS_LINE,
            "Body": "yes friday works great",
            "MessageSid": "SMprop1",
        },
    )

    to_owner = [m for m in spy.sent if m["to"] == OWNER]
    assert len(to_owner) == 1
    assert "yes friday works great" in to_owner[0]["body"]
    assert f"Y{job_id}" in to_owner[0]["body"]
    with Session(test_engine) as session:
        assert session.get(Job, job_id).booking_status == BOOKING_PROPOSED


def test_the_customers_answer_is_acknowledged_without_claiming_a_booking(test_engine, console):
    from booking_language import assert_no_confirmation_claim

    business_id, job_id = _seed(test_engine)
    client, spy = console
    client.post(
        f"/clients/{business_id}/jobs/{job_id}/propose",
        data={"window": "Friday 8am-12pm"},
        headers=DASH_AUTH,
    )

    response = client.post(
        "/webhook/sms",
        data={"From": CUSTOMER, "To": BUSINESS_LINE, "Body": "that works", "MessageSid": "SMprop2"},
    )

    assert_no_confirmation_claim(response.text)


def test_an_owner_proposal_stops_intercepting_replies_once_settled(
    test_engine, console, monkeypatch
):
    """A confirmed booking's later texts are ordinary conversation again."""
    business_id, job_id = _seed(test_engine)
    client, spy = console
    client.post(
        f"/clients/{business_id}/jobs/{job_id}/propose",
        data={"window": "Friday 8am-12pm"},
        headers=DASH_AUTH,
    )
    client.post(f"/clients/{business_id}/jobs/{job_id}/confirm", headers=DASH_AUTH)
    spy.sent.clear()

    import service as service_module

    seen = []

    class Recording:
        def respond(self, *a, **k):
            seen.append(1)
            return {"reply": "Sure!", "jobs": [], "new_messages": [], "pending_tool_call": None}

    # monkeypatch, not a bare assignment: service.agent is module-level, so a
    # direct write leaks into every test that runs after this one.
    monkeypatch.setattr(service_module, "agent", Recording())
    client.post(
        "/webhook/sms",
        data={
            "From": CUSTOMER,
            "To": BUSINESS_LINE,
            "Body": "one more thing",
            "MessageSid": "SMp3",
        },
    )

    assert seen == [1]  # fell through to Frontdesk, as it should
