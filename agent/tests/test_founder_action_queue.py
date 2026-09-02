"""The founder console's work queue (`app._founder_action_queue`).

The client page used to be a flat 378-line scroll: ten sections at identical
visual weight, config forms interleaved with live work, and the bookings a
customer is right now waiting on sitting at the bottom, underneath three "save
this text field" forms. So the one thing done daily was the hardest thing to
find (founder, 2026-09-01: "the dashboard is so confusing").

The queue is what fixed it. These tests guard the two properties that make it
worth having: it says only true things, and it ranks by what costs the most to
leave alone.
"""

import app as app_module
from conftest import DASH_AUTH
from db_models import Business, DepartmentInterest, Job
from sqlmodel import Session
from starlette.testclient import TestClient


def _queue(client: Business, jobs=(), interests=(), checklist=None):
    """Called directly: the queue is pure over rows the route already loaded,
    so it needs no session, no engine and no HTTP round trip to test."""
    if checklist is None:
        checklist = [{"label": "Everything", "done": True, "hint": ""}]
    return app_module._founder_action_queue(client, list(jobs), list(interests), checklist)


def _client(**kw):
    defaults = dict(
        business_name="Ridgeline Plumbing",
        trade="Plumbing",
        billing_state="paid",
        trial_spend_cents=0,
        trial_cap_cents=2000,
    )
    return Business(**{**defaults, **kw})


def test_a_healthy_client_produces_an_empty_queue():
    """Not a reassuring placeholder — an empty list, so the template can show
    "nothing needs you" rather than inventing a task to fill the block."""
    assert _queue(_client()) == []


def test_a_booking_with_an_unanswered_time_is_queued():
    """The most expensive thing on the page to miss: a customer is waiting to
    hear whether they have an appointment."""
    job = Job(
        business_id=1,
        customer_phone="+15125550001",
        service_type="Drain cleaning",
        urgency="routine",
        preferred_window="Thursday 8am-12pm",
        booking_status="requested",
    )

    queue = _queue(_client(), jobs=[job])

    assert len(queue) == 1
    assert "1 booking waiting on you to confirm a time" in queue[0]["text"]
    assert queue[0]["href"] == "#jobs"


def test_a_settled_booking_is_not_queued():
    """Confirmed and cancelled are terminal — surfacing them would train the
    founder to ignore the block, which is how an attention list dies."""
    for status in ("confirmed", "cancelled"):
        job = Job(
            business_id=1,
            customer_phone="+15125550002",
            service_type="Drain cleaning",
            urgency="routine",
            preferred_window="Thursday 8am-12pm",
            booking_status=status,
        )

        assert _queue(_client(), jobs=[job]) == [], status


def test_a_job_with_no_time_in_play_is_not_queued():
    """Nobody is waiting on an answer about a time nobody proposed."""
    job = Job(
        business_id=1,
        customer_phone="+15125550003",
        service_type="Drain cleaning",
        urgency="routine",
        booking_status="requested",
    )

    assert _queue(_client(), jobs=[job]) == []


def test_unhandled_expansion_requests_are_queued():
    queue = _queue(
        _client(),
        interests=[{"interest": DepartmentInterest(business_id=1, department_key="sales")}],
    )

    assert "1 expansion request unhandled" in queue[0]["text"]
    assert queue[0]["href"] == "#expansion"


def test_an_incomplete_setup_names_the_next_step():
    checklist = [
        {"label": "Business details captured", "done": True, "hint": ""},
        {"label": "AI phone number provisioned", "done": False, "hint": "Buy one."},
        {"label": "A department staffed", "done": False, "hint": "Deploy one."},
    ]

    queue = _queue(_client(), checklist=checklist)

    # The NEXT one, singular — a queue that lists every outstanding step is
    # the checklist again, which is what this block replaced.
    assert len(queue) == 1
    assert "AI phone number provisioned" in queue[0]["text"]
    assert "A department staffed" not in queue[0]["text"]


def test_a_silenced_workforce_outranks_everything_else():
    """The trial cap doesn't slow this client down, it stops their employees
    replying — the one state where the product is actively not working. It
    leads the queue and it is the only entry allowed to be critical."""
    job = Job(
        business_id=1,
        customer_phone="+15125550004",
        service_type="Drain cleaning",
        urgency="routine",
        preferred_window="Thursday 8am-12pm",
        booking_status="requested",
    )
    capped = _client(billing_state="trial", trial_spend_cents=2000, trial_cap_cents=2000)

    queue = _queue(capped, jobs=[job])

    assert queue[0]["severity"] == "critical"
    assert "stopped replying" in queue[0]["text"]
    assert [item["severity"] for item in queue[1:]] == ["high"]


def test_a_paid_client_over_the_old_cap_is_never_flagged():
    """Paid removes the cap entirely — flagging spend on a paying customer
    would be a false alarm, and a work queue survives on being true."""
    paid = _client(billing_state="paid", trial_spend_cents=99999, trial_cap_cents=2000)

    assert _queue(paid) == []


def test_the_client_page_leads_with_the_queue_not_with_setup(test_engine, monkeypatch):
    """The whole reorganisation, as one assertion: what needs doing appears
    before the setup panel, which is now folded away."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        b = Business(business_name="Ridgeline Plumbing", trade="Plumbing")
        s.add(b)
        s.commit()
        s.refresh(b)
        bid = b.id

    body = TestClient(app_module.app, headers=DASH_AUTH).get(f"/clients/{bid}").text

    assert body.index("Needs you") < body.index("Setup &amp; configuration")
    # Folded, not deleted: every control the flat page had is still on it.
    assert "console-fold" in body
    for action in ("/provision-number", "/pipeline-stage", "/employees/deploy", "/delete"):
        assert action in body, f"{action} disappeared in the reorganisation"
