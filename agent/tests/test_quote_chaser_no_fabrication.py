"""Quote Chaser never invents a time, and never hides why it couldn't offer one.

Three outcomes when an interested customer replies, and all three must be
distinguishable to the owner:

  1. calendar connected, windows free  -> offer them
  2. calendar connected, nothing free  -> ask the customer, tell the owner it's full
  3. calendar unreadable / not connected -> ask the customer, tell the owner it's broken

Collapsing 3 into 2 would hide a broken integration behind a plausible business
explanation, which is the failure mode the whole no-silent-failures milestone
exists to prevent.
"""

import json

import recovery_service
from calendar_provider import CalendarUnavailable
from conftest import StubAgent
from db_models import Business, Event, OwnerNotification, RecoveryJob
from deployment import deploy_role
from sqlmodel import Session, select


def make_client(session: Session, connected: bool = False) -> Business:
    client = Business(
        business_name="Ridgeline Plumbing",
        trade="Plumbing",
        hours="Mon-Sat 7am-7pm",
        pricing_faq="n/a",
        escalation_phone="+15550000000",
        inbound_number="+15559990000",
        timezone="America/New_York",
        google_refresh_token="token" if connected else None,
    )
    session.add(client)
    session.commit()
    session.refresh(client)
    assert client.id is not None
    deploy_role(session, client.id, "quote_chaser")
    session.refresh(client)
    return client


def _interested_lead(session: Session, client: Business) -> RecoveryJob:
    recovery_service.create_campaign(
        session,
        client,
        "quote",
        "June quotes",
        [{"phone": "+15551234567", "name": "Mike", "service_type": "AC install"}],
    )
    job = session.exec(select(RecoveryJob)).first()
    assert job is not None
    job.last_sent_day = 1
    session.add(job)
    session.commit()
    return job


def _say_interested(monkeypatch):
    monkeypatch.setattr(
        recovery_service,
        "agent",
        StubAgent(
            {
                "reply": "",
                "jobs": [],
                "new_messages": [],
                "pending_tool_call": {"name": "record_response", "input": {"intent": "interested"}},
            }
        ),
    )


class _Provider:
    connected = True

    def __init__(self, slots=None, raises=None):
        self._slots, self._raises = slots or [], raises

    def get_available_slots(self, business_hours="", days_ahead=7, count=3):
        if self._raises:
            raise self._raises
        return self._slots


def test_no_calendar_connected_offers_no_time_and_asks_the_customer(session, monkeypatch):
    client = make_client(session, connected=False)
    job = _interested_lead(session, client)
    _say_interested(monkeypatch)

    reply = recovery_service.handle_recovery_reply(session, client, job, "yes please")

    # No invented time reaches the customer.
    for giveaway in ("morning (9am", "afternoon (1pm", "Monday", "Tuesday", "Wednesday"):
        assert giveaway not in reply, f"a time was invented: {reply}"
    assert "what day and time" in reply.lower()
    session.refresh(job)
    assert json.loads(job.offered_slots_json or "[]") == []


def test_the_owner_is_told_when_no_calendar_is_connected(session, monkeypatch):
    client = make_client(session, connected=False)
    job = _interested_lead(session, client)
    _say_interested(monkeypatch)

    recovery_service.handle_recovery_reply(session, client, job, "yes please")

    events = session.exec(select(Event)).all()
    blocked = [e for e in events if "no_calendar_connected" in (e.dedup_key or "")]
    assert blocked, f"owner never told the calendar is missing: {[e.dedup_key for e in events]}"


def test_a_full_calendar_is_reported_as_full_not_as_broken(session, monkeypatch):
    client = make_client(session, connected=True)
    monkeypatch.setattr(recovery_service, "get_calendar_provider", lambda c: _Provider(slots=[]))
    job = _interested_lead(session, client)
    _say_interested(monkeypatch)

    recovery_service.handle_recovery_reply(session, client, job, "yes please")

    keys = [e.dedup_key or "" for e in session.exec(select(Event)).all()]
    assert any("calendar_fully_booked" in k for k in keys), keys
    assert not any("calendar_unreadable" in k for k in keys), keys


def test_an_unreadable_calendar_is_reported_as_broken_not_as_full(session, monkeypatch):
    client = make_client(session, connected=True)
    monkeypatch.setattr(
        recovery_service,
        "get_calendar_provider",
        lambda c: _Provider(raises=CalendarUnavailable("token revoked")),
    )
    job = _interested_lead(session, client)
    _say_interested(monkeypatch)

    reply = recovery_service.handle_recovery_reply(session, client, job, "yes please")

    keys = [e.dedup_key or "" for e in session.exec(select(Event)).all()]
    assert any("calendar_unreadable" in k for k in keys), keys
    assert not any("calendar_fully_booked" in k for k in keys), keys
    # The customer is never shown the plumbing.
    assert "token revoked" not in reply
    assert "what day and time" in reply.lower()


def test_a_customer_who_needs_a_human_reaches_one(session, monkeypatch):
    """The systemic alert is deduped per business, so it fires once. This
    customer still needs an actual human — the per-customer escalation is what
    guarantees that, and it also releases them from the rigid slot machine."""
    client = make_client(session, connected=False)
    job = _interested_lead(session, client)
    _say_interested(monkeypatch)

    recovery_service.handle_recovery_reply(session, client, job, "yes please")

    session.refresh(job)
    assert job.current_status == "escalated"
    assert job.current_status not in recovery_service.ACTIVE_STATUSES
    notes = session.exec(select(OwnerNotification)).all()
    assert notes, "no owner notification for a customer who wants to book"


def test_a_connected_calendar_with_free_windows_still_offers_them(session, monkeypatch):
    """The positive path must not regress: real slots are still offered."""
    client = make_client(session, connected=True)
    monkeypatch.setattr(
        recovery_service,
        "get_calendar_provider",
        lambda c: _Provider(slots=["Tuesday 09/02 morning (9am-12pm)"]),
    )
    job = _interested_lead(session, client)
    _say_interested(monkeypatch)

    reply = recovery_service.handle_recovery_reply(session, client, job, "yes please")

    assert "Tuesday 09/02 morning (9am-12pm)" in reply
    session.refresh(job)
    assert job.current_status == "awaiting_slot"
    assert json.loads(job.offered_slots_json) == ["Tuesday 09/02 morning (9am-12pm)"]


def test_repeated_interested_replies_do_not_spam_the_owner(session, monkeypatch):
    """Dedup is per (business, role, cause): a second interested customer with
    the same missing calendar must not page the owner again."""
    client = make_client(session, connected=False)
    _say_interested(monkeypatch)

    for phone in ("+15551110001", "+15551110002"):
        recovery_service.create_campaign(
            session, client, "quote", "c", [{"phone": phone, "service_type": "AC install"}]
        )
    for job in session.exec(select(RecoveryJob)).all():
        job.last_sent_day = 1
        session.add(job)
    session.commit()

    for job in session.exec(select(RecoveryJob)).all():
        recovery_service.handle_recovery_reply(session, client, job, "yes please")

    keys = [e.dedup_key or "" for e in session.exec(select(Event)).all()]
    blocked = [k for k in keys if "no_calendar_connected" in k]
    assert len(blocked) == 1, f"owner paged {len(blocked)} times for one cause: {blocked}"
