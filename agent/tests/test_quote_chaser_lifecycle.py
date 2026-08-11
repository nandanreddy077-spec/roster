"""Quote Chaser end to end: an estimate that would have gone cold, recovered.

test_recovery_service.py already covers each behaviour in isolation — decline,
STOP, escalation, slot booking, giving up. What it does not cover is the
JOURNEY those behaviours add up to, or the promise a customer actually
experiences, which is a number:

    a chased lead receives at most one message per sequence day (6 total),
    and NOTHING once they book, decline, unsubscribe, or get escalated.

That number is what makes this sellable. A quote-follow-up employee that
double-texts, or keeps chasing after someone said no, doesn't lose a lead —
it costs the business the customer. So the cap is driven across many ticks,
the way the production scheduler does, rather than asserted once per call.
"""

import json
from datetime import datetime, timedelta

import notifications
import pytest
import recovery_service
from conftest import StubAgent
from db_models import Business, Job, OwnerNotification, RecoveryCampaign, RecoveryJob
from deployment import deploy_role
from recovery_engine import SEQUENCE_DAYS
from sqlmodel import Session, select

PHONE = "+15125556001"


class Spy:
    def __init__(self):
        self.sent = []

    def send(self, from_number, to_number, body):
        self.sent.append({"to": to_number, "body": body})


@pytest.fixture
def chaser(monkeypatch):
    customer, owner = Spy(), Spy()
    monkeypatch.setattr(recovery_service, "sms_channel", customer)
    monkeypatch.setattr(notifications, "_owner_channel", owner)
    return customer, owner


def _business(session, **overrides):
    fields = dict(
        business_name="Ridgeline HVAC",
        trade="hvac",
        services_json=json.dumps(["AC"]),
        hours="9-5",
        pricing_faq="n/a",
        escalation_phone="+15125550149",
        inbound_number="+15125557777",
        trial_cap_cents=100000,
    )
    fields.update(overrides)
    b = Business(**fields)
    session.add(b)
    session.commit()
    session.refresh(b)
    deploy_role(session, b.id, "quote_chaser")
    return b


def _estimate_marked_done(session, business, phone=PHONE):
    job = Job(
        business_id=business.id,
        customer_phone=phone,
        customer_name="Ray Molina",
        service_type="water heater replacement",
        urgency="routine",
        callback_number=phone,
        is_estimate=True,
        completed_at=datetime.utcnow() - timedelta(hours=2),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def _age(session, business_id, days):
    """Move the campaign's clock back so a sequence day comes due. Sequence
    days start at 1, so a freshly enrolled lead is deliberately never texted
    the same day its estimate was marked done."""
    for c in session.exec(
        select(RecoveryCampaign).where(RecoveryCampaign.business_id == business_id)
    ).all():
        c.started_at = datetime.utcnow() - timedelta(days=days)
        session.add(c)
    session.commit()


def _run_whole_sequence(session, business_id, extra_days=10):
    """Walk the campaign through every sequence day and past the end."""
    for day in list(SEQUENCE_DAYS) + [SEQUENCE_DAYS[-1] + extra_days]:
        _age(session, business_id, day)
        for _ in range(3):  # overlapping ticks, as the scheduler can do
            recovery_service.tick(session)


def _stub(monkeypatch, tool_name, tool_input, reply=""):
    monkeypatch.setattr(
        recovery_service,
        "agent",
        StubAgent(
            {
                "reply": reply,
                "jobs": [],
                "new_messages": [],
                "pending_tool_call": {"name": tool_name, "input": tool_input},
            }
        ),
    )


# ---- the journey ----------------------------------------------------------


def test_a_cold_estimate_is_recovered_into_a_booked_job(test_engine, chaser, monkeypatch):
    """The whole money path in one test: marked done -> enrolled -> chased ->
    interested -> slot offered -> slot chosen -> job booked -> owner told."""
    customer, owner = chaser
    with Session(test_engine) as session:
        business = _business(session, email="journey@test.io")
        _estimate_marked_done(session, business)

        assert len(recovery_service.enroll_completed_estimates(session)) == 1
        _age(session, business.id, 1)
        assert len(recovery_service.tick(session)) == 1
        assert len(customer.sent) == 1

        lead = session.exec(select(RecoveryJob)).first()
        _stub(monkeypatch, "record_response", {"intent": "interested"})
        recovery_service.handle_recovery_reply(session, business, lead, "yes still interested")
        session.refresh(lead)
        assert lead.current_status == "awaiting_slot"
        assert lead.offered_slots, "no times were offered to an interested customer"

        _stub(monkeypatch, "confirm_slot", {"slot_index": 0})
        recovery_service.handle_recovery_reply(session, business, lead, "the first one")
        session.refresh(lead)

        assert lead.current_status == "booked"
        booked = session.get(Job, lead.booked_job_id)
        assert booked.service_type == "water heater replacement"
        assert booked.customer_id is not None, "recovered job not linked to a customer"

        assert [
            n
            for n in session.exec(
                select(OwnerNotification).where(
                    OwnerNotification.business_id == business.id,
                    OwnerNotification.source == "recovery_booking",
                )
            ).all()
        ], "owner never told"
        assert any("Quote Chaser" in m["body"] for m in owner.sent)


def test_a_booked_lead_is_never_chased_again(test_engine, chaser, monkeypatch):
    """The most expensive failure: still nagging someone who already booked."""
    customer, _ = chaser
    with Session(test_engine) as session:
        business = _business(session, email="booked@test.io")
        _estimate_marked_done(session, business)
        recovery_service.enroll_completed_estimates(session)
        _age(session, business.id, 1)
        recovery_service.tick(session)

        lead = session.exec(select(RecoveryJob)).first()
        lead.current_status = "awaiting_slot"
        lead.offered_slots_json = json.dumps(["Monday morning", "Tuesday am", "Wed am"])
        session.add(lead)
        session.commit()
        _stub(monkeypatch, "confirm_slot", {"slot_index": 0})
        recovery_service.handle_recovery_reply(session, business, lead, "first one")

        before = len(customer.sent)
        _run_whole_sequence(session, business.id)
        assert len(customer.sent) == before


# ---- the contact cap, stated as a number ----------------------------------


def test_a_silent_lead_receives_the_sequence_once_and_then_nothing(test_engine, chaser):
    customer, _ = chaser
    with Session(test_engine) as session:
        business = _business(session, email="silent@test.io")
        _estimate_marked_done(session, business)
        recovery_service.enroll_completed_estimates(session)

        _run_whole_sequence(session, business.id)

        assert len(customer.sent) <= len(SEQUENCE_DAYS), (
            f"a silent lead got {len(customer.sent)} messages; the sequence is "
            f"{len(SEQUENCE_DAYS)} touches"
        )
        lead = session.exec(select(RecoveryJob)).first()
        assert lead.current_status == "no_response", "never gave up on a silent lead"


def test_overlapping_ticks_never_double_text_the_same_day(test_engine, chaser):
    """The scheduler can fire while a manual run is in flight; last_sent_day is
    claimed with a conditional UPDATE so only one send wins."""
    customer, _ = chaser
    with Session(test_engine) as session:
        business = _business(session, email="race@test.io")
        _estimate_marked_done(session, business)
        recovery_service.enroll_completed_estimates(session)
        _age(session, business.id, 1)

        for _ in range(5):
            recovery_service.tick(session)

        assert len(customer.sent) == 1


@pytest.mark.parametrize("status", ["declined", "escalated", "booked"])
def test_a_resolved_lead_is_never_chased_again(test_engine, chaser, status):
    """Declined, escalated or booked — every terminal state ends the sequence.
    Only 'pending' is eligible, and this pins that it stays true."""
    customer, _ = chaser
    with Session(test_engine) as session:
        business = _business(session, email=f"{status}@test.io")
        _estimate_marked_done(session, business)
        recovery_service.enroll_completed_estimates(session)
        lead = session.exec(select(RecoveryJob)).first()
        lead.current_status = status
        session.add(lead)
        session.commit()

        _run_whole_sequence(session, business.id)

        assert customer.sent == []


def test_one_business_never_chases_another_businesses_lead(test_engine, chaser):
    customer, _ = chaser
    with Session(test_engine) as session:
        a = _business(session, email="a@test.io")
        b = _business(
            session, email="b@test.io", business_name="Other Co", inbound_number="+15125558888"
        )
        _estimate_marked_done(session, a, phone="+15125556002")
        _estimate_marked_done(session, b, phone="+15125556003")
        recovery_service.enroll_completed_estimates(session)

        _age(session, a.id, 1)
        recovery_service.tick(session)

        for lead in session.exec(select(RecoveryJob)).all():
            campaign = session.get(RecoveryCampaign, lead.campaign_id)
            assert campaign.business_id == lead.business_id


# ---- registry ------------------------------------------------------------


def test_quote_chaser_is_live_and_deployable():
    """Graduated internal -> live once the journey above was verified end to
    end against real Claude, and once enrolment was gated on the Employee row
    (before that gate, `live` would have meant a business could be chased
    without hiring anyone)."""
    from employees import REGISTRY

    definition = next(e for e in REGISTRY if e.key == "quote_chaser")
    assert definition.status == "live"
