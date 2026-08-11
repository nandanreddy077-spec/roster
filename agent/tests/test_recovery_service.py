import json
from datetime import datetime, timedelta

from sqlmodel import Session, select

from db_models import (
    Business,
    Job,
    OwnerNotification,
    RecoveryCampaign,
    RecoveryJob,
    RecoveryMessageLog,
)
import recovery_service
from conftest import StubAgent
from deployment import deploy_role


def make_client(session: Session) -> Business:
    client = Business(
        business_name="Test Co",
        trade="HVAC",
        services_json=json.dumps(["AC repair"]),
        hours="9-5",
        pricing_faq="n/a",
        escalation_phone="+15550000000",
        inbound_number="+15559990000",
    )
    session.add(client)
    session.commit()
    session.refresh(client)
    # Quote Chaser's auto-enrolment is gated on the Employee row like every
    # other tick worker (see test_tick_deployment_gate.py): these tests'
    # premise is a business that HAS it, which now means hired rather than
    # merely existing. Founder-created campaigns are unaffected either way.
    deploy_role(session, client.id, "quote_chaser")
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
        session,
        client,
        "quote",
        "June quotes",
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
        session,
        client,
        "quote",
        "June quotes",
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
        session,
        client,
        "quote",
        "June quotes",
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
        session,
        client,
        "quote",
        "June quotes",
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
        session,
        client,
        "quote",
        "June quotes",
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

    session.add(
        RecoveryMessageLog(recovery_job_id=job.id, message_day=1, message_text="Hi Mike...")
    )
    session.commit()

    reply = recovery_service.handle_recovery_reply(session, client, job, "STOP")

    session.refresh(job)
    assert job.current_status == "declined"
    log = session.exec(
        select(RecoveryMessageLog).where(RecoveryMessageLog.recovery_job_id == job.id)
    ).first()
    assert log.customer_reply == "STOP"  # opt-out text is captured, not silently dropped
    assert "unsubscribed" in reply.lower()


def test_handle_recovery_reply_awaiting_slot_can_decline(session, monkeypatch):
    client = make_client(session)
    recovery_service.create_campaign(
        session,
        client,
        "quote",
        "June quotes",
        [{"phone": "+1", "name": "Mike", "service_type": "AC install"}],
    )
    job = session.exec(select(RecoveryJob)).first()
    job.current_status = "awaiting_slot"
    job.offered_slots_json = json.dumps(
        ["Monday morning", "Tuesday afternoon", "Wednesday morning"]
    )
    session.add(job)
    session.commit()

    monkeypatch.setattr(
        recovery_service,
        "agent",
        StubAgent(
            {
                "reply": "",
                "jobs": [],
                "new_messages": [],
                "pending_tool_call": {
                    "name": "record_response",
                    "input": {"intent": "not_interested"},
                },
            }
        ),
    )

    recovery_service.handle_recovery_reply(
        session, client, job, "actually never mind, don't text me again"
    )

    session.refresh(job)
    assert job.current_status == "declined"
    assert job.booked_job_id is None


def test_handle_recovery_reply_interested_offers_slots(session, monkeypatch):
    client = make_client(session)
    recovery_service.create_campaign(
        session,
        client,
        "quote",
        "June quotes",
        [{"phone": "+1", "name": "Mike", "service_type": "AC install"}],
    )
    job = session.exec(select(RecoveryJob)).first()
    job.last_sent_day = 1
    session.add(job)
    session.commit()
    session.add(
        RecoveryMessageLog(recovery_job_id=job.id, message_day=1, message_text="Hi Mike...")
    )
    session.commit()

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

    reply = recovery_service.handle_recovery_reply(session, client, job, "Yes I'm interested!")

    session.refresh(job)
    assert job.current_status == "awaiting_slot"
    assert len(job.offered_slots) == 3
    assert "1)" in reply


def test_handle_recovery_reply_confirm_slot_books_job(session, monkeypatch):
    client = make_client(session)
    recovery_service.create_campaign(
        session,
        client,
        "quote",
        "June quotes",
        [{"phone": "+1", "name": "Mike", "service_type": "AC install"}],
    )
    job = session.exec(select(RecoveryJob)).first()
    job.current_status = "awaiting_slot"
    job.offered_slots_json = json.dumps(
        ["Monday morning", "Tuesday afternoon", "Wednesday morning"]
    )
    session.add(job)
    session.commit()

    monkeypatch.setattr(
        recovery_service,
        "agent",
        StubAgent(
            {
                "reply": "",
                "jobs": [],
                "new_messages": [],
                "pending_tool_call": {"name": "confirm_slot", "input": {"slot_index": 1}},
            }
        ),
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
        session,
        client,
        "reactivation",
        "Dormant",
        [{"phone": "+1", "name": "Sue", "service_type": "Tune-up"}],
    )
    job = session.exec(select(RecoveryJob)).first()

    monkeypatch.setattr(
        recovery_service,
        "agent",
        StubAgent(
            {
                "reply": "",
                "jobs": [],
                "new_messages": [],
                "pending_tool_call": {
                    "name": "record_response",
                    "input": {"intent": "not_interested"},
                },
            }
        ),
    )

    recovery_service.handle_recovery_reply(session, client, job, "No thanks")

    session.refresh(job)
    assert job.current_status == "declined"


def test_find_active_recovery_job_only_matches_active_statuses(session):
    client = make_client(session)
    recovery_service.create_campaign(
        session,
        client,
        "quote",
        "June",
        [{"phone": "+1", "name": "Mike", "service_type": "AC install"}],
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
        session,
        client,
        "quote",
        "June",
        [{"phone": "+1", "name": "Mike", "service_type": "AC install"}],
    )
    job = session.exec(select(RecoveryJob)).first()

    assert job.last_sent_day is None
    assert job.current_status == "pending"
    assert recovery_service.find_active_recovery_job(session, client.id, "+1") is None


def test_handle_recovery_reply_out_of_range_slot_index_does_not_book(session, monkeypatch):
    client = make_client(session)
    recovery_service.create_campaign(
        session,
        client,
        "quote",
        "June quotes",
        [{"phone": "+1", "name": "Mike", "service_type": "AC install"}],
    )
    job = session.exec(select(RecoveryJob)).first()
    job.current_status = "awaiting_slot"
    job.offered_slots_json = json.dumps(
        ["Monday morning", "Tuesday afternoon", "Wednesday morning"]
    )
    session.add(job)
    session.commit()

    monkeypatch.setattr(
        recovery_service,
        "agent",
        StubAgent(
            {
                "reply": "",
                "jobs": [],
                "new_messages": [],
                "pending_tool_call": {"name": "confirm_slot", "input": {"slot_index": 99}},
            }
        ),
    )

    recovery_service.handle_recovery_reply(session, client, job, "uh, the fourth one?")

    session.refresh(job)
    assert job.current_status == "awaiting_slot"
    assert job.booked_job_id is None


def test_handle_recovery_reply_awaiting_slot_no_tool_call_does_not_crash(session, monkeypatch):
    client = make_client(session)
    recovery_service.create_campaign(
        session,
        client,
        "quote",
        "June quotes",
        [{"phone": "+1", "name": "Mike", "service_type": "AC install"}],
    )
    job = session.exec(select(RecoveryJob)).first()
    job.current_status = "awaiting_slot"
    job.offered_slots_json = json.dumps(
        ["Monday morning", "Tuesday afternoon", "Wednesday morning"]
    )
    session.add(job)
    session.commit()

    monkeypatch.setattr(
        recovery_service,
        "agent",
        StubAgent(
            {
                "reply": "Sorry, which day did you mean?",
                "jobs": [],
                "new_messages": [],
                "pending_tool_call": None,
            }
        ),
    )

    reply = recovery_service.handle_recovery_reply(session, client, job, "hmm not sure")

    session.refresh(job)
    assert job.current_status == "awaiting_slot"
    assert job.booked_job_id is None
    assert reply == "Sorry, which day did you mean?"


def test_create_campaign_membership_sets_anchor_date(session):
    client = make_client(session)
    customers = [
        {"phone": "+1", "name": "Sarah", "service_type": "AC tune-up", "anchor_date": "2026-07-15"},
    ]

    campaign = recovery_service.create_campaign(
        session, client, "membership", "July renewals", customers
    )

    job = session.exec(select(RecoveryJob).where(RecoveryJob.campaign_id == campaign.id)).first()
    assert job.anchor_date == "2026-07-15"


def test_create_campaign_quote_leaves_anchor_date_none(session):
    client = make_client(session)
    customers = [
        {"phone": "+1", "name": "Mike", "service_type": "AC install", "estimate_amount": "8000"}
    ]

    campaign = recovery_service.create_campaign(session, client, "quote", "June quotes", customers)

    job = session.exec(select(RecoveryJob).where(RecoveryJob.campaign_id == campaign.id)).first()
    assert job.anchor_date is None


def test_tick_sends_membership_offset_before_renewal(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(recovery_service, "sms_channel", fake_channel)

    client = make_client(session)
    renewal = (datetime.utcnow() + timedelta(days=25)).strftime("%Y-%m-%d")
    campaign = recovery_service.create_campaign(
        session,
        client,
        "membership",
        "July renewals",
        [{"phone": "+1", "name": "Sarah", "service_type": "AC tune-up", "anchor_date": renewal}],
    )

    sent = recovery_service.tick(session)

    assert len(sent) == 1
    assert fake_channel.sent[0]["to"] == "+1"
    assert "Sarah" in fake_channel.sent[0]["body"]
    job = session.exec(select(RecoveryJob).where(RecoveryJob.campaign_id == campaign.id)).first()
    assert job.last_sent_day == -30


def test_tick_membership_catch_up_lands_on_latest_offset_not_burst(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(recovery_service, "sms_channel", fake_channel)

    client = make_client(session)
    renewal = (datetime.utcnow() + timedelta(days=5)).strftime("%Y-%m-%d")  # added late, 5 days out
    recovery_service.create_campaign(
        session,
        client,
        "membership",
        "July renewals",
        [{"phone": "+1", "name": "Sarah", "service_type": "AC tune-up", "anchor_date": renewal}],
    )

    sent = recovery_service.tick(session)

    assert len(sent) == 1
    job = session.exec(select(RecoveryJob)).first()
    assert job.last_sent_day == -7  # jumps straight to -7, doesn't replay -30/-14 as a burst


def test_tick_membership_does_not_resend_same_offset_twice(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(recovery_service, "sms_channel", fake_channel)

    client = make_client(session)
    renewal = (datetime.utcnow() + timedelta(days=25)).strftime("%Y-%m-%d")
    recovery_service.create_campaign(
        session,
        client,
        "membership",
        "July renewals",
        [{"phone": "+1", "name": "Sarah", "service_type": "AC tune-up", "anchor_date": renewal}],
    )

    recovery_service.tick(session)
    second = recovery_service.tick(session)

    assert second == []
    assert len(fake_channel.sent) == 1


def test_tick_membership_marks_no_response_after_final_offset(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(recovery_service, "sms_channel", fake_channel)

    client = make_client(session)
    renewal = (datetime.utcnow() - timedelta(days=10)).strftime("%Y-%m-%d")  # renewed 10 days ago
    recovery_service.create_campaign(
        session,
        client,
        "membership",
        "July renewals",
        [{"phone": "+1", "name": "Sarah", "service_type": "AC tune-up", "anchor_date": renewal}],
    )

    recovery_service.tick(session)  # catches up: sends the one unsent due offset (+7)
    recovery_service.tick(session)  # nothing left to send -> marks no_response

    job = session.exec(select(RecoveryJob)).first()
    assert job.current_status == "no_response"


def test_tick_quote_face_unaffected_by_membership_branch(session, monkeypatch):
    """Regression guard: byte-identical behavior for the pre-existing faces."""
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(recovery_service, "sms_channel", fake_channel)

    client = make_client(session)
    campaign = recovery_service.create_campaign(
        session,
        client,
        "quote",
        "June quotes",
        [{"phone": "+1", "name": "Mike", "service_type": "AC install", "estimate_amount": "8000"}],
    )
    campaign.started_at = datetime.utcnow() - timedelta(days=1)
    session.add(campaign)
    session.commit()

    sent = recovery_service.tick(session)

    assert len(sent) == 1
    job = session.exec(select(RecoveryJob).where(RecoveryJob.campaign_id == campaign.id)).first()
    assert job.last_sent_day == 1
    assert job.anchor_date is None


class ReentrantTickChannel:
    """Simulates a second recovery tick running concurrently at the exact
    moment the first tick is mid-send — the classic overlapping-cron race."""

    def __init__(self, engine):
        self.engine = engine
        self.sent = []
        self._reentered = False

    def send(self, from_number, to_number, body):
        self.sent.append({"to": to_number, "body": body})
        if not self._reentered:
            self._reentered = True
            from sqlmodel import Session as _S

            with _S(self.engine) as inner:
                recovery_service.tick(inner)


def test_overlapping_ticks_cannot_double_text_a_customer(test_engine, monkeypatch):
    from sqlmodel import Session as _S

    channel = ReentrantTickChannel(test_engine)
    monkeypatch.setattr(recovery_service, "sms_channel", channel)

    with _S(test_engine) as session:
        client = make_client(session)
        campaign = recovery_service.create_campaign(
            session,
            client,
            "quote",
            "June quotes",
            [{"phone": "+15550001111", "name": "Pat", "service_type": "AC install"}],
        )
        campaign.started_at = datetime.utcnow() - timedelta(days=1)
        session.add(campaign)
        session.commit()

    with _S(test_engine) as session:
        recovery_service.tick(session)

    texts_to_customer = [s for s in channel.sent if s["to"] == "+15550001111"]
    assert len(texts_to_customer) == 1, (
        f"customer must get day-1 message exactly once, got {len(texts_to_customer)}"
    )


# ---- PR #1: automatic enrollment of completed estimates ---------------------
# Job.completed_at (not a new "requested" timestamp) is the timing anchor —
# an estimate can only be chased once it's actually been given, which is what
# "Mark done" represents. Job.is_estimate is the structured "what kind of job
# was this" flag. Each enrollment gets its OWN RecoveryCampaign: tick()'s
# elapsed-time math for the quote/reactivation faces reads campaign.started_at,
# not per-job creation time, so sharing one long-lived campaign across leads
# enrolled at different times would make a late-enrolled lead's "elapsed days"
# jump straight to the campaign's true age — potentially firing the day-28
# "last chance" message on a lead's very first tick. One campaign per job
# keeps that math correct with zero changes to the frozen tick() engine.


def _completed_estimate_job(session, client, **overrides) -> Job:
    defaults = dict(
        business_id=client.id,
        service_type="AC replacement",
        urgency="routine",
        customer_name="Mike",
        callback_number="+15551234567",
        is_estimate=True,
        completed_at=datetime.utcnow(),
    )
    defaults.update(overrides)
    job = Job(**defaults)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def test_enroll_completed_estimates_creates_a_recovery_job(session):
    client = make_client(session)
    job = _completed_estimate_job(session, client)

    enrolled = recovery_service.enroll_completed_estimates(session)

    assert len(enrolled) == 1
    rj = enrolled[0]
    assert rj.source_job_id == job.id
    assert rj.business_id == client.id
    assert rj.customer_phone == "+15551234567"
    assert rj.customer_name == "Mike"
    assert rj.service_type == "AC replacement"
    assert rj.current_status == "pending"
    campaign = session.get(RecoveryCampaign, rj.campaign_id)
    assert campaign.face == "quote"


def test_enroll_completed_estimates_ignores_non_estimate_jobs(session):
    client = make_client(session)
    _completed_estimate_job(session, client, is_estimate=False)

    assert recovery_service.enroll_completed_estimates(session) == []


def test_enroll_completed_estimates_ignores_incomplete_jobs(session):
    client = make_client(session)
    _completed_estimate_job(session, client, completed_at=None)

    assert recovery_service.enroll_completed_estimates(session) == []


def test_enroll_completed_estimates_skips_jobs_without_callback_number(session):
    client = make_client(session)
    _completed_estimate_job(session, client, callback_number=None)

    assert recovery_service.enroll_completed_estimates(session) == []


def test_enroll_completed_estimates_is_idempotent(session):
    """Duplicate-enrollment protection: a job already linked to a RecoveryJob
    (via source_job_id) must never be enrolled a second time, matching
    find_active_review_ask/find_active_referral_ask's own precedent — the
    tick can safely run more than once a day."""
    client = make_client(session)
    _completed_estimate_job(session, client)

    first = recovery_service.enroll_completed_estimates(session)
    second = recovery_service.enroll_completed_estimates(session)

    assert len(first) == 1
    assert second == []
    all_recovery_jobs = session.exec(select(RecoveryJob)).all()
    assert len(all_recovery_jobs) == 1


def test_enroll_completed_estimates_business_isolation(session):
    client_a = make_client(session)
    client_b = Business(
        business_name="Other Co",
        trade="HVAC",
        hours="9-5",
        pricing_faq="n/a",
        escalation_phone="+15550009999",
        inbound_number="+15559991111",
    )
    session.add(client_b)
    session.commit()
    session.refresh(client_b)
    deploy_role(session, client_b.id, "quote_chaser")

    job_a = _completed_estimate_job(session, client_a, callback_number="+1")
    job_b = _completed_estimate_job(session, client_b, callback_number="+2")

    enrolled = recovery_service.enroll_completed_estimates(session)

    by_source = {rj.source_job_id: rj for rj in enrolled}
    assert len(enrolled) == 2
    assert by_source[job_a.id].business_id == client_a.id
    assert by_source[job_b.id].business_id == client_b.id


def test_enroll_completed_estimates_gives_each_job_its_own_campaign(session):
    """Regression guard for the shared-campaign elapsed-time bug described
    above: two estimates completed at different times must never land in the
    same RecoveryCampaign."""
    client = make_client(session)
    job1 = _completed_estimate_job(session, client, callback_number="+1")
    job2 = _completed_estimate_job(session, client, callback_number="+2")

    enrolled = recovery_service.enroll_completed_estimates(session)

    campaign_ids = {rj.campaign_id for rj in enrolled}
    assert len(campaign_ids) == 2


def test_enroll_completed_estimates_then_tick_sends_after_the_sequence_delay(session, monkeypatch):
    """Integration proof that an auto-enrolled lead flows correctly into the
    existing, unmodified tick() engine — the real point of this PR."""
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(recovery_service, "sms_channel", fake_channel)
    client = make_client(session)
    _completed_estimate_job(session, client)

    enrolled = recovery_service.enroll_completed_estimates(session)
    campaign = session.get(RecoveryCampaign, enrolled[0].campaign_id)
    campaign.started_at = datetime.utcnow() - timedelta(days=1)
    session.add(campaign)
    session.commit()

    sent = recovery_service.tick(session)

    assert len(sent) == 1
    assert fake_channel.sent[0]["to"] == "+15551234567"
    assert "AC replacement" in fake_channel.sent[0]["body"]


# ---- PR #2: escalation -------------------------------------------------------
# Negotiation, pricing exceptions, scheduling-change requests, and complaints
# get handed to the owner rather than handled as an automated sales response —
# the model has no tool to negotiate, promise a discount, or confirm a
# reschedule, only escalate_to_owner. Escalating moves the job to a new
# terminal-ish "escalated" status: excluded from tick()'s "pending" query (so
# the automated sequence stops immediately, matching "never spam") and from
# ACTIVE_STATUSES (so find_active_recovery_job stops routing this customer's
# further replies into the rigid Quote Chaser flow — a later text falls
# through to Frontdesk's general handling instead, the same "capture outcome,
# then let general handling take over" shape Reviews' negative-outcome path
# already uses).


def _stub_escalation(reason: str = "asked for a discount") -> StubAgent:
    return StubAgent(
        {
            "reply": "",
            "jobs": [],
            "new_messages": [],
            "pending_tool_call": {"name": "escalate_to_owner", "input": {"reason": reason}},
        }
    )


def test_handle_recovery_reply_escalates_on_price_negotiation(session, monkeypatch):
    client = make_client(session)
    recovery_service.create_campaign(
        session,
        client,
        "quote",
        "June quotes",
        [{"phone": "+1", "name": "Mike", "service_type": "AC install"}],
    )
    job = session.exec(select(RecoveryJob)).first()
    job.last_sent_day = 1
    session.add(job)
    session.commit()
    monkeypatch.setattr(recovery_service, "agent", _stub_escalation("wants 10% off the quote"))
    calls = []
    monkeypatch.setattr(
        recovery_service,
        "notify_owner_of_escalation",
        lambda business, caller, reason, **k: calls.append((caller, reason)) or True,
    )

    reply = recovery_service.handle_recovery_reply(
        session, client, job, "can you do any better on price?"
    )

    session.refresh(job)
    assert job.current_status == "escalated"
    assert job.escalation_reason == "wants 10% off the quote"
    assert len(calls) == 1
    assert calls[0][0] == "+1"
    assert "10% off" in calls[0][1]
    assert "team" in reply.lower() or "reach out" in reply.lower()

    notifications = session.exec(
        select(OwnerNotification).where(OwnerNotification.business_id == client.id)
    ).all()
    assert len(notifications) == 1
    assert notifications[0].kind == "escalation"
    assert notifications[0].source == "recovery_escalation"


def test_handle_recovery_reply_escalates_from_awaiting_slot_state(session, monkeypatch):
    """A reschedule request mid-slot-confirmation must escalate too, not just
    the first reply."""
    client = make_client(session)
    recovery_service.create_campaign(
        session,
        client,
        "quote",
        "June quotes",
        [{"phone": "+1", "name": "Mike", "service_type": "AC install"}],
    )
    job = session.exec(select(RecoveryJob)).first()
    job.current_status = "awaiting_slot"
    job.offered_slots_json = json.dumps(["Monday morning", "Tuesday afternoon"])
    session.add(job)
    session.commit()
    monkeypatch.setattr(
        recovery_service,
        "agent",
        _stub_escalation("none of those times work, needs a different week"),
    )
    calls = []
    monkeypatch.setattr(
        recovery_service,
        "notify_owner_of_escalation",
        lambda business, caller, reason, **k: calls.append(1) or True,
    )

    recovery_service.handle_recovery_reply(
        session, client, job, "none of those work, can we do next month instead?"
    )

    session.refresh(job)
    assert job.current_status == "escalated"
    assert len(calls) == 1


def test_handle_recovery_reply_escalation_stops_future_sequence_messages(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(recovery_service, "sms_channel", fake_channel)
    client = make_client(session)
    campaign = recovery_service.create_campaign(
        session,
        client,
        "quote",
        "June quotes",
        [{"phone": "+1", "name": "Mike", "service_type": "AC install"}],
    )
    campaign.started_at = datetime.utcnow() - timedelta(days=1)
    session.add(campaign)
    job = session.exec(select(RecoveryJob)).first()
    job.last_sent_day = 1
    session.add(job)
    session.commit()
    monkeypatch.setattr(recovery_service, "agent", _stub_escalation())
    monkeypatch.setattr(recovery_service, "notify_owner_of_escalation", lambda *a, **k: True)

    recovery_service.handle_recovery_reply(session, client, job, "can you knock the price down?")
    session.refresh(campaign)
    campaign.started_at = datetime.utcnow() - timedelta(days=8)  # day-3 threshold now due
    session.add(campaign)
    session.commit()

    sent = recovery_service.tick(session)

    assert sent == [], "an escalated job must never receive another automated sequence message"


def test_find_active_recovery_job_excludes_escalated_jobs(session, monkeypatch):
    client = make_client(session)
    recovery_service.create_campaign(
        session,
        client,
        "quote",
        "June quotes",
        [{"phone": "+1", "name": "Mike", "service_type": "AC install"}],
    )
    job = session.exec(select(RecoveryJob)).first()
    job.last_sent_day = 1
    session.add(job)
    session.commit()
    monkeypatch.setattr(recovery_service, "agent", _stub_escalation())
    monkeypatch.setattr(recovery_service, "notify_owner_of_escalation", lambda *a, **k: True)

    recovery_service.handle_recovery_reply(session, client, job, "can you knock the price down?")

    assert recovery_service.find_active_recovery_job(session, client.id, "+1") is None


def test_handle_recovery_reply_normal_interest_does_not_escalate(session, monkeypatch):
    """Regression: an ordinary 'yes, book me' reply must never page the
    owner."""
    client = make_client(session)
    recovery_service.create_campaign(
        session,
        client,
        "quote",
        "June quotes",
        [{"phone": "+1", "name": "Mike", "service_type": "AC install"}],
    )
    job = session.exec(select(RecoveryJob)).first()
    job.last_sent_day = 1
    session.add(job)
    session.commit()
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
    calls = []
    monkeypatch.setattr(
        recovery_service, "notify_owner_of_escalation", lambda *a, **k: calls.append(1) or True
    )

    recovery_service.handle_recovery_reply(session, client, job, "yes, sign me up")

    session.refresh(job)
    assert job.current_status == "awaiting_slot"
    assert calls == []
    assert job.escalation_reason is None


# ---- PR #3: booking notification parity --------------------------------------
# confirm_slot's booking branch already called bookings.book_job, but unlike
# every other booking path (service.py's SMS flow) it never told the owner —
# a real bug, not a missing feature: notify_owner_of_booking already accepts
# an employee_name specifically so other booking employees could plug in.


def test_handle_recovery_reply_confirm_slot_notifies_the_owner(session, monkeypatch):
    client = make_client(session)
    recovery_service.create_campaign(
        session,
        client,
        "quote",
        "June quotes",
        [{"phone": "+15551234567", "name": "Mike", "service_type": "AC install"}],
    )
    job = session.exec(select(RecoveryJob)).first()
    job.current_status = "awaiting_slot"
    job.offered_slots_json = json.dumps(["Monday morning", "Tuesday afternoon"])
    session.add(job)
    session.commit()
    monkeypatch.setattr(
        recovery_service,
        "agent",
        StubAgent(
            {
                "reply": "",
                "jobs": [],
                "new_messages": [],
                "pending_tool_call": {"name": "confirm_slot", "input": {"slot_index": 1}},
            }
        ),
    )
    calls = []
    monkeypatch.setattr(
        recovery_service,
        "notify_owner_of_booking",
        lambda business, job, employee_name="Frontdesk", **k: calls.append(employee_name) or True,
    )

    recovery_service.handle_recovery_reply(session, client, job, "Tuesday afternoon works")

    assert calls == ["Quote Chaser"]
    notifications = session.exec(
        select(OwnerNotification).where(OwnerNotification.business_id == client.id)
    ).all()
    assert len(notifications) == 1
    assert notifications[0].kind == "job_booked"
    assert notifications[0].source == "recovery_booking"


def test_handle_recovery_reply_out_of_range_slot_does_not_notify_owner(session, monkeypatch):
    """Regression: no booking happened, so no notification should fire."""
    client = make_client(session)
    recovery_service.create_campaign(
        session,
        client,
        "quote",
        "June quotes",
        [{"phone": "+1", "name": "Mike", "service_type": "AC install"}],
    )
    job = session.exec(select(RecoveryJob)).first()
    job.current_status = "awaiting_slot"
    job.offered_slots_json = json.dumps(["Monday morning", "Tuesday afternoon"])
    session.add(job)
    session.commit()
    monkeypatch.setattr(
        recovery_service,
        "agent",
        StubAgent(
            {
                "reply": "",
                "jobs": [],
                "new_messages": [],
                "pending_tool_call": {"name": "confirm_slot", "input": {"slot_index": 99}},
            }
        ),
    )
    calls = []
    monkeypatch.setattr(
        recovery_service, "notify_owner_of_booking", lambda *a, **k: calls.append(1) or True
    )

    recovery_service.handle_recovery_reply(session, client, job, "the third one")

    assert calls == []
    assert session.exec(select(OwnerNotification)).all() == []
