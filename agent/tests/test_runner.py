import json

import runner
from db_models import Business, Job
from runner import RoleDefinition, is_active, dispatch_job_completed, DECLARED_ROLES


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


def test_upsell_agent_active_reflects_requested_roster_by_raw_key():
    requested = Business(business_name="B", requested_roster=json.dumps(["upsell_agent"]))
    assert is_active(requested, "upsell_agent") is True


def test_every_declared_role_has_capability_or_a_blocked_on_reason():
    for defn in DECLARED_ROLES:
        assert defn.capability is not None or defn.blocked_on, (
            f"{defn.role_key} has no capability and no blocked_on reason"
        )


def test_declared_role_keys_are_unique():
    keys = [d.role_key for d in DECLARED_ROLES]
    assert len(keys) == len(set(keys))


def test_dispatch_job_completed_fires_capability_when_active(monkeypatch):
    sent = []
    fake_role = RoleDefinition(
        role_key="upsell_agent", trigger="job_completed",
        capability=lambda session, business, job: sent.append(business.id),
    )
    monkeypatch.setattr(runner, "JOB_COMPLETED_ROLES", [fake_role])
    business = Business(id=1, business_name="B", requested_roster=json.dumps(["upsell_agent"]))
    job = Job(business_id=1, service_type="drain cleaning", urgency="routine")
    dispatch_job_completed(None, business, job)
    assert sent == [1]


def test_dispatch_job_completed_skips_capability_when_not_active(monkeypatch):
    sent = []
    fake_role = RoleDefinition(
        role_key="upsell_agent", trigger="job_completed",
        capability=lambda session, business, job: sent.append(business.id),
    )
    monkeypatch.setattr(runner, "JOB_COMPLETED_ROLES", [fake_role])
    business = Business(id=1, business_name="B", requested_roster=json.dumps([]))
    job = Job(business_id=1, service_type="drain cleaning", urgency="routine")
    dispatch_job_completed(None, business, job)
    assert sent == []
