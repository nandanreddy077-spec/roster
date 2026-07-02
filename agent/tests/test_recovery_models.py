import json

from sqlmodel import Session

from db_models import Client, RecoveryCampaign, RecoveryJob, RecoveryMessageLog


def make_client(session: Session) -> Client:
    client = Client(
        business_name="Test Co", trade="HVAC", services_json=json.dumps(["AC repair"]),
        hours="9-5", pricing_faq="n/a", escalation_phone="+15550000000",
    )
    session.add(client)
    session.commit()
    session.refresh(client)
    return client


def test_recovery_campaign_customer_list_roundtrips(session):
    client = make_client(session)
    campaign = RecoveryCampaign(
        client_id=client.id, face="quote", name="June quotes",
        customer_list_json=json.dumps([{"phone": "+15551112222", "service_type": "AC install"}]),
    )
    session.add(campaign)
    session.commit()
    session.refresh(campaign)

    assert campaign.customer_list == [{"phone": "+15551112222", "service_type": "AC install"}]
    assert campaign.template_overrides == {}


def test_recovery_job_defaults_to_pending_status(session):
    client = make_client(session)
    campaign = RecoveryCampaign(
        client_id=client.id, face="reactivation", name="Dormant list", customer_list_json="[]",
    )
    session.add(campaign)
    session.commit()
    session.refresh(campaign)

    job = RecoveryJob(
        campaign_id=campaign.id, client_id=client.id, customer_phone="+15551112222",
        service_type="Tune-up",
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    assert job.current_status == "pending"
    assert job.offered_slots == []


def test_recovery_message_log_links_to_job(session):
    client = make_client(session)
    campaign = RecoveryCampaign(client_id=client.id, face="quote", name="X", customer_list_json="[]")
    session.add(campaign)
    session.commit()
    session.refresh(campaign)
    job = RecoveryJob(campaign_id=campaign.id, client_id=client.id, customer_phone="+1", service_type="AC")
    session.add(job)
    session.commit()
    session.refresh(job)

    log = RecoveryMessageLog(recovery_job_id=job.id, message_day=1, message_text="Hi there!")
    session.add(log)
    session.commit()
    session.refresh(log)

    assert log.recovery_job_id == job.id
    assert log.customer_reply is None
