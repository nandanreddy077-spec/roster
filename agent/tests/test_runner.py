"""Runner seam tests.

REWRITTEN in Phase 4a: these previously asserted that deployment state lived
in Business.frontdesk_live + requested_roster. That is the conflation the
departments migration removes — requested_roster meant both "the customer
asked for this" and "this is running" (audit F6). Deployment state is now the
Employee row, so every scenario below is preserved but driven through
deployment.deploy_role instead.
"""
import runner
from db_models import Business, Employee, Job
from deployment import deploy_role
from runner import JOB_COMPLETED_ROLES, RoleDefinition, dispatch_job_completed, is_active


def _business(session, email):
    b = Business(business_name="B", trade="hvac", email=email)
    session.add(b)
    session.commit()
    session.refresh(b)
    return b


def test_frontdesk_active_reflects_deployment(session):
    live = _business(session, "runner1a@test.io")
    not_live = _business(session, "runner1b@test.io")
    deploy_role(session, live.id, "frontdesk")

    assert is_active(session, live, "frontdesk") is True
    assert is_active(session, not_live, "frontdesk") is False


def test_quote_chaser_active_reflects_deployment(session):
    deployed = _business(session, "runner2a@test.io")
    not_deployed = _business(session, "runner2b@test.io")
    deploy_role(session, deployed.id, "quote_chaser")

    assert is_active(session, deployed, "quote_chaser") is True
    assert is_active(session, not_deployed, "quote_chaser") is False


def test_a_role_key_outside_the_registry_still_resolves_from_its_row(session):
    """A future role registered after this code was written. deploy_role would
    reject the key (it validates against the registry), so the row is inserted
    directly — is_active must answer from the row itself, not from a registry
    lookup, or newly-added roles would read as inactive until a redeploy."""
    b = _business(session, "runner3@test.io")
    session.add(Employee(business_id=b.id, role_key="some_future_role"))
    session.commit()

    assert is_active(session, b, "some_future_role") is True


def test_dispatch_job_completed_is_a_noop_with_empty_registry(session):
    """The seam ships empty on purpose — no registered employee means no
    dispatch, no error."""
    assert JOB_COMPLETED_ROLES == []
    b = _business(session, "runner4@test.io")
    job = Job(business_id=b.id, service_type="drain cleaning", urgency="routine")

    dispatch_job_completed(session, b, job)  # must not raise


def test_dispatch_job_completed_fires_a_registered_active_role(session, monkeypatch):
    """Proves the seam works when a real employee is eventually registered,
    without shipping a speculative one."""
    sent = []
    fake_role = RoleDefinition(
        role_key="quote_chaser", trigger="job_completed",
        capability=lambda s, business, job: sent.append(business.id),
    )
    monkeypatch.setattr(runner, "JOB_COMPLETED_ROLES", [fake_role])
    b = _business(session, "runner5@test.io")
    deploy_role(session, b.id, "quote_chaser")
    job = Job(business_id=b.id, service_type="drain cleaning", urgency="routine")

    dispatch_job_completed(session, b, job)

    assert sent == [b.id]


def test_dispatch_job_completed_skips_registered_but_inactive_role(session, monkeypatch):
    sent = []
    fake_role = RoleDefinition(
        role_key="quote_chaser", trigger="job_completed",
        capability=lambda s, business, job: sent.append(business.id),
    )
    monkeypatch.setattr(runner, "JOB_COMPLETED_ROLES", [fake_role])
    b = _business(session, "runner6@test.io")  # never deployed
    job = Job(business_id=b.id, service_type="drain cleaning", urgency="routine")

    dispatch_job_completed(session, b, job)

    assert sent == []
