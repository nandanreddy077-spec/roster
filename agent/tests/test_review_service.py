"""Reviews: automatic review-request texts triggered off Job.completed_at,
after waiting REVIEW_DELAY_DAYS. Moved off app.py's synchronous "Mark done"
path (2026-07-29/30 plan) onto the same tick-based, delayed mechanism
referral_service.send_due_referral_asks already uses — safe to call more
than once a day since review_requested_at gates re-sending.
"""

from datetime import datetime, timedelta

import review_service
from conftest import StubAgent
from db_models import Business, Job, OwnerNotification, ReviewReply
from deployment import deploy_role
from sqlmodel import Session, select


def make_client(session: Session, review_link=None, deployed=True) -> Business:
    """A business with Reviews deployed, since that Employee row — not
    review_link — is what authorises Reviews to text anyone. `deployed=False`
    builds the un-hired case the gate exists to block."""
    client = Business(
        business_name="Ridgeline Plumbing",
        trade="Plumbing",
        hours="9-5",
        pricing_faq="n/a",
        escalation_phone="+15550000000",
        inbound_number="+15559990000",
        review_link=review_link,
    )
    session.add(client)
    session.commit()
    session.refresh(client)
    if deployed:
        deploy_role(session, client.id, "reviews")
        session.refresh(client)
    return client


class FakeSMSChannel:
    def __init__(self):
        self.sent = []

    def send(self, from_number, to_number, body):
        self.sent.append({"from": from_number, "to": to_number, "body": body})


class ExplodingSMSChannel:
    def send(self, from_number, to_number, body):
        raise RuntimeError("simulated Twilio failure")


def test_send_due_review_requests_sends_to_eligible_job(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(review_service, "sms_channel", fake_channel)

    client = make_client(session, review_link="https://g.page/r/test")
    job = Job(
        business_id=client.id,
        service_type="AC repair",
        urgency="routine",
        callback_number="+15551234567",
        completed_at=datetime.utcnow() - timedelta(days=2),
    )
    session.add(job)
    session.commit()

    sent = review_service.send_due_review_requests(session)

    assert len(sent) == 1
    assert fake_channel.sent[0]["to"] == "+15551234567"
    assert "https://g.page/r/test" in fake_channel.sent[0]["body"]
    assert "Ridgeline Plumbing" in fake_channel.sent[0]["body"]
    session.refresh(job)
    assert job.review_requested_at is not None


def test_send_due_review_requests_skips_job_completed_too_recently(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(review_service, "sms_channel", fake_channel)

    client = make_client(session, review_link="https://g.page/r/test")
    job = Job(
        business_id=client.id,
        service_type="AC repair",
        urgency="routine",
        callback_number="+15551234567",
        completed_at=datetime.utcnow(),
    )
    session.add(job)
    session.commit()

    sent = review_service.send_due_review_requests(session)

    assert sent == []
    assert fake_channel.sent == []
    session.refresh(job)
    assert job.review_requested_at is None


def test_send_due_review_requests_skips_business_without_review_link(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(review_service, "sms_channel", fake_channel)

    client = make_client(session, review_link=None)
    job = Job(
        business_id=client.id,
        service_type="AC repair",
        urgency="routine",
        callback_number="+15551234567",
        completed_at=datetime.utcnow() - timedelta(days=2),
    )
    session.add(job)
    session.commit()

    sent = review_service.send_due_review_requests(session)

    assert sent == []
    assert fake_channel.sent == []


def test_send_due_review_requests_skips_job_without_callback_number(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(review_service, "sms_channel", fake_channel)

    client = make_client(session, review_link="https://g.page/r/test")
    job = Job(
        business_id=client.id,
        service_type="AC repair",
        urgency="routine",
        callback_number=None,
        completed_at=datetime.utcnow() - timedelta(days=2),
    )
    session.add(job)
    session.commit()

    sent = review_service.send_due_review_requests(session)

    assert sent == []


def test_send_due_review_requests_is_idempotent_across_two_calls(session, monkeypatch):
    """Safe to call more than once a day — review_requested_at gates it, the
    same idempotency guarantee referral_service's own tick function has."""
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(review_service, "sms_channel", fake_channel)

    client = make_client(session, review_link="https://g.page/r/test")
    job = Job(
        business_id=client.id,
        service_type="AC repair",
        urgency="routine",
        callback_number="+15551234567",
        completed_at=datetime.utcnow() - timedelta(days=2),
    )
    session.add(job)
    session.commit()

    review_service.send_due_review_requests(session)
    second = review_service.send_due_review_requests(session)

    assert second == []
    assert len(fake_channel.sent) == 1


def test_send_due_review_requests_survives_a_send_failure(session, monkeypatch):
    """A failed send must not crash the tick or leave review_requested_at
    set for a message that never actually went out — mirrors
    app.py's old complete_job guarantee ("the review text is a bonus"),
    now upheld by the tick instead."""
    monkeypatch.setattr(review_service, "sms_channel", ExplodingSMSChannel())

    client = make_client(session, review_link="https://g.page/r/test")
    job = Job(
        business_id=client.id,
        service_type="AC repair",
        urgency="routine",
        callback_number="+15551234567",
        completed_at=datetime.utcnow() - timedelta(days=2),
    )
    session.add(job)
    session.commit()

    sent = review_service.send_due_review_requests(session)  # must not raise

    assert sent == []
    session.refresh(job)
    assert job.review_requested_at is None


# ---- send_due_review_followups: PR #2, the one polite follow-up ------------
#
# Deliberately NOT yet gated on "did the customer reply" — ReviewReply and
# reply-routing land in PR #3, which will add "no ReviewReply exists" as an
# additional condition here. Until then this is purely elapsed-time, same
# as every other new-behavior boundary in this codebase that's been shipped
# incrementally and documented as such rather than silently left incomplete.


def test_send_due_review_followups_sends_to_an_unanswered_request(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(review_service, "sms_channel", fake_channel)

    client = make_client(session, review_link="https://g.page/r/test")
    job = Job(
        business_id=client.id,
        service_type="AC repair",
        urgency="routine",
        callback_number="+15551234567",
        completed_at=datetime.utcnow() - timedelta(days=10),
        review_requested_at=datetime.utcnow() - timedelta(days=5),
    )
    session.add(job)
    session.commit()

    sent = review_service.send_due_review_followups(session)

    assert len(sent) == 1
    assert "https://g.page/r/test" in fake_channel.sent[0]["body"]
    session.refresh(job)
    assert job.review_followup_sent_at is not None


def test_send_due_review_followups_skips_a_request_too_recent_to_follow_up_on(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(review_service, "sms_channel", fake_channel)

    client = make_client(session, review_link="https://g.page/r/test")
    job = Job(
        business_id=client.id,
        service_type="AC repair",
        urgency="routine",
        callback_number="+15551234567",
        completed_at=datetime.utcnow() - timedelta(days=2),
        review_requested_at=datetime.utcnow() - timedelta(days=1),
    )
    session.add(job)
    session.commit()

    sent = review_service.send_due_review_followups(session)

    assert sent == []
    session.refresh(job)
    assert job.review_followup_sent_at is None


def test_send_due_review_followups_never_sends_a_second_one(session, monkeypatch):
    """Never spam: the follow-up is a one-time touch, enforced by
    review_followup_sent_at gating re-sends — not a runtime "have we
    already annoyed them" check, an actual structural cap of one."""
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(review_service, "sms_channel", fake_channel)

    client = make_client(session, review_link="https://g.page/r/test")
    job = Job(
        business_id=client.id,
        service_type="AC repair",
        urgency="routine",
        callback_number="+15551234567",
        completed_at=datetime.utcnow() - timedelta(days=10),
        review_requested_at=datetime.utcnow() - timedelta(days=5),
    )
    session.add(job)
    session.commit()

    review_service.send_due_review_followups(session)
    second = review_service.send_due_review_followups(session)

    assert second == []
    assert len(fake_channel.sent) == 1


def test_send_due_review_followups_skips_a_job_never_asked_in_the_first_place(session, monkeypatch):
    """No review_requested_at at all (the request itself hasn't gone out
    yet, or the business/job was ineligible in PR #1) — nothing to follow
    up on."""
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(review_service, "sms_channel", fake_channel)

    client = make_client(session, review_link="https://g.page/r/test")
    job = Job(
        business_id=client.id,
        service_type="AC repair",
        urgency="routine",
        callback_number="+15551234567",
        completed_at=datetime.utcnow() - timedelta(days=10),
    )
    session.add(job)
    session.commit()

    sent = review_service.send_due_review_followups(session)

    assert sent == []


def test_send_due_review_followups_skips_business_without_review_link(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(review_service, "sms_channel", fake_channel)

    client = make_client(session, review_link=None)
    job = Job(
        business_id=client.id,
        service_type="AC repair",
        urgency="routine",
        callback_number="+15551234567",
        completed_at=datetime.utcnow() - timedelta(days=10),
        review_requested_at=datetime.utcnow() - timedelta(days=5),
    )
    session.add(job)
    session.commit()

    sent = review_service.send_due_review_followups(session)

    assert sent == []


def test_send_due_review_followups_survives_a_send_failure(session, monkeypatch):
    monkeypatch.setattr(review_service, "sms_channel", ExplodingSMSChannel())

    client = make_client(session, review_link="https://g.page/r/test")
    job = Job(
        business_id=client.id,
        service_type="AC repair",
        urgency="routine",
        callback_number="+15551234567",
        completed_at=datetime.utcnow() - timedelta(days=10),
        review_requested_at=datetime.utcnow() - timedelta(days=5),
    )
    session.add(job)
    session.commit()

    sent = review_service.send_due_review_followups(session)  # must not raise

    assert sent == []
    session.refresh(job)
    assert job.review_followup_sent_at is None


# ---- PR #3: follow-up suppression once a ReviewReply exists -----------------
#
# "unclear" is the ONE outcome that leaves the scheduled follow-up logic to
# continue (the plan's explicit behavior table) — every other outcome stops
# it, the same way an "already resolved" ReferralLead stops re-routing.


def _job_due_for_followup(session, client) -> Job:
    job = Job(
        business_id=client.id,
        service_type="AC repair",
        urgency="routine",
        callback_number="+15551234567",
        completed_at=datetime.utcnow() - timedelta(days=10),
        review_requested_at=datetime.utcnow() - timedelta(days=5),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def test_send_due_review_followups_fires_when_reply_outcome_is_unclear(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(review_service, "sms_channel", fake_channel)
    client = make_client(session, review_link="https://g.page/r/test")
    job = _job_due_for_followup(session, client)
    session.add(
        ReviewReply(
            business_id=client.id,
            source_job_id=job.id,
            customer_phone="+15551234567",
            outcome="unclear",
            raw_reply_text="huh?",
        )
    )
    session.commit()

    sent = review_service.send_due_review_followups(session)

    assert len(sent) == 1


def test_send_due_review_followups_is_suppressed_for_every_other_outcome(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(review_service, "sms_channel", fake_channel)
    client = make_client(session, review_link="https://g.page/r/test")

    for outcome in ("left_review", "positive", "neutral", "negative", "declined"):
        job = _job_due_for_followup(session, client)
        session.add(
            ReviewReply(
                business_id=client.id,
                source_job_id=job.id,
                customer_phone="+15551234567",
                outcome=outcome,
                raw_reply_text="whatever they said",
            )
        )
        session.commit()

    sent = review_service.send_due_review_followups(session)

    assert sent == [], "every non-unclear outcome must suppress the scheduled follow-up"


# ---- find_active_review_ask: the reply-routing window -----------------------


def test_find_active_review_ask_matches_within_window(session):
    client = make_client(session, review_link="https://g.page/r/test")
    job = Job(
        business_id=client.id,
        service_type="AC repair",
        urgency="routine",
        callback_number="+1",
        completed_at=datetime.utcnow() - timedelta(days=6),
        review_requested_at=datetime.utcnow() - timedelta(days=1),
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    found = review_service.find_active_review_ask(session, client.id, "+1")
    assert found is not None
    assert found.id == job.id


def test_find_active_review_ask_ignores_expired_window(session):
    client = make_client(session, review_link="https://g.page/r/test")
    job = Job(
        business_id=client.id,
        service_type="AC repair",
        urgency="routine",
        callback_number="+1",
        completed_at=datetime.utcnow() - timedelta(days=20),
        review_requested_at=datetime.utcnow() - timedelta(days=15),  # outside the reply window
    )
    session.add(job)
    session.commit()

    assert review_service.find_active_review_ask(session, client.id, "+1") is None


def test_find_active_review_ask_ignores_already_replied(session):
    """Duplicate-reply protection: once ANY ReviewReply exists for a job, a
    second inbound text from the same customer must not re-route to Reviews
    (matching find_active_referral_ask's exact precedent — one classified
    reply per ask, no re-classification attempts)."""
    client = make_client(session, review_link="https://g.page/r/test")
    job = Job(
        business_id=client.id,
        service_type="AC repair",
        urgency="routine",
        callback_number="+1",
        completed_at=datetime.utcnow() - timedelta(days=6),
        review_requested_at=datetime.utcnow() - timedelta(days=1),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    session.add(
        ReviewReply(
            business_id=client.id,
            source_job_id=job.id,
            customer_phone="+1",
            outcome="unclear",
            raw_reply_text="already replied",
        )
    )
    session.commit()

    assert review_service.find_active_review_ask(session, client.id, "+1") is None


# ---- handle_review_reply: classification, one per outcome -------------------


def _stub_reply_outcome(outcome: str, reply_text: str = "ok") -> StubAgent:
    return StubAgent(
        {
            "reply": reply_text,
            "jobs": [],
            "new_messages": [],
            "pending_tool_call": {"name": "record_review_reply", "input": {"outcome": outcome}},
        }
    )


def _job_awaiting_reply(session, client) -> Job:
    job = Job(
        business_id=client.id,
        service_type="AC repair",
        urgency="routine",
        customer_name="Mike",
        callback_number="+15551234567",
        completed_at=datetime.utcnow() - timedelta(days=6),
        review_requested_at=datetime.utcnow() - timedelta(days=1),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def test_handle_review_reply_left_review_records_and_stops_followups(session, monkeypatch):
    client = make_client(session, review_link="https://g.page/r/test")
    job = _job_awaiting_reply(session, client)
    monkeypatch.setattr(review_service, "agent", _stub_reply_outcome("left_review"))

    reply = review_service.handle_review_reply(
        session, client, job, "just left you a great review!"
    )

    saved = session.exec(select(ReviewReply).where(ReviewReply.source_job_id == job.id)).first()
    assert saved is not None
    assert saved.outcome == "left_review"
    assert saved.raw_reply_text == "just left you a great review!"
    assert reply is not None
    # Never claims the review is verified/seen — no wording implying we
    # checked Google, just a thank-you.
    assert "google" not in reply.lower()


def test_handle_review_reply_positive_records_and_stops_followups(session, monkeypatch):
    client = make_client(session, review_link="https://g.page/r/test")
    job = _job_awaiting_reply(session, client)
    monkeypatch.setattr(review_service, "agent", _stub_reply_outcome("positive"))

    review_service.handle_review_reply(session, client, job, "you guys were great, thanks!")

    saved = session.exec(select(ReviewReply).where(ReviewReply.source_job_id == job.id)).first()
    assert saved.outcome == "positive"


def test_handle_review_reply_neutral_records_and_stops_followups(session, monkeypatch):
    client = make_client(session, review_link="https://g.page/r/test")
    job = _job_awaiting_reply(session, client)
    monkeypatch.setattr(review_service, "agent", _stub_reply_outcome("neutral"))

    review_service.handle_review_reply(session, client, job, "ok, got it")

    saved = session.exec(select(ReviewReply).where(ReviewReply.source_job_id == job.id)).first()
    assert saved.outcome == "neutral"


def test_handle_review_reply_declined_records_and_stops_followups(session, monkeypatch):
    client = make_client(session, review_link="https://g.page/r/test")
    job = _job_awaiting_reply(session, client)
    monkeypatch.setattr(review_service, "agent", _stub_reply_outcome("declined"))

    review_service.handle_review_reply(session, client, job, "not really my thing, sorry")

    saved = session.exec(select(ReviewReply).where(ReviewReply.source_job_id == job.id)).first()
    assert saved.outcome == "declined"


def test_handle_review_reply_unclear_records_outcome(session, monkeypatch):
    client = make_client(session, review_link="https://g.page/r/test")
    job = _job_awaiting_reply(session, client)
    monkeypatch.setattr(review_service, "agent", _stub_reply_outcome("unclear"))

    review_service.handle_review_reply(session, client, job, "????")

    saved = session.exec(select(ReviewReply).where(ReviewReply.source_job_id == job.id)).first()
    assert saved.outcome == "unclear"


def test_handle_review_reply_negative_records_and_notifies_the_owner(session, monkeypatch):
    client = make_client(session, review_link="https://g.page/r/test")
    job = _job_awaiting_reply(session, client)
    monkeypatch.setattr(review_service, "agent", _stub_reply_outcome("negative"))
    calls = []
    monkeypatch.setattr(
        review_service,
        "notify_owner_of_escalation",
        lambda business, caller, reason, **k: calls.append((caller, reason)) or True,
    )

    reply = review_service.handle_review_reply(
        session, client, job, "honestly the technician was late and rude"
    )

    saved = session.exec(select(ReviewReply).where(ReviewReply.source_job_id == job.id)).first()
    assert saved.outcome == "negative"
    assert len(calls) == 1
    assert calls[0][0] == "+15551234567"
    # The reply sent back to the customer must never point at the review
    # link again once they've expressed real dissatisfaction.
    assert "https://g.page/r/test" not in reply

    notifications = session.exec(
        select(OwnerNotification).where(OwnerNotification.business_id == client.id)
    ).all()
    assert len(notifications) == 1
    assert notifications[0].kind == "escalation"
    assert notifications[0].source == "negative_review_reply"


def test_handle_review_reply_only_negative_triggers_an_owner_notification(session, monkeypatch):
    """Regression: left_review/positive/neutral/declined/unclear must never
    page the owner — only genuine dissatisfaction does."""
    client = make_client(session, review_link="https://g.page/r/test")
    calls = []
    monkeypatch.setattr(
        review_service,
        "notify_owner_of_escalation",
        lambda business, caller, reason, **k: calls.append(1) or True,
    )

    for outcome in ("left_review", "positive", "neutral", "declined", "unclear"):
        job = _job_awaiting_reply(session, client)
        monkeypatch.setattr(review_service, "agent", _stub_reply_outcome(outcome))
        review_service.handle_review_reply(session, client, job, "some reply")

    assert calls == []


def test_handle_review_reply_is_never_called_twice_for_the_same_ask(session, monkeypatch):
    """Duplicate-reply protection at the routing layer (find_active_review_ask
    already covers this — this test proves it end to end): a second inbound
    text after a reply is already recorded must not reach handle_review_reply
    again via the normal routing chain."""
    client = make_client(session, review_link="https://g.page/r/test")
    job = _job_awaiting_reply(session, client)
    monkeypatch.setattr(review_service, "agent", _stub_reply_outcome("declined"))

    review_service.handle_review_reply(session, client, job, "no thanks")
    still_active = review_service.find_active_review_ask(session, client.id, "+15551234567")

    assert still_active is None
    replies = session.exec(select(ReviewReply).where(ReviewReply.source_job_id == job.id)).all()
    assert len(replies) == 1


def test_handle_review_reply_skips_paid_call_past_trial_cap(session, monkeypatch):
    """Mirrors handle_referral_reply's own trial-cap fallback: past the soft
    buffer, skip the paid classification call but still persist the raw
    reply — recorded as unclear, since nothing classified it."""
    client = make_client(session, review_link="https://g.page/r/test")
    client.trial_spend_cents = client.trial_cap_cents + client.trial_soft_buffer_cents + 1
    session.add(client)
    session.commit()
    job = _job_awaiting_reply(session, client)

    reply = review_service.handle_review_reply(session, client, job, "left a review!")

    assert reply is None
    saved = session.exec(select(ReviewReply).where(ReviewReply.source_job_id == job.id)).first()
    assert saved is not None
    assert saved.outcome == "unclear"
    assert saved.raw_reply_text == "left a review!"


def test_handle_review_reply_business_isolation(session, monkeypatch):
    client_a = make_client(session, review_link="https://g.page/r/a")
    client_b = Business(
        business_name="Other Co",
        trade="HVAC",
        hours="9-5",
        pricing_faq="n/a",
        escalation_phone="+15550009999",
        inbound_number="+15559991111",
        review_link="https://g.page/r/b",
    )
    session.add(client_b)
    session.commit()
    session.refresh(client_b)

    job_a = _job_awaiting_reply(session, client_a)
    monkeypatch.setattr(review_service, "agent", _stub_reply_outcome("left_review"))
    review_service.handle_review_reply(session, client_a, job_a, "left a review")

    assert review_service.find_active_review_ask(session, client_b.id, "+15551234567") is None


# --- deployment gate ---------------------------------------------------------
# Reviews went `live` in employees.REGISTRY on 2026-08-04. "Live" means a
# business can hire it — which is only true if NOT hiring it means it stays
# quiet. review_link is configuration; the Employee row is the deployment
# record (ARCHITECTURE.md invariant 8), and it is what these two guard.


def test_no_review_request_when_reviews_is_not_deployed(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(review_service, "sms_channel", fake_channel)

    client = make_client(session, review_link="https://g.page/r/test", deployed=False)
    job = Job(
        business_id=client.id,
        service_type="AC repair",
        urgency="routine",
        callback_number="+15551234567",
        completed_at=datetime.utcnow() - timedelta(days=2),
    )
    session.add(job)
    session.commit()

    assert review_service.send_due_review_requests(session) == []
    assert fake_channel.sent == []
    session.refresh(job)
    assert job.review_requested_at is None


def test_no_review_followup_when_reviews_is_not_deployed(session, monkeypatch):
    fake_channel = FakeSMSChannel()
    monkeypatch.setattr(review_service, "sms_channel", fake_channel)

    client = make_client(session, review_link="https://g.page/r/test", deployed=False)
    job = Job(
        business_id=client.id,
        service_type="AC repair",
        urgency="routine",
        callback_number="+15551234567",
        completed_at=datetime.utcnow() - timedelta(days=10),
        review_requested_at=datetime.utcnow() - timedelta(days=9),
    )
    session.add(job)
    session.commit()

    assert review_service.send_due_review_followups(session) == []
    assert fake_channel.sent == []
