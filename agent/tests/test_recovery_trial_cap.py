import recovery_service
from conftest import StubAgent
from db_models import Business, RecoveryJob
from sqlmodel import Session


def _capped_client(session):
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
    return client


def test_recovery_reply_skips_paid_call_when_capped(test_engine, monkeypatch):
    monkeypatch.setattr(
        recovery_service,
        "agent",
        StubAgent(
            {
                "reply": "should not be used",
                "jobs": [],
                "new_messages": [],
                "pending_tool_call": None,
            }
        ),
    )
    with Session(test_engine) as session:
        client = _capped_client(session)
        job = RecoveryJob(
            campaign_id=1,
            business_id=client.id,
            customer_phone="+15551112222",
            service_type="AC repair",
            current_status="pending",
            last_sent_day=1,
        )
        session.add(job)
        session.commit()
        session.refresh(job)

        reply = recovery_service.handle_recovery_reply(session, client, job, "yes I'm interested")

        assert reply is None
        assert client.trial_spend_cents == 2200  # unchanged — no paid call


def test_recovery_reply_records_usage_when_under_cap(test_engine, monkeypatch):
    monkeypatch.setattr(
        recovery_service,
        "agent",
        StubAgent(
            {
                "reply": "Thanks for the reply!",
                "jobs": [],
                "new_messages": [],
                "pending_tool_call": None,
            }
        ),
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
        job = RecoveryJob(
            campaign_id=1,
            business_id=client.id,
            customer_phone="+15551112222",
            service_type="AC repair",
            current_status="pending",
            last_sent_day=1,
        )
        session.add(job)
        session.commit()
        session.refresh(job)

        recovery_service.handle_recovery_reply(session, client, job, "maybe, tell me more")

        assert client.trial_spend_cents == 50  # one paid turn recorded
