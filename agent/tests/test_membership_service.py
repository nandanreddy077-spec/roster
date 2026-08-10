"""Membership Agent — the employee that consumes Lead Qualifier's
membership_candidate flag.

Organised by the guarantee under test rather than by function, because the
guarantees are what a reviewer needs to check: never pitch a member, never
pitch twice, never text at 3am, never claim more than we did.
"""
import importlib
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, select

import membership_engine
import membership_service
from conftest import StubAgent
from db_models import (
    Business, Customer, Employee, Job, JobQualification, MembershipOffer,
    OwnerNotification,
)
from deployment import deploy_role
from membership_service import (
    find_active_membership_offer,
    handle_membership_reply,
    send_due_membership_followups,
    send_due_membership_offers,
)

PLAN = "Comfort Club — $19/month, two tune-ups a year plus priority scheduling."


class Spy:
    """Records outbound sends. `fail` makes send() raise, to exercise the
    claim-release path."""

    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def send(self, from_number, to_number, body):
        if self.fail:
            raise RuntimeError("twilio is down")
        self.sent.append({"to": to_number, "body": body})


@pytest.fixture(autouse=True)
def _instant_delays(monkeypatch):
    """Zero delays by default so a test doesn't have to fake a week passing.
    Tests that care about timing set their own completed_at instead."""
    monkeypatch.setenv("MEMBERSHIP_OFFER_DELAY_DAYS", "0")
    monkeypatch.setenv("MEMBERSHIP_FOLLOWUP_DELAY_DAYS", "0")
    importlib.reload(membership_engine)
    importlib.reload(membership_service)
    yield
    monkeypatch.undo()
    importlib.reload(membership_engine)
    importlib.reload(membership_service)


@pytest.fixture
def spy():
    s = Spy()
    membership_service.sms_channel = s
    return s


def _business(session, **overrides):
    fields = dict(
        business_name="Ridgeline HVAC", trade="hvac", services_json="[]", hours="9-5",
        escalation_phone="+15125550149", inbound_number="+15125557777",
        membership_plan=PLAN, trial_cap_cents=100000,
    )
    fields.update(overrides)
    b = Business(**fields)
    session.add(b)
    session.commit()
    session.refresh(b)
    deploy_role(session, b.id, "membership_agent")
    return b


def _candidate_job(session, business, phone="+15125550001", *, candidate=True,
                   completed_days_ago=1, name="Dana Cruz", customer_id=None):
    """A completed job with a Lead Qualifier verdict attached — the exact
    shape Membership Agent triggers on."""
    job = Job(
        business_id=business.id, customer_phone=phone, customer_name=name,
        service_type="AC repair", urgency="routine", callback_number=phone,
        customer_id=customer_id,
        completed_at=datetime.utcnow() - timedelta(days=completed_days_ago),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    session.add(JobQualification(
        business_id=business.id, source_job_id=job.id, job_type="repair",
        financing_candidate=False, membership_candidate=candidate,
        priority="normal", possible_spam=False, reasoning="test",
    ))
    session.commit()
    return job


# ---- it offers, and it offers the owner's own words -------------------------

def test_offers_the_plan_on_a_completed_membership_candidate_job(session, spy):
    b = _business(session)
    _candidate_job(session, b)

    sent = send_due_membership_offers(session)

    assert len(sent) == 1
    assert len(spy.sent) == 1
    assert spy.sent[0]["to"] == "+15125550001"
    # The plan is quoted VERBATIM — the agent never paraphrases a price or a
    # benefit, because it has no way to know if a paraphrase is still true.
    assert PLAN in spy.sent[0]["body"]
    assert "Dana Cruz" in spy.sent[0]["body"] or "Dana" in spy.sent[0]["body"]


def test_the_offer_records_a_row_that_marks_it_sent(session, spy):
    b = _business(session)
    job = _candidate_job(session, b)

    send_due_membership_offers(session)

    offer = session.exec(select(MembershipOffer)).one()
    assert offer.source_job_id == job.id
    assert offer.sent_at is not None
    assert offer.outcome == "pending"


# ---- the four reasons it stays silent --------------------------------------

def test_no_offer_when_lead_qualifier_said_not_a_candidate(session, spy):
    b = _business(session)
    _candidate_job(session, b, candidate=False)

    assert send_due_membership_offers(session) == []
    assert spy.sent == []


def test_no_offer_when_the_owner_has_not_set_a_plan(session, spy):
    """Configuration is required: with no plan text there is nothing honest to
    say, so the employee stays inert rather than inventing an offer."""
    b = _business(session, membership_plan=None)
    _candidate_job(session, b)

    assert send_due_membership_offers(session) == []
    assert spy.sent == []


def test_no_offer_when_the_business_never_hired_the_employee(session, spy):
    """Configuration is not consent — the Employee row is the deployment
    record (ARCHITECTURE.md invariant 8)."""
    b = _business(session)
    employee = session.exec(
        select(Employee).where(Employee.business_id == b.id,
                               Employee.role_key == "membership_agent")
    ).one()
    session.delete(employee)
    session.commit()
    _candidate_job(session, b)

    assert send_due_membership_offers(session) == []
    assert spy.sent == []


def test_firing_the_employee_stops_the_offers(session, spy):
    b = _business(session)
    employee = session.exec(
        select(Employee).where(Employee.business_id == b.id,
                               Employee.role_key == "membership_agent")
    ).one()
    employee.status = "fired"
    session.add(employee)
    session.commit()
    _candidate_job(session, b)

    assert send_due_membership_offers(session) == []
    assert spy.sent == []


def test_no_offer_before_the_delay_has_elapsed(session, spy, monkeypatch):
    """The 7-day default exists to sequence this behind the Reviews and
    Referral asks, so it has to actually hold."""
    monkeypatch.setenv("MEMBERSHIP_OFFER_DELAY_DAYS", "7")
    importlib.reload(membership_engine)
    importlib.reload(membership_service)
    membership_service.sms_channel = spy

    b = _business(session)
    _candidate_job(session, b, completed_days_ago=2)

    assert membership_service.send_due_membership_offers(session) == []
    assert spy.sent == []


# ---- never pitch someone who already has a plan ----------------------------

def test_never_offers_to_a_customer_who_already_has_a_plan(session, spy):
    """The re-check at SEND time, not just at qualification time. Lead
    Qualifier decided a week ago; the customer may have signed up since."""
    b = _business(session)
    customer = Customer(business_id=b.id, phone="+15125550001",
                        plan_notes="Comfort Club member, renews in Sept.")
    session.add(customer)
    session.commit()
    session.refresh(customer)
    _candidate_job(session, b, customer_id=customer.id)

    assert send_due_membership_offers(session) == []
    assert spy.sent == []


def test_one_offer_per_customer_even_across_several_jobs(session, spy):
    """A customer with three completed repairs must be pitched once, not
    three times."""
    b = _business(session)
    _candidate_job(session, b, phone="+15125550001")
    send_due_membership_offers(session)

    _candidate_job(session, b, phone="+15125550001")
    _candidate_job(session, b, phone="+15125550001")
    send_due_membership_offers(session)

    assert len(spy.sent) == 1


# ---- idempotency and the no-duplicate-text guarantee -----------------------

def test_running_the_tick_repeatedly_sends_exactly_one_offer(session, spy):
    b = _business(session)
    _candidate_job(session, b)

    for _ in range(5):
        send_due_membership_offers(session)

    assert len(spy.sent) == 1
    assert len(session.exec(select(MembershipOffer)).all()) == 1


def test_a_failed_send_releases_the_claim_so_the_next_tick_retries(session):
    """The claim row must not survive a failed send — otherwise the customer
    is permanently marked 'offered' having never been texted."""
    failing = Spy(fail=True)
    membership_service.sms_channel = failing
    b = _business(session)
    _candidate_job(session, b)

    assert send_due_membership_offers(session) == []
    assert session.exec(select(MembershipOffer)).all() == []

    working = Spy()
    membership_service.sms_channel = working
    assert len(send_due_membership_offers(session)) == 1
    assert len(working.sent) == 1


def test_a_second_claim_on_the_same_job_is_impossible(session, spy):
    """The database, not the code path, is what guarantees this: source_job_id
    is unique, so a concurrent tick loses the race rather than double-texting."""
    from sqlalchemy.exc import IntegrityError

    b = _business(session)
    job = _candidate_job(session, b)
    send_due_membership_offers(session)

    session.add(MembershipOffer(business_id=b.id, source_job_id=job.id,
                                customer_phone="+15125550001"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


# ---- the follow-up: exactly one, and only while unanswered -----------------

def test_sends_one_follow_up_and_never_a_second(session, spy):
    b = _business(session)
    _candidate_job(session, b)
    send_due_membership_offers(session)

    for _ in range(5):
        send_due_membership_followups(session)

    bodies = [m["body"] for m in spy.sent]
    assert len(bodies) == 2, bodies
    assert "circling back" in bodies[1]
    # The nudge deliberately does NOT repeat the whole plan blurb.
    assert PLAN not in bodies[1]


@pytest.mark.parametrize("outcome", ["accepted", "declined", "question", "unsubscribed"])
def test_no_follow_up_once_the_customer_has_actually_decided(session, spy, outcome):
    b = _business(session)
    _candidate_job(session, b)
    send_due_membership_offers(session)
    offer = session.exec(select(MembershipOffer)).one()
    offer.outcome = outcome
    session.add(offer)
    session.commit()

    assert send_due_membership_followups(session) == []
    assert len(spy.sent) == 1


def test_an_ambiguous_reply_still_gets_the_one_nudge(session, spy):
    """Found by running the real model (2026-08-10): "sounds interesting"
    classifies as `unclear`, which is correct — the customer said neither yes
    nor no. Treating that as settled would drop the nudge on the warmest lead
    the sequence produces. Reviews makes the same exception for the same
    reason."""
    b = _business(session)
    _candidate_job(session, b)
    send_due_membership_offers(session)
    offer = session.exec(select(MembershipOffer)).one()
    offer.outcome = "unclear"
    session.add(offer)
    session.commit()

    assert len(send_due_membership_followups(session)) == 1
    assert len(spy.sent) == 2
    assert "circling back" in spy.sent[1]["body"]


def test_an_ambiguous_reply_does_not_keep_burning_model_calls(session, spy):
    """The other half of the split: `unclear` stays nudge-eligible but stops
    routing, so a chatty customer is classified once, not once per text."""
    b = _business(session)
    _candidate_job(session, b)
    send_due_membership_offers(session)
    offer = session.exec(select(MembershipOffer)).one()
    offer.outcome = "unclear"
    session.add(offer)
    session.commit()

    assert find_active_membership_offer(session, b.id, "+15125550001") is None


def test_no_follow_up_for_a_claim_that_never_actually_sent(session):
    """sent_at is None on a claim whose send failed. Nudging about a message
    the customer never received would be incoherent."""
    b = _business(session)
    job = _candidate_job(session, b)
    session.add(MembershipOffer(business_id=b.id, source_job_id=job.id,
                                customer_phone="+15125550001", sent_at=None))
    session.commit()
    spy = Spy()
    membership_service.sms_channel = spy

    assert send_due_membership_followups(session) == []
    assert spy.sent == []


# ---- replies ---------------------------------------------------------------

def _offer_sent(session, spy):
    b = _business(session)
    _candidate_job(session, b)
    send_due_membership_offers(session)
    return b, session.exec(select(MembershipOffer)).one()


def _stub(intent):
    return StubAgent({
        "reply": None,
        "pending_tool_call": {"name": "record_membership_reply",
                              "input": {"intent": intent}},
    })


def test_an_accepted_offer_records_the_plan_and_tells_the_owner(session, spy):
    b, offer = _offer_sent(session, spy)
    membership_service.agent = _stub("accepted")
    owner_texts = Spy()
    import notifications
    notifications._owner_channel = owner_texts

    reply = handle_membership_reply(session, b, offer, "yes please, sign me up")

    session.refresh(offer)
    assert offer.outcome == "accepted"
    assert offer.raw_reply_text == "yes please, sign me up"
    # THE HANDOFF: plan_notes is what passes this customer to Retention
    # Manager and stops Lead Qualifier flagging them again.
    customer = session.exec(
        select(Customer).where(Customer.phone == "+15125550001")).one()
    assert customer.plan_notes and "maintenance plan" in customer.plan_notes
    note = session.exec(
        select(OwnerNotification).where(
            OwnerNotification.kind == "membership_accepted")).one()
    assert "+15125550001" in note.message
    # Honest wording: we recorded a yes, we did not take payment.
    assert "wants to sign up" in note.message
    assert "passed it to the office" in reply


def test_a_declined_offer_is_settled_and_nobody_is_paged(session, spy):
    b, offer = _offer_sent(session, spy)
    membership_service.agent = _stub("declined")

    reply = handle_membership_reply(session, b, offer, "no thanks")

    session.refresh(offer)
    assert offer.outcome == "declined"
    assert session.exec(select(OwnerNotification)).all() == []
    assert "No problem" in reply


def test_a_question_goes_to_a_human_and_the_agent_answers_nothing(session, spy):
    """The agent has no way to know whether the owner's plan blurb is current
    or applies to this customer, so it never answers from it."""
    b, offer = _offer_sent(session, spy)
    membership_service.agent = _stub("question")
    import notifications
    notifications._owner_channel = Spy()

    reply = handle_membership_reply(session, b, offer, "does that cover the water heater too?")

    session.refresh(offer)
    assert offer.outcome == "question"
    note = session.exec(
        select(OwnerNotification).where(
            OwnerNotification.kind == "escalation")).one()
    assert "maintenance plan" in note.message
    assert "$19" not in reply and "Comfort Club" not in reply


def test_stop_unsubscribes_without_spending_a_model_call(session, spy):
    b, offer = _offer_sent(session, spy)

    class Exploding:
        def respond(self, *a, **k):
            raise AssertionError("STOP must never reach the model")

    membership_service.agent = Exploding()

    reply = handle_membership_reply(session, b, offer, "STOP")

    session.refresh(offer)
    assert offer.outcome == "unsubscribed"
    assert "unsubscribed" in reply


def test_a_model_intent_outside_the_schema_is_treated_as_unclear(session, spy):
    """A hallucinated intent must not leave the offer pending forever, and
    must not crash the webhook."""
    b, offer = _offer_sent(session, spy)
    membership_service.agent = _stub("definitely_maybe")

    handle_membership_reply(session, b, offer, "hmm")

    session.refresh(offer)
    assert offer.outcome == "unclear"


def test_past_the_trial_cap_the_reply_is_kept_but_not_classified(session, spy):
    b, offer = _offer_sent(session, spy)
    b.trial_spend_cents = 999999
    session.add(b)
    session.commit()

    class Exploding:
        def respond(self, *a, **k):
            raise AssertionError("must not spend past the cap")

    membership_service.agent = Exploding()

    reply = handle_membership_reply(session, b, offer, "sure why not")

    session.refresh(offer)
    assert reply is None
    assert offer.raw_reply_text == "sure why not"
    # Settled, so the scheduled nudge never reaches someone who did answer.
    assert offer.outcome == "unclear"


# ---- routing ---------------------------------------------------------------

def test_a_reply_routes_to_the_offer_only_while_it_is_unanswered(session, spy):
    b, offer = _offer_sent(session, spy)
    assert find_active_membership_offer(session, b.id, "+15125550001") is not None

    offer.outcome = "declined"
    session.add(offer)
    session.commit()

    assert find_active_membership_offer(session, b.id, "+15125550001") is None


def test_a_reply_long_after_the_offer_is_not_routed_here(session, spy):
    """Unbounded matching is how an unrelated text months later gets misrouted
    into a sales classifier forever."""
    b, offer = _offer_sent(session, spy)
    offer.sent_at = datetime.utcnow() - timedelta(days=40)
    session.add(offer)
    session.commit()

    assert find_active_membership_offer(session, b.id, "+15125550001") is None


def test_offers_never_leak_across_businesses(session, spy):
    b, offer = _offer_sent(session, spy)
    other = _business(session, email="other@test.io", business_name="Other Co")

    assert find_active_membership_offer(session, other.id, "+15125550001") is None


# ---- metrics ---------------------------------------------------------------

def test_metrics_count_sent_offers_and_acceptances_separately(session, spy):
    import metrics

    b, offer = _offer_sent(session, spy)
    outcomes = metrics.employee_outcomes(session, b.id, "membership_agent")
    assert outcomes[metrics.MEMBERSHIP_OFFERS_SENT] == 1
    assert outcomes[metrics.MEMBERSHIPS_ACCEPTED] == 0

    offer.outcome = "accepted"
    session.add(offer)
    session.commit()

    outcomes = metrics.employee_outcomes(session, b.id, "membership_agent")
    assert outcomes[metrics.MEMBERSHIPS_ACCEPTED] == 1
    rows = metrics.employee_activity(session, b.id, "membership_agent")
    assert rows and all(r.when is not None for r in rows)


def test_an_unsent_claim_is_never_reported_as_activity(session):
    """A claim whose send failed is not something the customer experienced,
    so it must not appear in the owner's numbers."""
    import metrics

    b = _business(session)
    job = _candidate_job(session, b)
    session.add(MembershipOffer(business_id=b.id, source_job_id=job.id,
                                customer_phone="+15125550001", sent_at=None))
    session.commit()

    outcomes = metrics.employee_outcomes(session, b.id, "membership_agent")
    assert outcomes[metrics.MEMBERSHIP_OFFERS_SENT] == 0
    assert metrics.employee_activity(session, b.id, "membership_agent") == []


# ---- the handoff to the rest of the workforce ------------------------------

def test_accepting_stops_lead_qualifier_flagging_the_customer_again(session, spy):
    """The loop closes: the same plan_notes write that hands the customer to
    Retention Manager is what makes Lead Qualifier stop nominating them."""
    from lead_qualifier_engine import classify_membership_candidate
    from lead_qualifier_rules import REASON_MEMBERSHIP_HAS_PLAN

    b, offer = _offer_sent(session, spy)
    membership_service.agent = _stub("accepted")
    import notifications
    notifications._owner_channel = Spy()
    handle_membership_reply(session, b, offer, "yes")

    customer = session.exec(
        select(Customer).where(Customer.phone == "+15125550001")).one()
    candidate, reason = classify_membership_candidate("repair", customer)
    assert candidate is False
    assert reason == REASON_MEMBERSHIP_HAS_PLAN
