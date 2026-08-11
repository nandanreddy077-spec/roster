"""PR 2 of IMPLEMENTATION-PLAN-001 — job.booked / job.completed reach the
event stream, and never at the expense of the booking that caused them.
"""

import json

import app as app_module
import bookings
from bookings import book_job
from conftest import DASH_AUTH
from db_models import Business, Customer, Event, Job
from events import JOB_BOOKED, JOB_COMPLETED
from fastapi.testclient import TestClient
from sqlmodel import Session, select


def _make_business(test_engine) -> int:
    with Session(test_engine) as s:
        b = Business(
            business_name="Test Co",
            trade="hvac",
            services_json=json.dumps(["AC repair"]),
            hours="9-5",
            pricing_faq="n/a",
            escalation_phone="+15550000000",
            inbound_number="+15559990000",
            email="events@test.io",
        )
        s.add(b)
        s.commit()
        s.refresh(b)
        return b.id


def _events(session, event_type):
    return session.exec(select(Event).where(Event.type == event_type)).all()


def test_new_booking_publishes_one_job_booked(test_engine):
    bid = _make_business(test_engine)
    with Session(test_engine) as s:
        business = s.get(Business, bid)
        job, created = book_job(
            s,
            business,
            "+15551112222",
            "+15551112222",
            {"service_type": "AC repair", "urgency": "same_day"},
            customer_id=None,
        )
        assert created is True

        rows = _events(s, JOB_BOOKED)
        assert len(rows) == 1
        assert rows[0].business_id == bid
        assert rows[0].dedup_key == f"job.booked:{job.id}"
        payload = json.loads(rows[0].payload_json)
        assert payload["job_id"] == job.id
        assert payload["service_type"] == "AC repair"
        assert payload["urgency"] == "same_day"


def test_event_carries_customer_id_when_known(test_engine):
    """DomainEvent.customer_id is a first-class column, not payload — the
    Identity Platform owns the customer, the event only references it."""
    bid = _make_business(test_engine)
    with Session(test_engine) as s:
        business = s.get(Business, bid)
        # Event.customer_id is a real foreign key, so a literal id with no
        # matching Customer row only ever worked on SQLite (no PRAGMA
        # foreign_keys=ON). Found running this suite against Postgres for the
        # first time, 2026-08-11.
        cust = Customer(business_id=bid, phone="+15551112222")
        s.add(cust)
        s.commit()
        s.refresh(cust)
        book_job(
            s,
            business,
            "+15551112222",
            "+15551112222",
            {"service_type": "AC repair", "urgency": "routine"},
            customer_id=cust.id,
        )
        assert _events(s, JOB_BOOKED)[0].customer_id == cust.id


def test_merge_rebook_publishes_nothing(test_engine):
    """book_job's whole reason for existing is that a re-book of the same
    service on the same thread MERGES rather than duplicating. That guarantee
    must reach the event stream too, or history would claim two bookings."""
    bid = _make_business(test_engine)
    with Session(test_engine) as s:
        business = s.get(Business, bid)
        first, created_first = book_job(
            s,
            business,
            "+15551112222",
            "+15551112222",
            {"service_type": "AC repair", "urgency": "routine"},
        )
        second, created_second = book_job(
            s,
            business,
            "+15551112222",
            "+15551112222",
            {"service_type": "AC repair", "urgency": "emergency", "address": "12 Elm St"},
        )

        assert created_first is True and created_second is False
        assert first.id == second.id
        assert len(_events(s, JOB_BOOKED)) == 1


def test_a_different_service_is_a_second_booking_and_a_second_event(test_engine):
    bid = _make_business(test_engine)
    with Session(test_engine) as s:
        business = s.get(Business, bid)
        book_job(
            s,
            business,
            "+15551112222",
            "+15551112222",
            {"service_type": "AC repair", "urgency": "routine"},
        )
        book_job(
            s,
            business,
            "+15551112222",
            "+15551112222",
            {"service_type": "Furnace install", "urgency": "routine"},
        )
        assert len(_events(s, JOB_BOOKED)) == 2


def test_publish_failure_never_loses_the_booking(monkeypatch, test_engine):
    """The safety property this whole PR rests on. If recording the event
    blows up, the customer is still booked and the caller still gets its
    (job, created) result — a failed audit write must never damage the thing
    it audits."""
    bid = _make_business(test_engine)

    class Exploding:
        def publish(self, session, event):
            raise RuntimeError("event store is down")

    monkeypatch.setattr(bookings, "bus", Exploding())

    with Session(test_engine) as s:
        business = s.get(Business, bid)
        job, created = book_job(
            s,
            business,
            "+15551112222",
            "+15551112222",
            {"service_type": "AC repair", "urgency": "emergency"},
        )
        assert created is True
        assert job.id is not None

    with Session(test_engine) as s:
        persisted = s.get(Job, job.id)
        assert persisted is not None
        assert persisted.service_type == "AC repair"
        assert _events(s, JOB_BOOKED) == []


def test_returned_job_is_still_readable_after_the_session_closes(test_engine):
    """book_job's contract: the Job it hands back can be read after its session
    closes. publish() commits, and a commit expires every instance in the
    session, so book_job refreshes AFTER publishing — get that order wrong and
    every field here raises DetachedInstanceError.

    test_idempotency.py catches this too, but by accident of where its asserts
    sit; this names the contract so a future regression can't be "fixed" by
    quietly moving an assertion inside the with-block."""
    bid = _make_business(test_engine)
    with Session(test_engine) as s:
        job, _ = book_job(
            s,
            s.get(Business, bid),
            "+15551112222",
            "+15551112222",
            {
                "service_type": "burst pipe",
                "urgency": "emergency",
                "preferred_window": "Thursday afternoon",
                "is_estimate": True,
            },
        )

    assert job.service_type == "burst pipe"
    assert job.urgency == "emergency"
    assert job.preferred_window == "Thursday afternoon"
    assert job.is_estimate is True


def test_marking_done_publishes_one_job_completed(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    bid = _make_business(test_engine)
    with Session(test_engine) as s:
        job = Job(
            business_id=bid,
            service_type="AC repair",
            urgency="routine",
            callback_number="+15551234567",
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        job_id = job.id

    client = TestClient(app_module.app, headers=DASH_AUTH)
    assert (
        client.post(f"/clients/{bid}/jobs/{job_id}/complete", follow_redirects=False).status_code
        == 303
    )

    with Session(test_engine) as s:
        rows = _events(s, JOB_COMPLETED)
        assert len(rows) == 1
        assert rows[0].business_id == bid
        assert rows[0].dedup_key == f"job.completed:{job_id}"
        assert json.loads(rows[0].payload_json)["job_id"] == job_id


def test_marking_done_twice_publishes_one_job_completed(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    bid = _make_business(test_engine)
    with Session(test_engine) as s:
        job = Job(business_id=bid, service_type="AC repair", urgency="routine")
        s.add(job)
        s.commit()
        s.refresh(job)
        job_id = job.id

    client = TestClient(app_module.app, headers=DASH_AUTH)
    client.post(f"/clients/{bid}/jobs/{job_id}/complete", follow_redirects=False)
    client.post(f"/clients/{bid}/jobs/{job_id}/complete", follow_redirects=False)

    with Session(test_engine) as s:
        assert len(_events(s, JOB_COMPLETED)) == 1


def test_no_subscribers_are_registered_anywhere(test_engine):
    """The precondition that makes eventbus's synchronous, in-process dispatch
    safe on the booking path. Registering a subscriber is an abort condition
    for this PR (IMPLEMENTATION-PLAN-001): a slow handler would then run inside
    a live booking. This fails the moment someone adds one, which is the point."""
    import app  # noqa: F401 — pulls in the whole live module graph
    import eventbus

    assert dict(eventbus.bus._subscribers) == {}
