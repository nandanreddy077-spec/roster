import json

import runner
from db_models import Business, Job
from runner import RoleDefinition, is_active, dispatch_job_completed, JOB_COMPLETED_ROLES


def test_frontdesk_active_reflects_frontdesk_live_flag():
    live = Business(business_name="B", frontdesk_live=True)
    not_live = Business(business_name="B", frontdesk_live=False)
    assert is_active(live, "frontdesk") is True
    assert is_active(not_live, "frontdesk") is False


def test_quote_chaser_active_reflects_requested_roster():
    requested = Business(business_name="B", requested_roster=json.dumps(["Quote Chaser"]))
    not_requested = Business(business_name="B", requested_roster=json.dumps([]))
    assert is_active(requested, "quote_chaser") is True
    assert is_active(not_requested, "quote_chaser") is False


def test_raw_role_key_active_via_requested_roster_fallback():
    """The founder deploy route writes raw role_keys; is_active must match
    them directly (no display-name mapping) for any role not in name_by_key."""
    requested = Business(business_name="B", requested_roster=json.dumps(["some_future_role"]))
    assert is_active(requested, "some_future_role") is True


def test_dispatch_job_completed_is_a_noop_with_empty_registry():
    """The seam ships empty on purpose — no registered employee means no
    dispatch, no error."""
    assert JOB_COMPLETED_ROLES == []
    business = Business(id=1, business_name="B", requested_roster=json.dumps([]))
    job = Job(business_id=1, service_type="drain cleaning", urgency="routine")
    dispatch_job_completed(None, business, job)  # must not raise


def test_dispatch_job_completed_fires_a_registered_active_role(monkeypatch):
    """Proves the seam works when a real employee is eventually registered,
    without shipping a speculative one."""
    sent = []
    fake_role = RoleDefinition(
        role_key="some_future_role", trigger="job_completed",
        capability=lambda session, business, job: sent.append(business.id),
    )
    monkeypatch.setattr(runner, "JOB_COMPLETED_ROLES", [fake_role])
    business = Business(id=1, business_name="B", requested_roster=json.dumps(["some_future_role"]))
    job = Job(business_id=1, service_type="drain cleaning", urgency="routine")
    dispatch_job_completed(None, business, job)
    assert sent == [1]


def test_dispatch_job_completed_skips_registered_but_inactive_role(monkeypatch):
    sent = []
    fake_role = RoleDefinition(
        role_key="some_future_role", trigger="job_completed",
        capability=lambda session, business, job: sent.append(business.id),
    )
    monkeypatch.setattr(runner, "JOB_COMPLETED_ROLES", [fake_role])
    business = Business(id=1, business_name="B", requested_roster=json.dumps([]))
    job = Job(business_id=1, service_type="drain cleaning", urgency="routine")
    dispatch_job_completed(None, business, job)
    assert sent == []
