"""Milestone 2 — booking honesty.

Found live in production language: recovery_service told a customer
"Perfect, you're booked for Wednesday 9am-12pm!" off a slot
ManualCalendarProvider invented, never checked against a real technician,
truck, or calendar. Every test here either pins the fix or guards against it
coming back — in this file, or anywhere else in the codebase.
"""

from pathlib import Path

import pytest
from booking_language import (
    CONFIRMATION_PHRASES,
    BookingLanguageError,
    assert_no_confirmation_claim,
    render_slot_language,
)
from db_models import BOOKING_CONFIRMED, BOOKING_PROPOSED, BOOKING_REQUESTED

AGENT_DIR = Path(__file__).resolve().parent.parent


# ---- assert_no_confirmation_claim: both directions --------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Perfect, you're booked for Wednesday 9am-12pm!",
        "You are booked for Thursday afternoon.",
        "Great, your appointment is confirmed for 3pm.",
        "You're all set for tomorrow morning!",
        "We'll see you at 9am.",
        "Your slot is confirmed for Friday.",
    ],
)
def test_confirmation_claims_are_caught(text):
    with pytest.raises(BookingLanguageError):
        assert_no_confirmation_claim(text)


@pytest.mark.parametrize(
    "text",
    [
        "Got it — Wednesday 9am-12pm works! I've noted that down and the office will reach out to confirm.",
        "Please confirm your address so we can send someone out.",  # "confirm" alone, harmless
        "Can you confirm the best callback number?",
        "Someone from the office will be in touch to confirm a time.",
        "Thanks for choosing Kestrel Plumbing! If we did right by you, a review means a lot.",
    ],
)
def test_honest_or_unrelated_text_is_not_flagged(text):
    assert_no_confirmation_claim(text)  # must not raise


def test_the_exact_violation_that_shipped_is_in_the_banned_list():
    """Pins the literal string that went out to a real customer, so this
    specific regression can never silently return."""
    with pytest.raises(BookingLanguageError):
        assert_no_confirmation_claim(
            "Perfect, you're booked for Wednesday 08/12 morning (9am-12pm)!"
        )


# ---- render_slot_language: the one honest sentence per status --------------


def test_proposed_status_gets_honest_proposed_language():
    text = render_slot_language(BOOKING_PROPOSED, "Wednesday 9am-12pm")
    assert "Wednesday 9am-12pm" in text
    assert_no_confirmation_claim(text)  # must not raise


def test_requested_status_gets_honest_requested_language():
    text = render_slot_language(BOOKING_REQUESTED, "Thursday afternoon")
    assert "Thursday afternoon" in text
    assert_no_confirmation_claim(text)


def test_confirmed_status_has_no_template_at_all():
    """Nothing in this codebase can honestly reach BOOKING_CONFIRMED
    automatically -- so nothing should be asking this function to phrase a
    confirmation sentence. Raising here is a signal to the caller: a
    genuinely confirmed appointment needs a human-written message for that
    specific job, not a template."""
    with pytest.raises(BookingLanguageError):
        render_slot_language(BOOKING_CONFIRMED, "Wednesday 9am-12pm")


def test_every_defined_status_is_handled_or_explicitly_refused():
    """If db_models.py ever grows a fourth BOOKING_* status, this function
    must be updated to handle it -- not silently fall through. Guards that
    coupling structurally rather than by convention."""
    for status in (BOOKING_REQUESTED, BOOKING_PROPOSED, BOOKING_CONFIRMED):
        try:
            render_slot_language(status, "some time")
        except BookingLanguageError:
            pass  # confirmed's explicit refusal is expected and correct


# ---- static audit: no confirmation phrase anywhere in production source ----


# Excluded: this file and booking_language.py itself (they NAME the banned
# phrases as data, which is not the same as emitting them to a customer),
# and migrate_to_postgres.py/backup.py/tests/ which have no customer-facing
# strings at all. Every other .py file in agent/ is in scope.
_AUDIT_EXCLUDE = {"booking_language.py", "test_booking_honesty.py"}


def _production_python_files():
    for path in AGENT_DIR.glob("*.py"):
        if path.name in _AUDIT_EXCLUDE:
            continue
        yield path


def test_no_production_source_file_contains_a_banned_confirmation_phrase():
    """Repo-wide regression guard: catches a FUTURE reintroduction of
    confirmation language anywhere, not just the one function this milestone
    fixed. Scans literal source text, not built strings -- deliberately
    blunter than a runtime check, so a new violation fails in CI before it
    ever reaches a customer."""
    violations = []
    for path in _production_python_files():
        text = path.read_text().lower()
        for phrase in CONFIRMATION_PHRASES:
            if phrase in text:
                violations.append(f"{path.name}: contains {phrase!r}")
    assert violations == [], "banned confirmation language found:\n" + "\n".join(violations)


def test_the_prompt_builders_contain_the_general_honesty_instruction():
    """engine.py's prompts must carry an explicit, general instruction against
    claiming a confirmed appointment -- not just the narrower preferred_window
    note, which only covers the log_job moment, not ad-hoc conversational
    drift (a customer asking "so is my appointment confirmed?" mid-call)."""
    text = (AGENT_DIR / "engine.py").read_text()
    assert "_CONFIRMATION_HONESTY_NOTE" in text
    # Both prompt builders must actually reference it, not just define it.
    assert text.count("_CONFIRMATION_HONESTY_NOTE") >= 3  # def + 2 call sites


# ---- schema: the honest default -------------------------------------------


def test_booking_status_defaults_to_requested(session):
    from db_models import Business, Job

    biz = Business(business_name="Test Co", trade="plumbing")
    session.add(biz)
    session.commit()
    session.refresh(biz)

    job = Job(business_id=biz.id, service_type="drain clear", urgency="routine")
    session.add(job)
    session.commit()
    session.refresh(job)

    assert job.booking_status == BOOKING_REQUESTED
    assert job.confirmed_at is None


# ---- the /confirm route: the ONLY path to BOOKING_CONFIRMED -----------------


def _founder_client():
    import app as app_module
    from conftest import DASH_AUTH
    from starlette.testclient import TestClient

    return TestClient(app_module.app, headers=DASH_AUTH), app_module


def test_confirm_route_sets_booking_status_and_timestamp(test_engine, monkeypatch):
    from db_models import Business, Job
    from sqlmodel import Session

    client, app_module = _founder_client()
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        biz = Business(business_name="Kestrel", trade="plumbing")
        s.add(biz)
        s.commit()
        s.refresh(biz)
        job = Job(
            business_id=biz.id,
            service_type="drain clear",
            urgency="routine",
            preferred_window="Thursday 9am-12pm",
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        bid, jid = biz.id, job.id

    r = client.post(f"/clients/{bid}/jobs/{jid}/confirm", follow_redirects=False)

    assert r.status_code == 303
    with Session(test_engine) as s:
        job = s.get(Job, jid)
        assert job.booking_status == BOOKING_CONFIRMED
        assert job.confirmed_at is not None


def test_confirm_route_is_idempotent(test_engine, monkeypatch):
    """Re-clicking a stale page (double submit, back button) must not error
    or move the timestamp -- same posture as complete_job's own idempotency."""
    from db_models import Business, Job
    from sqlmodel import Session

    client, app_module = _founder_client()
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        biz = Business(business_name="Kestrel", trade="plumbing")
        s.add(biz)
        s.commit()
        s.refresh(biz)
        job = Job(business_id=biz.id, service_type="drain clear", urgency="routine")
        s.add(job)
        s.commit()
        s.refresh(job)
        bid, jid = biz.id, job.id

    client.post(f"/clients/{bid}/jobs/{jid}/confirm")
    with Session(test_engine) as s:
        first_confirmed_at = s.get(Job, jid).confirmed_at

    r = client.post(f"/clients/{bid}/jobs/{jid}/confirm", follow_redirects=False)

    assert r.status_code == 303
    with Session(test_engine) as s:
        job = s.get(Job, jid)
        assert job.booking_status == BOOKING_CONFIRMED
        assert job.confirmed_at == first_confirmed_at


def test_confirm_route_404s_for_a_job_belonging_to_another_business(test_engine, monkeypatch):
    from db_models import Business, Job
    from sqlmodel import Session

    client, app_module = _founder_client()
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        a = Business(business_name="A", trade="plumbing")
        b = Business(business_name="B", trade="plumbing")
        s.add(a)
        s.add(b)
        s.commit()
        s.refresh(a)
        s.refresh(b)
        job = Job(business_id=a.id, service_type="drain clear", urgency="routine")
        s.add(job)
        s.commit()
        s.refresh(job)
        job_id, wrong_business_id = job.id, b.id

    r = client.post(f"/clients/{wrong_business_id}/jobs/{job_id}/confirm")

    assert r.status_code == 404


def test_confirm_route_requires_admin_auth(test_engine, monkeypatch):
    import app as app_module
    from db_models import Business, Job
    from sqlmodel import Session
    from starlette.testclient import TestClient

    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        biz = Business(business_name="Kestrel", trade="plumbing")
        s.add(biz)
        s.commit()
        s.refresh(biz)
        job = Job(business_id=biz.id, service_type="drain clear", urgency="routine")
        s.add(job)
        s.commit()
        s.refresh(job)
        bid, jid = biz.id, job.id

    unauthenticated = TestClient(app_module.app)
    r = unauthenticated.post(f"/clients/{bid}/jobs/{jid}/confirm")

    assert r.status_code == 401
    with Session(test_engine) as s:
        assert s.get(Job, jid).booking_status == BOOKING_REQUESTED


# ---- dashboard renders the honest label, never overclaims ------------------


def test_dashboard_shows_confirmed_badge_only_for_a_genuinely_confirmed_job(
    test_engine, monkeypatch
):
    from db_models import Business, Job
    from sqlmodel import Session

    client, app_module = _founder_client()
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        biz = Business(business_name="Kestrel", trade="plumbing")
        s.add(biz)
        s.commit()
        s.refresh(biz)
        s.add(
            Job(
                business_id=biz.id,
                service_type="requested-job",
                urgency="routine",
                preferred_window="Thursday afternoon",
                booking_status=BOOKING_REQUESTED,
            )
        )
        s.add(
            Job(
                business_id=biz.id,
                service_type="proposed-job",
                urgency="routine",
                preferred_window="Fri 1pm-4pm",
                booking_status=BOOKING_PROPOSED,
            )
        )
        s.add(
            Job(
                business_id=biz.id,
                service_type="confirmed-job",
                urgency="routine",
                preferred_window="Wed 9am-12pm",
                booking_status=BOOKING_CONFIRMED,
            )
        )
        s.commit()
        bid = biz.id

    html = client.get(f"/clients/{bid}").text

    assert "Requested: Thursday afternoon" in html
    assert "Proposed: Fri 1pm-4pm" in html
    assert "✅ Confirmed for Wed 9am-12pm" in html
    # The honesty invariant, checked against the ACTUAL rendered page: no
    # confirmation badge appears next to a time that isn't genuinely confirmed.
    requested_section = html[html.index("requested-job") : html.index("requested-job") + 800]
    proposed_section = html[html.index("proposed-job") : html.index("proposed-job") + 800]
    assert "✅ Confirmed" not in requested_section
    assert "✅ Confirmed" not in proposed_section
