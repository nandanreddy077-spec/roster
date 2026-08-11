from sqlmodel import select

import lead_qualifier_service
from db_models import Business, Customer, Job, JobQualification
from deployment import deploy_role
from lead_qualifier_rules import REASON_MEMBERSHIP_ELIGIBLE_NO_PLAN


def make_client(session, **overrides) -> Business:
    defaults = dict(
        business_name="Ridgeline Plumbing",
        trade="Plumbing",
        hours="9-5",
        pricing_faq="n/a",
        escalation_phone="+15550000000",
        inbound_number="+15559990000",
    )
    defaults.update(overrides)
    client = Business(**defaults)
    session.add(client)
    session.commit()
    session.refresh(client)
    return client


def _job(session, client, **overrides) -> Job:
    defaults = dict(
        business_id=client.id,
        service_type="AC not cooling",
        urgency="routine",
        callback_number="+15551234567",
    )
    defaults.update(overrides)
    job = Job(**defaults)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


# ---- deployment gating (2026-07-30, Critical Finding #2 fix) ---------------
# qualify_new_jobs now dispatches through runner.dispatch_tick — only
# businesses with lead_qualifier deployed are ever processed. Every test
# below that expects real qualification to happen deploys the employee
# first; the tests in this section prove the gate itself.


def test_qualify_new_jobs_skips_a_job_for_an_undeployed_business(session):
    client = make_client(session)  # never deployed
    _job(session, client)

    qualified = lead_qualifier_service.qualify_new_jobs(session)

    assert qualified == []
    assert session.exec(select(JobQualification)).all() == []


def test_qualify_new_jobs_only_processes_deployed_businesses(session):
    deployed = make_client(session, business_name="Deployed Co", inbound_number="+15559990003")
    not_deployed = make_client(
        session, business_name="Not Deployed Co", inbound_number="+15559990004"
    )
    deploy_role(session, deployed.id, "lead_qualifier")
    job_a = _job(session, deployed, callback_number="+1")
    _job(session, not_deployed, callback_number="+2")

    qualified = lead_qualifier_service.qualify_new_jobs(session)

    assert len(qualified) == 1
    assert qualified[0].source_job_id == job_a.id
    assert qualified[0].business_id == deployed.id


# ---- classification behavior (deployment already established) --------------


def test_qualify_new_jobs_creates_one_row_per_job(session):
    client = make_client(session)
    deploy_role(session, client.id, "lead_qualifier")
    job = _job(session, client)

    qualified = lead_qualifier_service.qualify_new_jobs(session)

    assert len(qualified) == 1
    row = qualified[0]
    assert row.source_job_id == job.id
    assert row.business_id == client.id
    assert row.job_type == "repair"
    assert row.reasoning != ""


def test_qualify_new_jobs_is_idempotent(session):
    client = make_client(session)
    deploy_role(session, client.id, "lead_qualifier")
    _job(session, client)

    first = lead_qualifier_service.qualify_new_jobs(session)
    second = lead_qualifier_service.qualify_new_jobs(session)

    assert len(first) == 1
    assert second == []
    assert len(session.exec(select(JobQualification)).all()) == 1


def test_qualify_new_jobs_business_isolation(session):
    client_a = make_client(session, business_name="A Co", inbound_number="+15559990001")
    client_b = make_client(session, business_name="B Co", inbound_number="+15559990002")
    deploy_role(session, client_a.id, "lead_qualifier")
    deploy_role(session, client_b.id, "lead_qualifier")
    job_a = _job(session, client_a, callback_number="+1")
    job_b = _job(session, client_b, callback_number="+2")

    qualified = lead_qualifier_service.qualify_new_jobs(session)

    by_source = {row.source_job_id: row for row in qualified}
    assert by_source[job_a.id].business_id == client_a.id
    assert by_source[job_b.id].business_id == client_b.id


def test_qualify_new_jobs_never_mutates_the_job(session):
    client = make_client(session)
    deploy_role(session, client.id, "lead_qualifier")
    job = _job(session, client, notes="original notes")
    original_urgency = job.urgency
    original_is_estimate = job.is_estimate
    original_notes = job.notes

    lead_qualifier_service.qualify_new_jobs(session)

    session.refresh(job)
    assert job.urgency == original_urgency
    assert job.is_estimate == original_is_estimate
    assert job.notes == original_notes


def test_qualify_new_jobs_uses_customer_id_when_set(session):
    client = make_client(session)
    deploy_role(session, client.id, "lead_qualifier")
    customer = Customer(business_id=client.id, phone="+15551234567", plan_notes="Gold plan")
    session.add(customer)
    session.commit()
    session.refresh(customer)
    job = _job(session, client, customer_id=customer.id)

    qualified = lead_qualifier_service.qualify_new_jobs(session)

    assert qualified[0].membership_candidate is False


def test_qualify_new_jobs_falls_back_to_phone_lookup_without_customer_id(session):
    client = make_client(session)
    deploy_role(session, client.id, "lead_qualifier")
    customer = Customer(business_id=client.id, phone="+15551234567", plan_notes="Gold plan")
    session.add(customer)
    session.commit()
    job = _job(session, client, callback_number="+15551234567", customer_phone="+15551234567")

    qualified = lead_qualifier_service.qualify_new_jobs(session)

    assert qualified[0].membership_candidate is False


def test_qualify_new_jobs_treats_no_customer_row_as_not_a_member(session):
    client = make_client(session)
    deploy_role(session, client.id, "lead_qualifier")
    _job(session, client)

    qualified = lead_qualifier_service.qualify_new_jobs(session)

    assert qualified[0].membership_candidate is True
    assert REASON_MEMBERSHIP_ELIGIBLE_NO_PLAN in qualified[0].reasoning
