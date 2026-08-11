import referral_service
from conftest import StubAgent
from db_models import Business, Job, ReferralLead
from sqlmodel import Session, select


def test_referral_reply_skips_paid_call_but_logs_raw_when_capped(test_engine, monkeypatch):
    monkeypatch.setattr(
        referral_service,
        "agent",
        StubAgent({"reply": "should not be used", "pending_tool_call": None}),
    )
    with Session(test_engine) as session:
        client = Business(
            business_name="Ridgeline",
            trade="Plumbing",
            services_json="[]",
            hours="9-5",
            escalation_phone="+1555",
            trial_spend_cents=2200,
            trial_cap_cents=2000,
            trial_soft_buffer_cents=200,
        )
        session.add(client)
        session.commit()
        session.refresh(client)
        job = Job(
            business_id=client.id,
            customer_phone="+15551112222",
            service_type="AC",
            urgency="routine",
            callback_number="+15551112222",
        )
        session.add(job)
        session.commit()
        session.refresh(job)

        reply = referral_service.handle_referral_reply(
            session, client, job, "my neighbor Bob, 555-9999"
        )

        assert reply is None
        assert client.trial_spend_cents == 2200  # no paid call
        lead = session.exec(
            select(ReferralLead).where(ReferralLead.source_job_id == job.id)
        ).first()
        assert lead is not None
        assert lead.raw_reply_text == "my neighbor Bob, 555-9999"  # raw text preserved


def test_referral_reply_records_usage_when_under_cap(test_engine, monkeypatch):
    monkeypatch.setattr(
        referral_service, "agent", StubAgent({"reply": "Thanks!", "pending_tool_call": None})
    )
    with Session(test_engine) as session:
        client = Business(
            business_name="Ridgeline",
            trade="Plumbing",
            services_json="[]",
            hours="9-5",
            escalation_phone="+1555",
        )
        session.add(client)
        session.commit()
        session.refresh(client)
        job = Job(
            business_id=client.id,
            customer_phone="+15551112222",
            service_type="AC",
            urgency="routine",
            callback_number="+15551112222",
        )
        session.add(job)
        session.commit()
        session.refresh(job)

        referral_service.handle_referral_reply(session, client, job, "thanks")

        assert client.trial_spend_cents == 50
