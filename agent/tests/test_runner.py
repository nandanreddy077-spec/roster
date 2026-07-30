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
from runner import (
    JOB_COMPLETED_ROLES,
    RoleDefinition,
    deployed_businesses,
    dispatch_job_completed,
    dispatch_tick,
    is_active,
)


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


# ---- tick-based dispatch (2026-07-30, Critical Finding #2) -------------------
# The tick-triggered counterpart to dispatch_job_completed: the same shape
# (resolve deployment, only then call the employee's own logic), generalized
# for "scan across every deployed business" instead of "react to one business's
# completed job." This is the enforced invariant — an employee's own
# processing function never queries across businesses itself; it only ever
# receives a Business the dispatcher has already confirmed is deployed for
# that role, so there is no reachable path to an undeployed business's data.

def test_deployed_businesses_returns_only_businesses_with_the_role_active(session):
    deployed = _business(session, "runner7a@test.io")
    not_deployed = _business(session, "runner7b@test.io")
    deploy_role(session, deployed.id, "lead_qualifier")

    result = {b.id for b in deployed_businesses(session, "lead_qualifier")}

    assert result == {deployed.id}
    assert not_deployed.id not in result


def test_deployed_businesses_excludes_fired_employees(session):
    b = _business(session, "runner7c@test.io")
    row = deploy_role(session, b.id, "lead_qualifier")
    row.status = "fired"
    session.add(row)
    session.commit()

    assert deployed_businesses(session, "lead_qualifier") == []


def test_deployed_businesses_normalizes_legacy_role_key_spellings(session):
    """Mirrors is_active's own retention/retention_manager normalization —
    the bulk-query sibling must resolve the same way the single-business
    check does, or the two would disagree about the same row."""
    b = _business(session, "runner7d@test.io")
    session.add(Employee(business_id=b.id, role_key="retention"))
    session.commit()

    result = {biz.id for biz in deployed_businesses(session, "retention_manager")}

    assert result == {b.id}


def test_dispatch_tick_calls_capability_only_for_deployed_businesses(session):
    deployed = _business(session, "runner8a@test.io")
    not_deployed = _business(session, "runner8b@test.io")
    deploy_role(session, deployed.id, "lead_qualifier")
    seen = []

    def capability(s, business):
        seen.append(business.id)
        return [business.id]

    results = dispatch_tick(session, "lead_qualifier", capability)

    assert seen == [deployed.id]
    assert results == [deployed.id]


def test_dispatch_tick_is_a_noop_with_no_deployed_businesses(session):
    _business(session, "runner8c@test.io")  # never deployed
    calls = []

    results = dispatch_tick(session, "lead_qualifier", lambda s, b: calls.append(1))

    assert calls == []
    assert results == []


def test_dispatch_tick_aggregates_results_across_multiple_deployed_businesses(session):
    a = _business(session, "runner8d@test.io")
    b = _business(session, "runner8e@test.io")
    deploy_role(session, a.id, "dispatcher")
    deploy_role(session, b.id, "dispatcher")

    results = dispatch_tick(session, "dispatcher", lambda s, biz: [f"job-for-{biz.id}"])

    assert set(results) == {f"job-for-{a.id}", f"job-for-{b.id}"}
