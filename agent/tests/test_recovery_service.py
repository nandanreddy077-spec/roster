import json

from sqlmodel import Session, select

from db_models import Client, RecoveryJob
import recovery_service


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
