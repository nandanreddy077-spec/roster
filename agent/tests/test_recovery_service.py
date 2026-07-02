import json
from datetime import datetime, timedelta

from sqlmodel import Session, select

from db_models import Client, Job, RecoveryJob, RecoveryMessageLog
import recovery_service
from conftest import StubAgent


def make_client(session: Session) -> Client:
    client = Client(
        business_name="Test Co", trade="HVAC", services_json=json.dumps(["AC repair"]),
        hours="9-5", pricing_faq="n/a", escalation_phone="+15550000000",
        inbound_number="+15559990000",
    )
    session.add(client)
    session.commit()
    session.refresh(client)
    return client


def test_create_campaign_creates_one_job_per_customer(session):
    client = make_client(session)
    customers = [
        {"phone": "+1", "name": "Mike", "service_type": "AC install", "estimate_amount": "8000"},
        {"phone": "+2", "name": "Sue", "service_type": "Furnace repair", "estimate_amount": "3000"},
    ]

    campaign = recovery_service.create_campaign(session, client, "quote", "June quotes", customers)

    jobs = session.exec(select(RecoveryJob).where(RecoveryJob.campaign_id == campaign.id)).all()
    assert len(jobs) == 2
    assert {j.customer_phone for j in jobs} == {"+1", "+2"}
    assert all(j.current_status == "pending" for j in jobs)


class FakeSMSChannel:
    def __init__(self):
        self.sent = []

    def send(self, from_number, to_number, body):
        self.sent.append({"from": from_number, "to": to_number, "body": body})


def test_tick_sends_day_one_message_once_elapsed(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(recovery_service, "sms_channel", fake_channel)

    client = make_client(session)
    campaign = recovery_service.create_campaign(
        session, client, "quote", "June quotes",
        [{"phone": "+1", "name": "Mike", "service_type": "AC install", "estimate_amount": "8000"}],
    )
    campaign.started_at = datetime.utcnow() - timedelta(days=1)
    session.add(campaign)
    session.commit()

    sent = recovery_service.tick(session)

    assert len(sent) == 1
    assert fake_channel.sent[0]["to"] == "+1"
    assert "Mike" in fake_channel.sent[0]["body"]
    logs = session.exec(select(RecoveryMessageLog)).all()
    assert len(logs) == 1
    assert logs[0].message_day == 1


def test_tick_does_not_resend_same_day_twice(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(recovery_service, "sms_channel", fake_channel)

    client = make_client(session)
    campaign = recovery_service.create_campaign(
        session, client, "quote", "June quotes",
        [{"phone": "+1", "name": "Mike", "service_type": "AC install"}],
    )
    campaign.started_at = datetime.utcnow() - timedelta(days=1)
    session.add(campaign)
    session.commit()

    recovery_service.tick(session)
    second = recovery_service.tick(session)

    assert second == []
    assert len(fake_channel.sent) == 1


def test_tick_marks_no_response_after_final_day(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(recovery_service, "sms_channel", fake_channel)

    client = make_client(session)
    campaign = recovery_service.create_campaign(
        session, client, "quote", "June quotes",
        [{"phone": "+1", "name": "Mike", "service_type": "AC install"}],
    )
    campaign.started_at = datetime.utcnow() - timedelta(days=40)
    session.add(campaign)
    session.commit()

    recovery_service.tick(session)  # catches up: sends the one unsent due message (day 28)
    recovery_service.tick(session)  # nothing left to send -> marks no_response

    job = session.exec(select(RecoveryJob)).first()
    assert job.current_status == "no_response"


class RaisingSMSChannel:
    """Raises for one phone number, sends normally for everyone else — simulates
    a single bad number (e.g. malformed, opted-out) failing mid-batch."""

    def __init__(self, bad_phone):
        self.bad_phone = bad_phone
        self.sent = []

    def send(self, from_number, to_number, body):
        if to_number == self.bad_phone:
            raise RuntimeError("simulated Twilio failure")
        self.sent.append({"from": from_number, "to": to_number, "body": body})


def test_tick_isolates_per_job_failure(session, monkeypatch):
    fake_channel = RaisingSMSChannel(bad_phone="+1")
    monkeypatch.setattr(recovery_service, "sms_channel", fake_channel)

    client = make_client(session)
    campaign = recovery_service.create_campaign(
        session, client, "quote", "June quotes",
        [
            {"phone": "+1", "name": "Bad", "service_type": "AC install"},
            {"phone": "+2", "name": "Good", "service_type": "Furnace repair"},
        ],
    )
    campaign.started_at = datetime.utcnow() - timedelta(days=1)
    session.add(campaign)
    session.commit()

    sent = recovery_service.tick(session)

    assert len(sent) == 1
    assert sent[0].customer_phone == "+2"
    assert fake_channel.sent[0]["to"] == "+2"
    good_job = session.exec(select(RecoveryJob).where(RecoveryJob.customer_phone == "+2")).first()
    assert good_job.last_sent_day == 1
    bad_job = session.exec(select(RecoveryJob).where(RecoveryJob.customer_phone == "+1")).first()
    assert bad_job.last_sent_day is None  # failed send left this job untouched, ready to retry


def test_handle_recovery_reply_stop_keyword_bypasses_llm(session, monkeypatch):
    client = make_client(session)
    recovery_service.create_campaign(
        session, client, "quote", "June quotes",
        [{"phone": "+1", "name": "Mike", "service_type": "AC install"}],
    )
    job = session.exec(select(RecoveryJob)).first()
    job.last_sent_day = 1
    session.add(job)
    session.commit()

    class ExplodingAgent:
        def respond(self, *args, **kwargs):
            raise AssertionError("LLM should never be called for a STOP reply")

    monkeypatch.setattr(recovery_service, "agent", ExplodingAgent())

    reply = recovery_service.handle_recovery_reply(session, client, job, "STOP")

    session.refresh(job)
    assert job.current_status == "declined"
    assert "unsubscribed" in reply.lower()


def test_handle_recovery_reply_awaiting_slot_can_decline(session, monkeypatch):
    client = make_client(session)
    recovery_service.create_campaign(
        session, client, "quote", "June quotes",
        [{"phone": "+1", "name": "Mike", "service_type": "AC install"}],
    )
    job = session.exec(select(RecoveryJob)).first()
    job.current_status = "awaiting_slot"
    job.offered_slots_json = json.dumps(["Monday morning", "Tuesday afternoon", "Wednesday morning"])
    session.add(job)
    session.commit()

    monkeypatch.setattr(
        recovery_service,
        "agent",
        StubAgent({
            "reply": "",
            "jobs": [],
            "new_messages": [],
            "pending_tool_call": {"name": "record_response", "input": {"intent": "not_interested"}},
        }),
    )

    recovery_service.handle_recovery_reply(session, client, job, "actually never mind, don't text me again")

    session.refresh(job)
    assert job.current_status == "declined"
    assert job.booked_job_id is None


def test_handle_recovery_reply_interested_offers_slots(session, monkeypatch):
    client = make_client(session)
    recovery_service.create_campaign(
        session, client, "quote", "June quotes",
        [{"phone": "+1", "name": "Mike", "service_type": "AC install"}],
    )
    job = session.exec(select(RecoveryJob)).first()
    job.last_sent_day = 1
    session.add(job)
    session.commit()
    session.add(RecoveryMessageLog(recovery_job_id=job.id, message_day=1, message_text="Hi Mike..."))
    session.commit()

    monkeypatch.setattr(
        recovery_service,
        "agent",
        StubAgent({
            "reply": "",
            "jobs": [],
            "new_messages": [],
            "pending_tool_call": {"name": "record_response", "input": {"intent": "interested"}},
        }),
    )

    reply = recovery_service.handle_recovery_reply(session, client, job, "Yes I'm interested!")

    session.refresh(job)
    assert job.current_status == "awaiting_slot"
    assert len(job.offered_slots) == 3
    assert "1)" in reply


def test_handle_recovery_reply_confirm_slot_books_job(session, monkeypatch):
    client = make_client(session)
    recovery_service.create_campaign(
        session, client, "quote", "June quotes",
        [{"phone": "+1", "name": "Mike", "service_type": "AC install"}],
    )
    job = session.exec(select(RecoveryJob)).first()
    job.current_status = "awaiting_slot"
    job.offered_slots_json = json.dumps(["Monday morning", "Tuesday afternoon", "Wednesday morning"])
    session.add(job)
    session.commit()

    monkeypatch.setattr(
        recovery_service,
        "agent",
        StubAgent({
            "reply": "",
            "jobs": [],
            "new_messages": [],
            "pending_tool_call": {"name": "confirm_slot", "input": {"slot_index": 1}},
        }),
    )

    reply = recovery_service.handle_recovery_reply(session, client, job, "Tuesday afternoon works")

    session.refresh(job)
    assert job.current_status == "booked"
    assert job.booked_job_id is not None
    booked = session.get(Job, job.booked_job_id)
    assert booked.service_type == "AC install"
    assert "Tuesday afternoon" in reply


def test_handle_recovery_reply_declined_stops_sequence(session, monkeypatch):
    client = make_client(session)
    recovery_service.create_campaign(
        session, client, "reactivation", "Dormant", [{"phone": "+1", "name": "Sue", "service_type": "Tune-up"}],
    )
    job = session.exec(select(RecoveryJob)).first()

    monkeypatch.setattr(
        recovery_service,
        "agent",
        StubAgent({
            "reply": "",
            "jobs": [],
            "new_messages": [],
            "pending_tool_call": {"name": "record_response", "input": {"intent": "not_interested"}},
        }),
    )

    recovery_service.handle_recovery_reply(session, client, job, "No thanks")

    session.refresh(job)
    assert job.current_status == "declined"


def test_find_active_recovery_job_only_matches_active_statuses(session):
    client = make_client(session)
    recovery_service.create_campaign(
        session, client, "quote", "June", [{"phone": "+1", "name": "Mike", "service_type": "AC install"}],
    )
    job = session.exec(select(RecoveryJob)).first()
    job.last_sent_day = 1  # simulate the first sequence message having gone out
    session.add(job)
    session.commit()

    found = recovery_service.find_active_recovery_job(session, client.id, "+1")
    assert found is not None

    job.current_status = "booked"
    session.add(job)
    session.commit()

    assert recovery_service.find_active_recovery_job(session, client.id, "+1") is None


def test_find_active_recovery_job_excludes_jobs_never_sent_to(session):
    client = make_client(session)
    recovery_service.create_campaign(
        session, client, "quote", "June", [{"phone": "+1", "name": "Mike", "service_type": "AC install"}],
    )
    job = session.exec(select(RecoveryJob)).first()

    assert job.last_sent_day is None
    assert job.current_status == "pending"
    assert recovery_service.find_active_recovery_job(session, client.id, "+1") is None


def test_handle_recovery_reply_out_of_range_slot_index_does_not_book(session, monkeypatch):
    client = make_client(session)
    recovery_service.create_campaign(
        session, client, "quote", "June quotes",
        [{"phone": "+1", "name": "Mike", "service_type": "AC install"}],
    )
    job = session.exec(select(RecoveryJob)).first()
    job.current_status = "awaiting_slot"
    job.offered_slots_json = json.dumps(["Monday morning", "Tuesday afternoon", "Wednesday morning"])
    session.add(job)
    session.commit()

    monkeypatch.setattr(
        recovery_service,
        "agent",
        StubAgent({
            "reply": "",
            "jobs": [],
            "new_messages": [],
            "pending_tool_call": {"name": "confirm_slot", "input": {"slot_index": 99}},
        }),
    )

    recovery_service.handle_recovery_reply(session, client, job, "uh, the fourth one?")

    session.refresh(job)
    assert job.current_status == "awaiting_slot"
    assert job.booked_job_id is None


def test_handle_recovery_reply_awaiting_slot_no_tool_call_does_not_crash(session, monkeypatch):
    client = make_client(session)
    recovery_service.create_campaign(
        session, client, "quote", "June quotes",
        [{"phone": "+1", "name": "Mike", "service_type": "AC install"}],
    )
    job = session.exec(select(RecoveryJob)).first()
    job.current_status = "awaiting_slot"
    job.offered_slots_json = json.dumps(["Monday morning", "Tuesday afternoon", "Wednesday morning"])
    session.add(job)
    session.commit()

    monkeypatch.setattr(
        recovery_service,
        "agent",
        StubAgent({
            "reply": "Sorry, which day did you mean?",
            "jobs": [],
            "new_messages": [],
            "pending_tool_call": None,
        }),
    )

    reply = recovery_service.handle_recovery_reply(session, client, job, "hmm not sure")

    session.refresh(job)
    assert job.current_status == "awaiting_slot"
    assert job.booked_job_id is None
    assert reply == "Sorry, which day did you mean?"
