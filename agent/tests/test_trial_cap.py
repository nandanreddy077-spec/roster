from sqlmodel import Session

from db_models import Client
from trial_cap import TRIAL_TURN_COST_CENTS, can_respond, record_usage


def _client(**overrides):
    defaults = dict(email="owner@example.com", password_hash="x", business_name="Ridgeline")
    defaults.update(overrides)
    return Client(**defaults)


def test_can_respond_true_when_under_cap():
    client = _client(trial_spend_cents=0, trial_cap_cents=2000, trial_soft_buffer_cents=200)
    assert can_respond(client) is True


def test_can_respond_true_within_soft_buffer():
    client = _client(trial_spend_cents=2050, trial_cap_cents=2000, trial_soft_buffer_cents=200)
    assert can_respond(client) is True


def test_can_respond_false_once_soft_buffer_exhausted():
    client = _client(trial_spend_cents=2200, trial_cap_cents=2000, trial_soft_buffer_cents=200)
    assert can_respond(client) is False


def test_record_usage_increments_spend(test_engine):
    with Session(test_engine) as session:
        client = _client()
        session.add(client)
        session.commit()
        session.refresh(client)

        record_usage(session, client)

        assert client.trial_spend_cents == TRIAL_TURN_COST_CENTS
