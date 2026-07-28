"""The durable owner-notification log. Today every owner alert is a
fire-and-forget SMS: it vanishes into a text thread, and a failed send is
swallowed silently (notifications.py's bare `except`). These rows are what
the Notifications page reads (blueprint §4), and the first place a failed
owner alert is visible at all."""
from sqlmodel import select

from db_models import Business, OwnerNotification
from notifications import (
    KIND_CALL_DROPPED,
    KIND_ESCALATION,
    KIND_JOB_BOOKED,
    SOURCE_ALERT_OWNER,
    SOURCE_CALL_DROPPED,
    SOURCE_SMS_BOOKING,
    SOURCE_VOICE_BOOKING,
    is_test_thread,
    record_owner_notification,
)


def test_records_a_delivered_notification(session):
    row = record_owner_notification(
        session, business_id=1, kind=KIND_JOB_BOOKED, source=SOURCE_SMS_BOOKING,
        message="Frontdesk just booked a job", delivered=True,
    )
    assert row.id is not None
    stored = session.exec(select(OwnerNotification)).all()
    assert len(stored) == 1
    assert stored[0].business_id == 1
    assert stored[0].kind == KIND_JOB_BOOKED
    assert stored[0].source == SOURCE_SMS_BOOKING
    assert stored[0].message == "Frontdesk just booked a job"
    assert stored[0].delivered is True


def test_records_an_undelivered_notification(session):
    """H5: a send that failed, or a business with no escalation phone set,
    still gets a row — marked undelivered. The dashboard is then the only
    place that alert exists, which is the entire point of the log."""
    record_owner_notification(
        session, business_id=1, kind=KIND_ESCALATION, source=SOURCE_ALERT_OWNER,
        message="URGENT — caller needs you", delivered=False,
    )
    stored = session.exec(select(OwnerNotification)).all()
    assert stored[0].delivered is False


def test_a_new_notification_starts_unread(session):
    """read_at has no producer until the Notifications page ships (Phase 5).
    It defaults to None so that page has a real unread signal to read."""
    row = record_owner_notification(
        session, business_id=1, kind=KIND_JOB_BOOKED, source=SOURCE_SMS_BOOKING,
        message="m", delivered=True,
    )
    assert row.read_at is None


def test_a_failed_write_returns_none_and_never_raises():
    """The log is strictly less important than the booking that just
    committed. A broken session must not propagate an exception into the
    alert path — the caller has already done the work that matters."""
    class BrokenSession:
        def add(self, _row):
            raise RuntimeError("database is gone")

        def rollback(self):
            raise RuntimeError("rollback also fails")

    assert record_owner_notification(
        BrokenSession(), business_id=1, kind=KIND_JOB_BOOKED,
        source=SOURCE_SMS_BOOKING, message="m", delivered=True,
    ) is None


def test_the_four_sources_are_distinct(session):
    """`source` is the operational axis: it exists so an SMS booking and a
    voice booking — both KIND_JOB_BOOKED — can be told apart without
    string-matching the message body."""
    sources = {
        SOURCE_SMS_BOOKING, SOURCE_VOICE_BOOKING,
        SOURCE_ALERT_OWNER, SOURCE_CALL_DROPPED,
    }
    assert len(sources) == 4
    assert KIND_CALL_DROPPED == SOURCE_CALL_DROPPED  # same string, different axes


def test_is_test_thread_matches_the_threads_that_skip_owner_sms():
    """Must stay in lockstep with notifications._TEST_THREADS — the log and
    the SMS have to agree about what counts as real activity, or the
    dashboard shows the owner their own test bookings."""
    assert is_test_thread("dashboard") is True
    assert is_test_thread("portal-test") is True
    assert is_test_thread("+15125550123") is False


# --- the booking paths -------------------------------------------------------


class _SpyChannel:
    def __init__(self):
        self.sent = []

    def send(self, from_number, to_number, body):
        self.sent.append((from_number, to_number, body))


class _BoomChannel:
    def send(self, *_a, **_kw):
        raise RuntimeError("twilio down")


class _BookingAgent:
    def respond(self, client_config, history, tools=None, system_prompt=None, max_iters=None):
        return {
            "reply": "You're booked!",
            "jobs": [{"id": "t1", "input": {
                "service_type": "AC Repair", "urgency": "same_day", "customer_name": "Sarah",
            }}],
            "new_messages": [], "pending_tool_call": None,
        }


def _live_business(session, email):
    biz = Business(
        business_name="B", trade="hvac", email=email, frontdesk_live=True,
        trial_cap_cents=10000, escalation_phone="+15550001111",
        inbound_number="+15550002222",
    )
    session.add(biz)
    session.commit()
    session.refresh(biz)
    return biz


def test_sms_booking_records_a_delivered_notification(session, monkeypatch):
    import notifications
    import service
    monkeypatch.setattr(service, "agent", _BookingAgent())
    monkeypatch.setattr(notifications, "_owner_channel", _SpyChannel())
    biz = _live_business(session, "log1@test.io")

    service.handle_customer_message(session, biz, "+15557778888", "my AC is dead")

    rows = session.exec(select(OwnerNotification)).all()
    assert len(rows) == 1
    assert rows[0].kind == KIND_JOB_BOOKED
    assert rows[0].source == SOURCE_SMS_BOOKING
    assert rows[0].business_id == biz.id
    assert rows[0].delivered is True
    assert "AC Repair" in rows[0].message


def test_voice_booking_records_a_notification_with_the_voice_source(session, monkeypatch):
    """Same kind as an SMS booking, different origin. Distinguishing the two
    without parsing `message` is exactly what `source` is for."""
    import asyncio

    import xai_voice_adapter as adapter
    monkeypatch.setattr(adapter, "notify_owner_of_booking", lambda *a, **k: True)
    biz = _live_business(session, "log-voice@test.io")

    asyncio.run(adapter._persist_job(
        session, biz, "xai-voice:c9", "+15125559999",
        {"service_type": "Burst pipe", "urgency": "emergency"},
    ))

    rows = session.exec(select(OwnerNotification)).all()
    assert len(rows) == 1
    assert rows[0].kind == KIND_JOB_BOOKED
    assert rows[0].source == SOURCE_VOICE_BOOKING
    assert "Burst pipe" in rows[0].message


def test_a_failed_owner_sms_still_records_an_undelivered_notification(session, monkeypatch):
    """H5: the whole reason the log exists. The SMS is gone, but the owner can
    still find out this job was booked."""
    import notifications
    import service
    monkeypatch.setattr(service, "agent", _BookingAgent())
    monkeypatch.setattr(notifications, "_owner_channel", _BoomChannel())
    biz = _live_business(session, "log2@test.io")

    service.handle_customer_message(session, biz, "+15557778888", "my AC is dead")

    rows = session.exec(select(OwnerNotification)).all()
    assert len(rows) == 1
    assert rows[0].delivered is False


def test_dashboard_test_bookings_are_not_logged(session, monkeypatch):
    """Mirrors the SMS skip exactly — an owner testing their own AI must not
    fill their notifications feed with activity that never happened."""
    import notifications
    import service
    monkeypatch.setattr(service, "agent", _BookingAgent())
    monkeypatch.setattr(notifications, "_owner_channel", _SpyChannel())
    biz = _live_business(session, "log3@test.io")

    service.handle_customer_message(session, biz, "portal-test", "testing my receptionist")

    assert session.exec(select(OwnerNotification)).all() == []


def test_a_repeat_turn_on_the_same_job_does_not_log_twice(session, monkeypatch):
    """Booking alerts are already deduplicated by book_job's `created` flag —
    a detail-merge re-runs the turn but must not produce a second row."""
    import notifications
    import service
    monkeypatch.setattr(service, "agent", _BookingAgent())
    monkeypatch.setattr(notifications, "_owner_channel", _SpyChannel())
    biz = _live_business(session, "log4@test.io")

    service.handle_customer_message(session, biz, "+15557778888", "my AC is dead")
    service.handle_customer_message(session, biz, "+15557778888", "it's also leaking")

    assert len(session.exec(select(OwnerNotification)).all()) == 1
