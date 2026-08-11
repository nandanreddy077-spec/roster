from sqlmodel import Session

import trial_cap
from db_models import BILLING_PAID, BILLING_TRIAL, Business
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
        client = _client(
            business_name="Ridgeline",
            trial_spend_cents=1960,
            trial_cap_cents=2000,
            trial_soft_buffer_cents=200,
        )
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


# ---- Concurrency: spend accounting must survive stale reads -----------------


def test_record_usage_is_atomic_against_stale_reads(test_engine):
    """Two concurrent turns load the same Business, then both record usage.
    A read-modify-write implementation loses one increment; the atomic
    UPDATE must count both."""
    from sqlmodel import Session
    from db_models import Business
    import trial_cap

    with Session(test_engine) as seed:
        b = Business(business_name="X", trial_spend_cents=0)
        seed.add(b)
        seed.commit()
        seed.refresh(b)
        bid = b.id

    with Session(test_engine) as s1, Session(test_engine) as s2:
        c1 = s1.get(Business, bid)
        c2 = s2.get(Business, bid)  # stale copy: loaded before c1's update
        trial_cap.record_usage(s1, c1)
        trial_cap.record_usage(s2, c2)

    with Session(test_engine) as check:
        assert check.get(Business, bid).trial_spend_cents == 2 * trial_cap.TRIAL_TURN_COST_CENTS


def test_cap_alert_fires_exactly_once_even_with_stale_clients(test_engine, monkeypatch):
    from sqlmodel import Session
    from db_models import Business
    import trial_cap

    monkeypatch.setenv("FOUNDER_ALERT_PHONE", "+15550009999")
    sent = []

    class Rec:
        def send(self, from_number, to_number, body):
            sent.append(body)

    monkeypatch.setattr(trial_cap, "sms_channel", Rec())

    with Session(test_engine) as seed:
        b = Business(
            business_name="X", trial_spend_cents=0, trial_cap_cents=trial_cap.TRIAL_TURN_COST_CENTS
        )  # first turn crosses
        seed.add(b)
        seed.commit()
        seed.refresh(b)
        bid = b.id

    with Session(test_engine) as s1, Session(test_engine) as s2:
        c1 = s1.get(Business, bid)
        c2 = s2.get(Business, bid)  # stale: still shows not-notified
        trial_cap.record_usage(s1, c1)
        trial_cap.record_usage(s2, c2)

    assert len(sent) == 1, f"founder must be alerted exactly once, got {len(sent)}"


# ---- paid accounts are never gated -------------------------------------------
# The cap had no exit before this: every business carried a $20 hard stop and
# nothing in the codebase could lift it, so a paying customer's SMS Frontdesk,
# Quote Chaser, Reviews, Membership Agent and Referral replies would all go
# silent after roughly 44 turns.


def test_a_paying_customer_is_never_capped():
    client = _client(
        billing_state=BILLING_PAID,
        trial_spend_cents=999_999,
        trial_cap_cents=2000,
        trial_soft_buffer_cents=200,
    )
    assert can_respond(client) is True


def test_a_trial_customer_is_still_capped():
    """The guard must keep working — an unattended trial is what it is for."""
    client = _client(
        billing_state=BILLING_TRIAL,
        trial_spend_cents=2200,
        trial_cap_cents=2000,
        trial_soft_buffer_cents=200,
    )
    assert can_respond(client) is False


def test_a_new_business_defaults_to_trial():
    """Provisioning must not accidentally mint an uncapped account."""
    assert _client().billing_state == BILLING_TRIAL


def test_paid_usage_is_tracked_but_never_alerts(test_engine, monkeypatch):
    """Spend is still worth knowing on a paid account — it is the input to
    pricing — but there is no cap to cross, so the founder is not paged and
    'raise the cap' is never suggested for someone already paying."""
    alerts = []
    monkeypatch.setattr(trial_cap, "_notify_founder_cap_reached", lambda c: alerts.append(c))
    with Session(test_engine) as s:
        client = _client(
            billing_state=BILLING_PAID,
            trial_spend_cents=0,
            trial_cap_cents=2000,
            trial_soft_buffer_cents=200,
        )
        s.add(client)
        s.commit()
        s.refresh(client)
        for _ in range(10):
            record_usage(s, client)
        assert client.trial_spend_cents == 10 * TRIAL_TURN_COST_CENTS
        assert client.trial_cap_notified is False
        assert alerts == []


# ---- the owner has to find out ------------------------------------------------


def test_crossing_the_cap_tells_the_owner_not_just_the_founder(test_engine, monkeypatch):
    """Before this the owner learned nothing: their employees stopped replying,
    the customer got silence, and only the founder was paged. The failure was
    invisible from the only side that matters."""
    from db_models import OwnerNotification
    from sqlmodel import select

    texts = []

    class _Rec:
        def send(self, from_number, to_number, body):
            texts.append((to_number, body))

    monkeypatch.setattr(trial_cap, "sms_channel", _Rec())
    monkeypatch.setattr(trial_cap, "_notify_founder_cap_reached", lambda c: None)

    with Session(test_engine) as s:
        client = _client(
            trial_spend_cents=1950,
            trial_cap_cents=2000,
            trial_soft_buffer_cents=200,
            escalation_phone="+15550001111",
        )
        s.add(client)
        s.commit()
        s.refresh(client)
        record_usage(s, client)  # crosses the cap
        record_usage(s, client)  # must not alert twice

        notes = s.exec(
            select(OwnerNotification).where(OwnerNotification.business_id == client.id)
        ).all()

    assert len(texts) == 1, f"owner alerted {len(texts)} times"
    assert texts[0][0] == "+15550001111"
    assert len(notes) == 1 and notes[0].kind == "trial_cap_reached"
