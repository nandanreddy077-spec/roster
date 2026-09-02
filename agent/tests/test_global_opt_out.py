"""STOP means stop — for every employee, not just the one being replied to.

Found 2026-09-01. STOP was handled in two of six inbound reply handlers
(Quote Chaser, Membership Agent), and even there it only settled the ONE
sequence being replied to. There was no persistent record anywhere, so a
customer who unsubscribed from a quote follow-up still received a review ask,
then a referral ask, then a membership offer — each from a different employee
with no idea the person had asked to be left alone. The reply they got said
"you won't receive further messages", which was not true.

channels.py already asserted the invariant in a comment: "Every employee that
runs an outbound sequence checks the SAME set." The keyword set was shared;
the decision was not. optout.py makes the decision shared too.
"""

from datetime import datetime, timedelta

import optout
import pytest
from db_models import Business, Customer, Employee, Job
from deployment import deploy_role
from repositories import get_or_create_customer

PHONE = "+15125550149"


@pytest.fixture
def business(session):
    biz = Business(
        business_name="Kestrel",
        trade="plumbing",
        inbound_number="+15125557777",
        review_link="https://g.page/r/kestrel",
        referral_incentive="$25 off",
        membership_plan="Comfort Club — $19/month",
    )
    session.add(biz)
    session.commit()
    session.refresh(biz)
    return biz


def _completed_job(session, business, phone=PHONE, days_ago=2):
    job = Job(
        business_id=business.id,
        customer_phone=phone,
        callback_number=phone,
        service_type="Drain cleaning",
        urgency="routine",
        completed_at=datetime.utcnow() - timedelta(days=days_ago),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


# ---- the record itself ------------------------------------------------------


def test_an_opt_out_is_remembered(session, business):
    get_or_create_customer(session, business.id, PHONE)

    assert optout.is_opted_out(session, business.id, PHONE) is False
    optout.record_opt_out(session, business.id, PHONE)
    assert optout.is_opted_out(session, business.id, PHONE) is True


def test_a_second_stop_keeps_the_original_timestamp(session, business):
    """When they FIRST asked is the fact that matters if anyone ever has to
    answer for a message sent after it."""
    get_or_create_customer(session, business.id, PHONE)
    optout.record_opt_out(session, business.id, PHONE)
    first = session.exec(_customers(business.id)).first().opted_out_at

    optout.record_opt_out(session, business.id, PHONE)

    assert session.exec(_customers(business.id)).first().opted_out_at == first


def test_start_resumes(session, business):
    get_or_create_customer(session, business.id, PHONE)
    optout.record_opt_out(session, business.id, PHONE)

    optout.record_opt_in(session, business.id, PHONE)

    assert optout.is_opted_out(session, business.id, PHONE) is False


def test_an_opt_out_is_scoped_to_one_business(session, business):
    """The same handset is a customer at more than one shop. Opting out of one
    is not opting out of another — business isolation, as everywhere else."""
    other = Business(business_name="Rival Plumbing", trade="plumbing")
    session.add(other)
    session.commit()
    session.refresh(other)
    get_or_create_customer(session, business.id, PHONE)
    get_or_create_customer(session, other.id, PHONE)

    optout.record_opt_out(session, business.id, PHONE)

    assert optout.is_opted_out(session, business.id, PHONE) is True
    assert optout.is_opted_out(session, other.id, PHONE) is False


def test_a_number_stored_in_a_different_shape_still_matches(session, business):
    """A Customer row created from a voice booking's transcribed callback
    number is not E.164; Twilio's `From` always is. Comparing raw strings is
    the silent-miss class this codebase has already been bitten by twice."""
    session.add(Customer(business_id=business.id, phone="(512) 555-0149"))
    session.commit()

    optout.record_opt_out(session, business.id, PHONE)

    assert optout.is_opted_out(session, business.id, PHONE) is True


def test_an_unknown_number_has_not_opted_out(session, business):
    """Fails OPEN on absence, deliberately: treating "no record" as "do not
    contact" would silence the missed-call text-back, which is the product."""
    assert optout.is_opted_out(session, business.id, "+15125559999") is False


# ---- every proactive employee honours it ------------------------------------


def _no_sends(monkeypatch, module):
    sent = []
    monkeypatch.setattr(
        module, "sms_channel", type("Spy", (), {"send": lambda self, **kw: sent.append(kw)})()
    )
    return sent


def test_reviews_does_not_text_someone_who_opted_out(session, business, monkeypatch):
    import review_service

    deploy_role(session, business.id, "reviews")
    _completed_job(session, business, days_ago=30)
    get_or_create_customer(session, business.id, PHONE)
    sent = _no_sends(monkeypatch, review_service)

    # Positive control FIRST: without it this test would pass vacuously the
    # day the send stops happening for an unrelated reason, and quietly stop
    # guarding anything. (It caught exactly that while being written.)
    review_service.send_due_review_requests(session)
    assert sent, "no send happened at all — this test would prove nothing"
    sent.clear()

    _reset_review_ask(session, business)
    optout.record_opt_out(session, business.id, PHONE)
    review_service.send_due_review_requests(session)

    assert sent == []


def test_referrals_does_not_text_someone_who_opted_out(session, business, monkeypatch):
    import referral_service

    # Inserted directly: deploy_role refuses Referral while its registry
    # status is "planned" (same reason test_referral_service does this).
    session.add(Employee(business_id=business.id, role_key="referral", display_name="Referral"))
    session.commit()
    _completed_job(session, business, days_ago=30)
    get_or_create_customer(session, business.id, PHONE)
    sent = _no_sends(monkeypatch, referral_service)

    referral_service.send_due_referral_asks(session)
    assert sent, "no send happened at all — this test would prove nothing"
    sent.clear()

    _reset_referral_ask(session, business)
    optout.record_opt_out(session, business.id, PHONE)
    referral_service.send_due_referral_asks(session)

    assert sent == []


def test_the_membership_agent_does_not_text_someone_who_opted_out(session, business, monkeypatch):
    import membership_service
    from db_models import JobQualification, MembershipOffer

    deploy_role(session, business.id, membership_service.ROLE_KEY)
    job = _completed_job(session, business, days_ago=30)
    session.add(
        JobQualification(
            business_id=business.id,
            source_job_id=job.id,
            job_type="repair",
            financing_candidate=False,
            membership_candidate=True,
            priority="normal",
            possible_spam=False,
            reasoning="TEST",
        )
    )
    session.commit()
    get_or_create_customer(session, business.id, PHONE)
    sent = _no_sends(monkeypatch, membership_service)

    membership_service.send_due_membership_offers(session)
    assert sent, "no send happened at all — this test would prove nothing"
    sent.clear()

    # A second offer for the same job is impossible by unique index, so the
    # opt-out half needs a clean slate to be a real test of the gate.
    for offer in session.exec(_select(MembershipOffer)).all():
        session.delete(offer)
    session.commit()
    optout.record_opt_out(session, business.id, PHONE)
    membership_service.send_due_membership_offers(session)

    assert sent == []


def _reset_review_ask(session, business):
    for job in session.exec(_select(Job)).all():
        job.review_requested_at = None
        session.add(job)
    session.commit()


def _reset_referral_ask(session, business):
    for job in session.exec(_select(Job)).all():
        job.referral_sent_at = None
        session.add(job)
    session.commit()


def _select(model):
    from sqlmodel import select

    return select(model)


def _customers(business_id):
    from sqlmodel import select

    return select(Customer).where(Customer.business_id == business_id)
