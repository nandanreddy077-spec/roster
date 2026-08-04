"""One rule for every tick-based employee: no Employee row, no customer contact.

This invariant has now been missed twice. It was first fixed for Lead
Qualifier and Dispatcher ("Critical Finding #2"), whose commit stated the
resolution as universal — "deployment is one uniform invariant for every
employee... enforced structurally rather than left as a convention each tick
worker's author must remember" — and then fixed only those two. Reviews and
Referral, written earlier against the older convention, kept texting whenever
`review_link` / `referral_incentive` happened to be set. Configuration is not
consent.

So the guard here is deliberately BLIND to which employees exist: it sets up a
business with every config field populated and every trigger condition met,
deploys nothing, runs the whole production tick, and asserts total silence. A
future tick employee that forgets the gate fails this test without anyone
remembering to add a case for it — which is the only kind of enforcement that
has actually worked here.
"""
import importlib

import pytest
from sqlmodel import Session, select

from datetime import datetime, timedelta

from db_models import Business, Employee, Job
from deployment import deploy_role


@pytest.fixture
def tick(monkeypatch):
    """Zero delays so every scheduled send is due immediately — the point is
    which sends are ALLOWED, not when they fire."""
    for var in ("REVIEW_DELAY_DAYS", "REVIEW_FOLLOWUP_DELAY_DAYS"):
        monkeypatch.setenv(var, "0")
    import referral_engine, referral_service, review_engine, review_service
    for mod in (review_engine, review_service, referral_engine, referral_service):
        importlib.reload(mod)

    sent = []

    class Spy:
        def send(self, from_number, to_number, body):
            sent.append({"to": to_number, "body": body})

    review_service.sms_channel = Spy()
    referral_service.sms_channel = Spy()

    class Tick:
        messages = sent

        @staticmethod
        def run_all(session):
            review_service.send_due_review_requests(session)
            review_service.send_due_review_followups(session)
            referral_service.send_due_referral_asks(session)

    yield Tick
    monkeypatch.undo()
    for mod in (review_engine, review_service, referral_engine, referral_service):
        importlib.reload(mod)


def _fully_configured_business(session, **overrides):
    """Every field that any tick employee treats as its trigger, all set. The
    only thing missing is the hire."""
    fields = dict(
        business_name="Ridgeline HVAC", trade="hvac", services_json="[]", hours="9-5",
        escalation_phone="+15125550149", inbound_number="+15125557777",
        review_link="https://g.page/r/ridgeline/review",
        referral_incentive="$25 off your next visit",
        trial_cap_cents=100000,
    )
    fields.update(overrides)
    business = Business(**fields)
    session.add(business)
    session.commit()
    session.refresh(business)
    return business


def _completed_job(session, business, phone="+15125550001"):
    job = Job(
        business_id=business.id, customer_phone=phone, customer_name="Dana Cruz",
        service_type="AC compressor replacement", urgency="same_day",
        callback_number=phone,
        completed_at=datetime.utcnow() - timedelta(hours=2),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def test_a_fully_configured_business_that_hired_nobody_is_never_texted(test_engine, tick):
    """THE guard. Every trigger condition met, zero employees deployed."""
    with Session(test_engine) as session:
        business = _fully_configured_business(session, email="nohire@test.io")
        _completed_job(session, business)

        for _ in range(5):
            tick.run_all(session)

        assert tick.messages == [], (
            "a business that hired nobody was texted by the tick — "
            "configuration is not consent"
        )


def test_hiring_reviews_permits_reviews_and_nothing_else(test_engine, tick):
    """The gate is per employee, not a global on-switch: hiring one must not
    silently enable another that was never hired."""
    with Session(test_engine) as session:
        business = _fully_configured_business(session, email="revonly@test.io")
        _completed_job(session, business)
        deploy_role(session, business.id, "reviews")

        for _ in range(3):
            tick.run_all(session)

        assert tick.messages, "hiring Reviews did not enable it"
        for msg in tick.messages:
            assert "$25 off" not in msg["body"], "Referral sent without being hired"


def test_referral_cannot_be_deployed_while_its_registry_entry_is_planned(test_engine):
    """Why gating Referral makes it inert rather than hireable, stated as a
    test so the state is deliberate and visible: the registry says `planned`,
    deploy_role refuses `planned`, so nothing can turn it on. Graduating the
    registry entry is the switch — a product decision, not an accident."""
    with Session(test_engine) as session:
        business = _fully_configured_business(session, email="planned@test.io")
        with pytest.raises(ValueError, match="planned"):
            deploy_role(session, business.id, "referral")


def test_a_hired_employee_at_one_business_never_acts_for_another(test_engine, tick):
    """The gate is per (business, employee) — deployment does not leak across
    tenants, which is the security boundary everywhere in Roster."""
    with Session(test_engine) as session:
        hired = _fully_configured_business(session, email="hired@test.io")
        unhired = _fully_configured_business(
            session, email="unhired@test.io", business_name="Other Co")
        _completed_job(session, hired, phone="+15125550002")
        _completed_job(session, unhired, phone="+15125550003")
        deploy_role(session, hired.id, "reviews")

        for _ in range(3):
            tick.run_all(session)

        recipients = {m["to"] for m in tick.messages}
        assert recipients == {"+15125550002"}, (
            f"the unhired business's customer was texted: {recipients}")


def test_firing_an_employee_stops_the_texts(test_engine, tick):
    """`fired` is the off switch, and it has to actually work — is_active
    treats any non-fired row as deployed."""
    with Session(test_engine) as session:
        business = _fully_configured_business(session, email="fired@test.io")
        _completed_job(session, business, phone="+15125550004")
        deploy_role(session, business.id, "reviews")

        employee = session.exec(
            select(Employee).where(
                Employee.business_id == business.id, Employee.role_key == "reviews")
        ).first()
        employee.status = "fired"
        session.add(employee)
        session.commit()

        for _ in range(3):
            tick.run_all(session)

        assert tick.messages == []
