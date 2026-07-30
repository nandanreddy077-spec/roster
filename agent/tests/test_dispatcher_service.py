from sqlmodel import select

import dispatcher_service
from db_models import Business, DispatchPlan, Job, JobQualification
from deployment import deploy_role


def make_client(session, **overrides) -> Business:
    defaults = dict(
        business_name="Ridgeline Plumbing", trade="Plumbing", hours="9-5",
        pricing_faq="n/a", escalation_phone="+15550000000",
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
        business_id=client.id, service_type="AC not cooling", urgency="routine",
        callback_number="+15551234567", address="123 Main St",
    )
    defaults.update(overrides)
    job = Job(**defaults)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def _qualify(session, job, **overrides) -> JobQualification:
    defaults = dict(
        business_id=job.business_id, source_job_id=job.id, job_type="repair",
        financing_candidate=False, membership_candidate=False,
        priority="normal", possible_spam=False, reasoning="X",
    )
    defaults.update(overrides)
    row = JobQualification(**defaults)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


# ---- deployment gating (2026-07-30, Critical Finding #2 fix) ---------------
# recommend_dispatch now dispatches through runner.dispatch_tick — only
# businesses with dispatcher deployed are ever processed.

def test_recommend_dispatch_skips_a_qualified_job_for_an_undeployed_business(session):
    client = make_client(session)  # never deployed
    job = _job(session, client)
    _qualify(session, job)

    planned = dispatcher_service.recommend_dispatch(session)

    assert planned == []
    assert session.exec(select(DispatchPlan)).all() == []


def test_recommend_dispatch_only_processes_deployed_businesses(session):
    deployed = make_client(session, business_name="Deployed Co", inbound_number="+15559990003")
    not_deployed = make_client(session, business_name="Not Deployed Co", inbound_number="+15559990004")
    deploy_role(session, deployed.id, "dispatcher")
    job_a = _job(session, deployed, callback_number="+1")
    job_b = _job(session, not_deployed, callback_number="+2")
    _qualify(session, job_a)
    _qualify(session, job_b)

    planned = dispatcher_service.recommend_dispatch(session)

    assert len(planned) == 1
    assert planned[0].source_job_id == job_a.id
    assert planned[0].business_id == deployed.id


# ---- classification behavior (deployment already established) --------------

def test_recommend_dispatch_creates_one_plan_per_qualified_job(session):
    client = make_client(session)
    deploy_role(session, client.id, "dispatcher")
    job = _job(session, client)
    _qualify(session, job)

    planned = dispatcher_service.recommend_dispatch(session)

    assert len(planned) == 1
    row = planned[0]
    assert row.source_job_id == job.id
    assert row.business_id == client.id
    assert row.dispatch_priority == "normal"
    assert row.dispatch_reason != ""


def test_recommend_dispatch_skips_jobs_not_yet_qualified(session):
    client = make_client(session)
    deploy_role(session, client.id, "dispatcher")
    _job(session, client)  # no JobQualification row

    planned = dispatcher_service.recommend_dispatch(session)

    assert planned == []
    assert session.exec(select(DispatchPlan)).all() == []


def test_recommend_dispatch_is_idempotent(session):
    client = make_client(session)
    deploy_role(session, client.id, "dispatcher")
    job = _job(session, client)
    _qualify(session, job)

    first = dispatcher_service.recommend_dispatch(session)
    second = dispatcher_service.recommend_dispatch(session)

    assert len(first) == 1
    assert second == []
    assert len(session.exec(select(DispatchPlan)).all()) == 1


def test_recommend_dispatch_business_isolation(session):
    client_a = make_client(session, business_name="A Co", inbound_number="+15559990001")
    client_b = make_client(session, business_name="B Co", inbound_number="+15559990002")
    deploy_role(session, client_a.id, "dispatcher")
    deploy_role(session, client_b.id, "dispatcher")
    job_a = _job(session, client_a, callback_number="+1")
    job_b = _job(session, client_b, callback_number="+2")
    _qualify(session, job_a)
    _qualify(session, job_b)

    planned = dispatcher_service.recommend_dispatch(session)

    by_source = {row.source_job_id: row for row in planned}
    assert by_source[job_a.id].business_id == client_a.id
    assert by_source[job_b.id].business_id == client_b.id


def test_recommend_dispatch_never_mutates_job_or_qualification(session):
    client = make_client(session)
    deploy_role(session, client.id, "dispatcher")
    job = _job(session, client)
    qualification = _qualify(session, job)
    original_urgency = job.urgency
    original_job_type = qualification.job_type

    dispatcher_service.recommend_dispatch(session)

    session.refresh(job)
    session.refresh(qualification)
    assert job.urgency == original_urgency
    assert qualification.job_type == original_job_type
