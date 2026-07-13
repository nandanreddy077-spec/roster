from sqlmodel import Session

import trial_cap
from db_models import Business
from trial_cap import TRIAL_TURN_COST_CENTS, can_respond, record_usage


def _client(**overrides):
    defaults = dict(email="owner@example.com", password_hash="x", business_name="Ridgeline")
    defaults.update(overrides)
    return Business(**defaults)


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


def test_record_usage_notifies_founder_once_on_crossing_cap(test_engine, monkeypatch):
    monkeypatch.setenv("FOUNDER_ALERT_PHONE", "+19015550000")
    sent = []

    class Recorder:
        def send(self, from_number, to_number, body):
            sent.append((to_number, body))

    monkeypatch.setattr(trial_cap, "sms_channel", Recorder())
    with Session(test_engine) as session:
        client = _client(business_name="Ridgeline", trial_spend_cents=1960, trial_cap_cents=2000, trial_soft_buffer_cents=200)
        session.add(client)
        session.commit()
        session.refresh(client)

        record_usage(session, client)  # 1960 -> 2010, crosses 2000
        assert len(sent) == 1
        assert sent[0][0] == "+19015550000"
        assert client.trial_cap_notified is True

        record_usage(session, client)  # 2010 -> 2060, already notified
        assert len(sent) == 1  # no second alert


def test_record_usage_no_alert_when_founder_phone_unset(test_engine, monkeypatch):
    monkeypatch.delenv("FOUNDER_ALERT_PHONE", raising=False)
    with Session(test_engine) as session:
        client = _client(trial_spend_cents=1960, trial_cap_cents=2000, trial_soft_buffer_cents=200)
        session.add(client)
        session.commit()
        session.refresh(client)

        record_usage(session, client)  # crosses cap, but no phone configured

        assert client.trial_spend_cents == 2010
        assert client.trial_cap_notified is True  # flag still set (notify-once, even if no channel)
