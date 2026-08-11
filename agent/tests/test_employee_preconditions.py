"""Milestone B, RC1: "hired but cannot work" is now an expressible state.

Four employees used to do nothing, forever, with no log and no alert, while
the dashboard showed their department STAFFED:

    Reviews     — review_link unset          (review_service.py, bare continue)
    Membership  — membership_plan unset      (membership_service.py, ditto)
    Referral    — referral_incentive unset   (referral_service.py, ditto)
    Dispatcher  — lead_qualifier not hired   (dispatcher_service.py, ditto)

That is the worst failure in the product: it looks exactly like working.
These tests assert the owner is told once, and that real work is unaffected.
"""

import employee_outcome
import pytest
import recovery_tick
from db_models import Business, Job, JobQualification, OwnerNotification
from deployment import deploy_role
from sqlmodel import Session, select

OWNER = "+15125550149"
CUSTOMER = "+15125550001"
LINE = "+15125557777"


class Spy:
    def __init__(self):
        self.sent = []

    def send(self, from_number, to_number, body):
        self.sent.append({"to": to_number, "body": body})


@pytest.fixture
def spy(monkeypatch):
    s = Spy()
    monkeypatch.setattr(employee_outcome, "sms_channel", s)
    return s


def _shop(session, **overrides):
    fields = {
        "business_name": "Ridgeline HVAC",
        "trade": "hvac",
        "services_json": "[]",
        "hours": "9-5",
        "escalation_phone": OWNER,
        "inbound_number": LINE,
        "email": "precond@test.io",
        "trial_cap_cents": 100000,
    }
    fields.update(overrides)
    b = Business(**fields)
    session.add(b)
    session.commit()
    session.refresh(b)
    return b


def _completed_job(session, business_id):
    from datetime import datetime, timedelta

    job = Job(
        business_id=business_id,
        customer_phone=CUSTOMER,
        customer_name="Dana",
        service_type="AC repair",
        urgency="routine",
        callback_number=CUSTOMER,
        completed_at=datetime.utcnow() - timedelta(days=30),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def _notification_kinds(session, business_id):
    return [
        n.kind
        for n in session.exec(
            select(OwnerNotification).where(OwnerNotification.business_id == business_id)
        ).all()
    ]


# ---- Reviews ---------------------------------------------------------------


def test_reviews_hired_without_a_review_link_tells_the_owner(test_engine, spy):
    import review_service

    with Session(test_engine) as session:
        shop = _shop(session)  # no review_link
        deploy_role(session, shop.id, "reviews")
        _completed_job(session, shop.id)

        review_service.send_due_review_requests(session)

        assert "employee_blocked" in _notification_kinds(session, shop.id)
    assert len(spy.sent) == 1
    assert "review" in spy.sent[0]["body"].lower()


def test_reviews_with_a_link_is_not_reported_as_blocked(test_engine, spy, monkeypatch):
    import review_service

    monkeypatch.setattr(review_service, "sms_channel", Spy())
    with Session(test_engine) as session:
        shop = _shop(session, review_link="https://g.page/x")
        deploy_role(session, shop.id, "reviews")
        _completed_job(session, shop.id)

        review_service.send_due_review_requests(session)

        assert "employee_blocked" not in _notification_kinds(session, shop.id)
    assert spy.sent == []


def test_reviews_not_hired_at_all_is_silent(test_engine, spy):
    """Not hired is not blocked. A business that never asked for Reviews must
    not be nagged about a review link it has no reason to set."""
    import review_service

    with Session(test_engine) as session:
        shop = _shop(session)
        _completed_job(session, shop.id)

        review_service.send_due_review_requests(session)

    assert spy.sent == []


def test_a_blocked_employee_reports_once_across_many_ticks(test_engine, spy):
    """The alert-fatigue guarantee, at the level that actually matters: the
    scheduler runs hourly forever."""
    import review_service

    with Session(test_engine) as session:
        shop = _shop(session)
        deploy_role(session, shop.id, "reviews")
        _completed_job(session, shop.id)

        for _ in range(10):
            review_service.send_due_review_requests(session)

    assert len(spy.sent) == 1


# ---- Membership ------------------------------------------------------------


def test_membership_hired_without_a_plan_tells_the_owner(test_engine, spy):
    import membership_service

    with Session(test_engine) as session:
        shop = _shop(session)  # no membership_plan
        deploy_role(session, shop.id, "membership_agent")
        job = _completed_job(session, shop.id)
        session.add(
            JobQualification(
                business_id=shop.id,
                source_job_id=job.id,
                job_type="repair",
                financing_candidate=False,
                membership_candidate=True,
                priority="normal",
                possible_spam=False,
                reasoning="t",
            )
        )
        session.commit()

        membership_service.send_due_membership_offers(session)

        assert "employee_blocked" in _notification_kinds(session, shop.id)
    assert len(spy.sent) == 1


# ---- Dispatcher's dependency ----------------------------------------------


def test_dispatcher_hired_without_lead_qualifier_tells_the_owner(test_engine, spy):
    """A dependency failure, not a config one: Dispatcher only ever plans jobs
    Lead Qualifier has already qualified, so hiring it alone means it skips
    every job forever."""
    import dispatcher_service

    with Session(test_engine) as session:
        shop = _shop(session)
        deploy_role(session, shop.id, "dispatcher")  # lead_qualifier NOT deployed
        _completed_job(session, shop.id)

        dispatcher_service.recommend_dispatch(session)

        assert "employee_blocked" in _notification_kinds(session, shop.id)
    assert len(spy.sent) == 1
    assert "lead qualifier" in spy.sent[0]["body"].lower()


def test_dispatcher_with_lead_qualifier_is_not_blocked(test_engine, spy):
    import dispatcher_service

    with Session(test_engine) as session:
        shop = _shop(session)
        deploy_role(session, shop.id, "dispatcher")
        deploy_role(session, shop.id, "lead_qualifier")
        _completed_job(session, shop.id)

        dispatcher_service.recommend_dispatch(session)

        assert "employee_blocked" not in _notification_kinds(session, shop.id)
    assert spy.sent == []


# ---- through the real scheduler --------------------------------------------


def test_the_production_tick_surfaces_a_blocked_employee(test_engine, spy, monkeypatch):
    """recovery_tick.run is what the scheduler actually calls. A guarantee
    that only holds when a service function is called directly is not a
    guarantee."""
    monkeypatch.setattr(recovery_tick, "engine", test_engine)
    monkeypatch.setattr(recovery_tick, "init_db", lambda: None)
    monkeypatch.setattr(recovery_tick, "send_hours_ok", lambda: True)
    with Session(test_engine) as session:
        shop = _shop(session)
        deploy_role(session, shop.id, "reviews")
        _completed_job(session, shop.id)
        shop_id = shop.id

    recovery_tick.run()

    assert len(spy.sent) >= 1
    with Session(test_engine) as session:
        assert "employee_blocked" in _notification_kinds(session, shop_id)


# ---- terminal states must be announced (Milestone B, RC4) -------------------


def test_a_lead_that_goes_cold_tells_the_owner(test_engine, spy, monkeypatch):
    """A Quote Chaser lead that runs the full 28-day sequence with no reply
    became `no_response` and stopped, telling nobody. That is the one moment a
    single phone call from the owner might still save the estimate."""
    from datetime import datetime, timedelta

    import recovery_service
    from db_models import RecoveryCampaign, RecoveryJob
    from recovery_engine import SEQUENCE_DAYS

    with Session(test_engine) as session:
        shop = _shop(session)
        camp = RecoveryCampaign(
            business_id=shop.id,
            face="quote",
            name="t",
            customer_list_json="[]",
            started_at=datetime.utcnow() - timedelta(days=SEQUENCE_DAYS[-1] + 5),
        )
        session.add(camp)
        session.commit()
        session.refresh(camp)
        job = RecoveryJob(
            campaign_id=camp.id,
            business_id=shop.id,
            customer_phone=CUSTOMER,
            customer_name="Dana",
            service_type="AC replacement",
            current_status="pending",
            last_sent_day=SEQUENCE_DAYS[-1],
        )
        session.add(job)
        session.commit()

        recovery_service.tick(session)

        refreshed = session.exec(select(RecoveryJob)).first()
        assert refreshed.current_status == "no_response"
        assert "lead_went_cold" in _notification_kinds(session, shop.id)

    assert len(spy.sent) == 1
    assert "Dana" in spy.sent[0]["body"]


def test_a_cold_lead_is_announced_once_not_every_tick(test_engine, spy):
    from datetime import datetime, timedelta

    import recovery_service
    from db_models import RecoveryCampaign, RecoveryJob
    from recovery_engine import SEQUENCE_DAYS

    with Session(test_engine) as session:
        shop = _shop(session)
        camp = RecoveryCampaign(
            business_id=shop.id,
            face="quote",
            name="t",
            customer_list_json="[]",
            started_at=datetime.utcnow() - timedelta(days=SEQUENCE_DAYS[-1] + 5),
        )
        session.add(camp)
        session.commit()
        session.refresh(camp)
        session.add(
            RecoveryJob(
                campaign_id=camp.id,
                business_id=shop.id,
                customer_phone=CUSTOMER,
                service_type="AC replacement",
                current_status="pending",
                last_sent_day=SEQUENCE_DAYS[-1],
            )
        )
        session.commit()

        for _ in range(5):
            recovery_service.tick(session)

    assert len(spy.sent) == 1
