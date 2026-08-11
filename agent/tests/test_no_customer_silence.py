"""Milestone B's live-turn floor: a customer who texts in ALWAYS gets a reply.

Before this, every reply handler returned None when the trial spend cap was
exhausted, and app.inbound_sms turned None into empty TwiML — a customer who
had just replied "yes, I want to book" received nothing at all. The owner was
alerted that the cap was hit; the customer was simply dropped.

The fix costs nothing: inbound replies leave as TwiML on the webhook response,
so there is no outbound API call and no per-message charge. The silence was
never buying anything.

One shared sentence (engine.FALLBACK_REPLY / employee_outcome
.CUSTOMER_FALLBACK_MESSAGE) rather than five near-identical ones — five copies
drift, and the one thing this text must never do is leak WHY (a billing cap is
not the customer's problem to parse).
"""

import membership_service
import recovery_service
import referral_service
import review_service
import service as service_module
from conftest import StubAgent
from db_models import (
    Business,
    Job,
    MembershipOffer,
    RecoveryCampaign,
    RecoveryJob,
)
from employee_outcome import CUSTOMER_FALLBACK_MESSAGE
from sqlmodel import Session


def _capped(session, **overrides):
    fields = {
        "business_name": "Ridgeline",
        "trade": "Plumbing",
        "services_json": "[]",
        "hours": "9-5",
        "escalation_phone": "+15125550149",
        "trial_spend_cents": 2200,
        "trial_cap_cents": 2000,
        "trial_soft_buffer_cents": 200,
    }
    fields.update(overrides)
    client = Business(**fields)
    session.add(client)
    session.commit()
    session.refresh(client)
    return client


def _job(session, business_id, **overrides):
    fields = {
        "business_id": business_id,
        "customer_phone": "+15125550001",
        "service_type": "AC repair",
        "urgency": "routine",
        "callback_number": "+15125550001",
    }
    fields.update(overrides)
    job = Job(**fields)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def test_frontdesk_answers_the_customer_even_when_capped(test_engine, monkeypatch):
    monkeypatch.setattr(
        service_module,
        "agent",
        StubAgent({"reply": "should not be used", "jobs": [], "new_messages": []}),
    )
    with Session(test_engine) as session:
        client = _capped(session)

        result = service_module.handle_customer_message(
            session, client, "+15125550001", "my AC died"
        )

    assert result["reply"] == CUSTOMER_FALLBACK_MESSAGE


def test_quote_chaser_answers_a_capped_customer_who_said_yes(test_engine, monkeypatch):
    """The worst instance of this bug: a recovered lead says yes and hears
    nothing back."""
    monkeypatch.setattr(
        recovery_service,
        "agent",
        StubAgent({"reply": "unused", "pending_tool_call": None}),
    )
    with Session(test_engine) as session:
        client = _capped(session)
        camp = RecoveryCampaign(
            business_id=client.id, face="quote", name="t", customer_list_json="[]"
        )
        session.add(camp)
        session.commit()
        session.refresh(camp)
        job = RecoveryJob(
            campaign_id=camp.id,
            business_id=client.id,
            customer_phone="+15125550001",
            service_type="AC replacement",
            current_status="pending",
            last_sent_day=1,
        )
        session.add(job)
        session.commit()
        session.refresh(job)

        reply = recovery_service.handle_recovery_reply(session, client, job, "yes I'm interested")

    assert reply == CUSTOMER_FALLBACK_MESSAGE


def test_reviews_answers_a_capped_customer(test_engine, monkeypatch):
    monkeypatch.setattr(
        review_service, "agent", StubAgent({"reply": "unused", "pending_tool_call": None})
    )
    with Session(test_engine) as session:
        client = _capped(session, review_link="https://g.page/x")
        job = _job(session, client.id)

        reply = review_service.handle_review_reply(session, client, job, "you were great")

    assert reply == CUSTOMER_FALLBACK_MESSAGE


def test_referral_answers_a_capped_customer(test_engine, monkeypatch):
    monkeypatch.setattr(
        referral_service, "agent", StubAgent({"reply": "unused", "pending_tool_call": None})
    )
    with Session(test_engine) as session:
        client = _capped(session, referral_incentive="$50 off")
        job = _job(session, client.id)

        reply = referral_service.handle_referral_reply(session, client, job, "my neighbor Bob")

    assert reply == CUSTOMER_FALLBACK_MESSAGE


def test_membership_answers_a_capped_customer(test_engine, monkeypatch):
    monkeypatch.setattr(
        membership_service, "agent", StubAgent({"reply": "unused", "pending_tool_call": None})
    )
    with Session(test_engine) as session:
        client = _capped(session, membership_plan="Comfort Club, $19/mo")
        job = _job(session, client.id)
        offer = MembershipOffer(
            business_id=client.id,
            source_job_id=job.id,
            customer_phone="+15125550001",
            outcome="pending",
        )
        session.add(offer)
        session.commit()
        session.refresh(offer)

        reply = membership_service.handle_membership_reply(session, client, offer, "sounds good")

    assert reply == CUSTOMER_FALLBACK_MESSAGE


# ---- the fallback must stay honest -----------------------------------------


def test_the_fallback_never_claims_a_booking():
    from booking_language import assert_no_confirmation_claim

    assert_no_confirmation_claim(CUSTOMER_FALLBACK_MESSAGE)


def test_the_fallback_never_leaks_why():
    """A billing cap or an outage is not the customer's problem to parse, and
    naming it would tell them something about the business they should never
    learn from an automated text."""
    lowered = CUSTOMER_FALLBACK_MESSAGE.lower()
    for leak in ("cap", "billing", "trial", "limit", "quota", "error", "outage", "api"):
        assert leak not in lowered


def test_capped_work_still_records_what_the_customer_said(test_engine, monkeypatch):
    """The reply changed; the existing guarantee that raw text is never lost
    must not have regressed."""
    monkeypatch.setattr(
        referral_service, "agent", StubAgent({"reply": "unused", "pending_tool_call": None})
    )
    from db_models import ReferralLead
    from sqlmodel import select

    with Session(test_engine) as session:
        client = _capped(session, referral_incentive="$50 off")
        job = _job(session, client.id)

        referral_service.handle_referral_reply(session, client, job, "my neighbor Bob, 555-9999")
        lead = session.exec(
            select(ReferralLead).where(ReferralLead.source_job_id == job.id)
        ).first()

    assert lead.raw_reply_text == "my neighbor Bob, 555-9999"
