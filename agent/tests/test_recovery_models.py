import json

from sqlmodel import Session

from db_models import Business, Job, RecoveryCampaign, RecoveryJob, RecoveryMessageLog


def make_client(session: Session) -> Business:
    client = Business(
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
        business_id=client.id, face="quote", name="June quotes",
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
        business_id=client.id, face="reactivation", name="Dormant list", customer_list_json="[]",
    )
    session.add(campaign)
    session.commit()
    session.refresh(campaign)

    job = RecoveryJob(
        campaign_id=campaign.id, business_id=client.id, customer_phone="+15551112222",
        service_type="Tune-up",
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    assert job.current_status == "pending"
    assert job.offered_slots == []


def test_recovery_message_log_links_to_job(session):
    client = make_client(session)
    campaign = RecoveryCampaign(business_id=client.id, face="quote", name="X", customer_list_json="[]")
    session.add(campaign)
    session.commit()
    session.refresh(campaign)
    job = RecoveryJob(campaign_id=campaign.id, business_id=client.id, customer_phone="+1", service_type="AC")
    session.add(job)
    session.commit()
    session.refresh(job)

    log = RecoveryMessageLog(recovery_job_id=job.id, message_day=1, message_text="Hi there!")
    session.add(log)
    session.commit()
    session.refresh(log)

    assert log.recovery_job_id == job.id
    assert log.customer_reply is None


def test_recovery_job_anchor_date_defaults_to_none_and_roundtrips(session):
    client = make_client(session)
    campaign = RecoveryCampaign(
        business_id=client.id, face="quote", name="June quotes", customer_list_json="[]",
    )
    session.add(campaign)
    session.commit()
    session.refresh(campaign)

    unanchored = RecoveryJob(
        campaign_id=campaign.id, business_id=client.id, customer_phone="+1", service_type="AC repair",
    )
    session.add(unanchored)
    session.commit()
    session.refresh(unanchored)
    assert unanchored.anchor_date is None

    anchored = RecoveryJob(
        campaign_id=campaign.id, business_id=client.id, customer_phone="+2",
        service_type="AC tune-up", anchor_date="2026-07-15",
    )
    session.add(anchored)
    session.commit()
    session.refresh(anchored)
    assert anchored.anchor_date == "2026-07-15"


def test_client_review_link_defaults_to_none(session):
    client = make_client(session)
    assert client.review_link is None

    client.review_link = "https://g.page/r/test"
    session.add(client)
    session.commit()
    session.refresh(client)
    assert client.review_link == "https://g.page/r/test"


def test_job_completed_at_defaults_to_none(session):
    client = make_client(session)
    job = Job(business_id=client.id, service_type="AC repair", urgency="routine")
    session.add(job)
    session.commit()
    session.refresh(job)
    assert job.completed_at is None


from db_models import ReferralLead


def test_job_referral_sent_at_defaults_to_none(session):
    client = make_client(session)
    job = Job(business_id=client.id, service_type="AC repair", urgency="routine")
    session.add(job)
    session.commit()
    session.refresh(job)
    assert job.referral_sent_at is None


def test_client_referral_incentive_defaults_to_none(session):
    client = make_client(session)
    assert client.referral_incentive is None

    client.referral_incentive = "$25 off your next service"
    session.add(client)
    session.commit()
    session.refresh(client)
    assert client.referral_incentive == "$25 off your next service"


def test_referral_lead_roundtrips(session):
    client = make_client(session)
    job = Job(business_id=client.id, service_type="AC repair", urgency="routine", callback_number="+1")
    session.add(job)
    session.commit()
    session.refresh(job)

    lead = ReferralLead(
        business_id=client.id, source_job_id=job.id, asker_phone="+1",
        referred_name="Sarah", referred_phone="+15559998888",
        raw_reply_text="my friend Sarah, 555-998-8888",
    )
    session.add(lead)
    session.commit()
    session.refresh(lead)

    assert lead.source_job_id == job.id
    assert lead.referred_name == "Sarah"
    assert lead.raw_reply_text == "my friend Sarah, 555-998-8888"


def test_referral_lead_allows_null_referred_fields(session):
    client = make_client(session)
    job = Job(business_id=client.id, service_type="AC repair", urgency="routine", callback_number="+1")
    session.add(job)
    session.commit()
    session.refresh(job)

    lead = ReferralLead(business_id=client.id, source_job_id=job.id, asker_phone="+1", raw_reply_text="no thanks")
    session.add(lead)
    session.commit()
    session.refresh(lead)

    assert lead.referred_name is None
    assert lead.referred_phone is None
