from sqlmodel import Session

from conftest import StubAgent
from db_models import Business
import service


def test_handle_customer_message_skips_agent_when_cap_exhausted(test_engine, monkeypatch):
    monkeypatch.setattr(
        service,
        "agent",
        StubAgent({"reply": "should not be used", "jobs": [], "new_messages": []}),
    )
    with Session(test_engine) as session:
        client = Business(
            email="owner@example.com", password_hash="x", business_name="Ridgeline",
            trade="Plumbing", services_json="[]", hours="9-5", escalation_phone="555",
            trial_spend_cents=2200, trial_cap_cents=2000, trial_soft_buffer_cents=200,
        )
        session.add(client)
        session.commit()
        session.refresh(client)

        result = service.handle_customer_message(session, client, "+15550001111", "hello")

        assert result["reply"] is None
        assert result["jobs"] == []
        assert client.trial_spend_cents == 2200  # unchanged — no call was made


def test_handle_customer_message_runs_agent_and_records_usage_when_under_cap(test_engine, monkeypatch):
    monkeypatch.setattr(
        service,
        "agent",
        StubAgent({"reply": "Hi there!", "jobs": [], "new_messages": []}),
    )
    with Session(test_engine) as session:
        client = Business(
            email="owner@example.com", password_hash="x", business_name="Ridgeline",
            trade="Plumbing", services_json="[]", hours="9-5", escalation_phone="555",
        )
        session.add(client)
        session.commit()
        session.refresh(client)

        result = service.handle_customer_message(session, client, "+15550001111", "hello")

        assert result["reply"] == "Hi there!"
        assert client.trial_spend_cents == 50
