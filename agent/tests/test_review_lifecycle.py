"""Reviews, end to end: the whole customer journey, one contact at a time.

The promise this file exists to keep is a NUMBER, not a feeling — a customer
receives at most:

    1 review request  +  1 follow-up nudge (only if they never replied)

and never anything after they answer, whether they were happy or furious.
Getting that wrong doesn't produce a bug report, it produces a business
texting its own customers repeatedly after one job. So each guarantee is
pinned by driving the real tick functions repeatedly, the way the production
scheduler does (recovery_tick.run on a timer), rather than asserting once
against a single call.

Delays are collapsed to zero via the env overrides review_engine reads, so a
journey that spans days in production runs in one test.
"""

import importlib
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, select

from db_models import Business, Job, ReviewReply
from deployment import deploy_role

CUSTOMER = "+15125550001"


@pytest.fixture
def reviews(monkeypatch):
    """review_engine reads its delays at import time, so they're set before the
    modules are (re)loaded — the same way production reads them at boot."""
    monkeypatch.setenv("REVIEW_DELAY_DAYS", "0")
    monkeypatch.setenv("REVIEW_FOLLOWUP_DELAY_DAYS", "0")
    import review_engine
    import review_service

    importlib.reload(review_engine)
    importlib.reload(review_service)
    sent = []

    class Spy:
        def send(self, from_number, to_number, body):
            sent.append({"to": to_number, "body": body})

    review_service.sms_channel = Spy()
    review_service.sent_log = sent
    yield review_service
    # Restore real delays for every other test in the session.
    monkeypatch.undo()
    importlib.reload(review_engine)
    importlib.reload(review_service)


def _business(session, **overrides):
    fields = dict(
        business_name="Ridgeline HVAC",
        trade="hvac",
        services_json="[]",
        hours="9-5",
        escalation_phone="+15125550149",
        inbound_number="+15125557777",
        review_link="https://g.page/r/ridgeline/review",
        trial_cap_cents=100000,
    )
    fields.update(overrides)
    b = Business(**fields)
    session.add(b)
    session.commit()
    session.refresh(b)
    deploy_role(session, b.id, "reviews")  # hired, so the tick is allowed to send
    return b


def _completed_job(session, business, phone=CUSTOMER, hours_ago=2):
    job = Job(
        business_id=business.id,
        customer_phone=phone,
        customer_name="Dana Cruz",
        service_type="AC compressor replacement",
        urgency="same_day",
        callback_number=phone,
        completed_at=datetime.utcnow() - timedelta(hours=hours_ago),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def _reply(session, business, job, text, outcome):
    """A classified customer reply, as handle_review_reply would persist it."""
    session.add(
        ReviewReply(
            business_id=business.id,
            source_job_id=job.id,
            customer_phone=job.callback_number,
            outcome=outcome,
            raw_reply_text=text,
        )
    )
    session.commit()


def _run_ticks(reviews, session, times=3):
    """Drive the scheduler repeatedly — production runs this on a timer, so
    'sends once' has to mean once across many ticks, not once per call."""
    for _ in range(times):
        reviews.send_due_review_requests(session)
        reviews.send_due_review_followups(session)


def _to(reviews, phone=CUSTOMER):
    return [m for m in reviews.sent_log if m["to"] == phone]


def _nudges(reviews, phone=CUSTOMER):
    """Split by the templates' own literal opening, taken up to the first
    placeholder, so the split survives copy edits without hardcoding wording."""
    from review_engine import REVIEW_FOLLOWUP_MESSAGE_TEMPLATE

    marker = REVIEW_FOLLOWUP_MESSAGE_TEMPLATE.split("{")[0]
    return [m for m in _to(reviews, phone) if m["body"].startswith(marker)]


def _requests(reviews, phone=CUSTOMER):
    nudges = _nudges(reviews, phone)
    return [m for m in _to(reviews, phone) if m not in nudges]


# ---- the initial request ---------------------------------------------------


def test_a_completed_job_gets_exactly_one_review_request(test_engine, reviews):
    """Many ticks, one ask. The nudge is counted separately below — this pins
    that the REQUEST itself never repeats."""
    with Session(test_engine) as session:
        business = _business(session, email="one@test.io")
        _completed_job(session, business)

        _run_ticks(reviews, session, times=5)

        assert len(_requests(reviews)) == 1, "customer was asked more than once"


def test_an_unhired_business_never_texts_at_all(test_engine, reviews):
    """review_link is configuration, not consent — the Employee row is."""
    with Session(test_engine) as session:
        business = Business(
            business_name="Not Hired Co",
            trade="hvac",
            services_json="[]",
            hours="9-5",
            email="unhired@test.io",
            review_link="https://g.page/r/x/review",
            inbound_number="+15125557777",
        )
        session.add(business)
        session.commit()
        session.refresh(business)
        _completed_job(session, business)

        _run_ticks(reviews, session, times=3)

        assert _to(reviews) == []


# ---- the one follow-up, only for silence -----------------------------------


def test_a_silent_customer_gets_one_nudge_and_never_a_second(test_engine, reviews):
    with Session(test_engine) as session:
        business = _business(session, email="silent@test.io")
        _completed_job(session, business)

        _run_ticks(reviews, session, times=6)

        msgs = _to(reviews)
        assert len(msgs) == 2, f"expected request + one nudge, got {len(msgs)}"
        assert msgs[0]["body"] != msgs[1]["body"]
        assert "again" in msgs[1]["body"].lower()


def test_the_total_contact_count_is_capped_at_two_forever(test_engine, reviews):
    """The headline guarantee, stated as the number it is."""
    with Session(test_engine) as session:
        business = _business(session, email="cap@test.io")
        _completed_job(session, business)

        _run_ticks(reviews, session, times=20)

        assert len(_to(reviews)) <= 2


# ---- never contacted again after replying ----------------------------------


@pytest.mark.parametrize(
    "outcome,text",
    [
        ("left_review", "just left you 5 stars!"),
        ("positive", "yeah you guys were great"),
        ("negative", "the tech was 3 hours late and it still doesn't work"),
        ("declined", "no thanks"),
    ],
)
def test_a_customer_who_replied_is_never_contacted_again(test_engine, reviews, outcome, text):
    """Happy or furious, answering ends the sequence. Re-nudging someone who
    just complained is the single worst thing this employee could do."""
    with Session(test_engine) as session:
        business = _business(session, email=f"{outcome}@test.io")
        job = _completed_job(session, business)

        reviews.send_due_review_requests(session)
        assert len(_to(reviews)) == 1
        _reply(session, business, job, text, outcome)

        _run_ticks(reviews, session, times=10)

        assert len(_to(reviews)) == 1, f"{outcome!r} customer was contacted again"


def test_an_unclear_reply_still_allows_the_one_scheduled_nudge(test_engine, reviews):
    """ "unclear" is the one outcome that told us nothing, so the already-
    scheduled nudge still goes — but it is still only ever one."""
    with Session(test_engine) as session:
        business = _business(session, email="unclear@test.io")
        job = _completed_job(session, business)

        reviews.send_due_review_requests(session)
        _reply(session, business, job, "k", "unclear")

        _run_ticks(reviews, session, times=8)

        assert len(_to(reviews)) == 2


def test_replying_after_the_nudge_stops_everything(test_engine, reviews):
    with Session(test_engine) as session:
        business = _business(session, email="late@test.io")
        job = _completed_job(session, business)

        _run_ticks(reviews, session, times=3)
        assert len(_to(reviews)) == 2
        _reply(session, business, job, "left one, thanks", "left_review")

        _run_ticks(reviews, session, times=10)

        assert len(_to(reviews)) == 2


# ---- separate jobs are separate conversations ------------------------------


def test_a_second_job_for_the_same_customer_earns_its_own_request(test_engine, reviews):
    """Two jobs months apart are two experiences worth asking about — the cap
    is per job, not a lifetime gag."""
    with Session(test_engine) as session:
        business = _business(session, email="twojobs@test.io")
        first = _completed_job(session, business)
        reviews.send_due_review_requests(session)
        _reply(session, business, first, "5 stars", "left_review")

        _completed_job(session, business)
        _run_ticks(reviews, session, times=3)

        # One request per job. The second job also earns its own nudge, since
        # nobody replied to it — that is the per-job cap working, not a leak.
        assert len(_requests(reviews)) == 2
        assert len(_nudges(reviews)) == 1


def test_one_business_never_texts_another_businesses_customer(test_engine, reviews):
    """Tenant isolation is the security boundary everywhere in Roster."""
    with Session(test_engine) as session:
        hired = _business(session, email="hired@test.io")
        other = _business(session, email="other@test.io", business_name="Other Co")
        _completed_job(session, hired, phone="+15125550002")
        _completed_job(session, other, phone="+15125550003")

        _run_ticks(reviews, session, times=2)

        for msg in reviews.sent_log:
            assert msg["to"] in ("+15125550002", "+15125550003")
        assert len(_to(reviews, "+15125550002")) == 2
        assert len(_to(reviews, "+15125550003")) == 2


# ---- the owner-facing escalation text --------------------------------------


def test_a_negative_reply_escalation_has_no_double_period():
    """The reason already ends in a sentence; the template supplies its own.
    Rendered "...review request.. Call them back immediately." to the owner."""
    from notifications import build_escalation_message

    msg = build_escalation_message(
        Business(business_name="Ridgeline HVAC"),
        "+15125550001",
        "Customer replied negatively to a review request.",
    )
    assert ".." not in msg
    assert "review request. Call them back immediately." in msg


def test_a_model_authored_reason_is_normalized_too():
    """alert_owner and recovery pass whatever the model wrote — no call site
    controls that, which is why this is fixed in the builder."""
    from notifications import build_escalation_message

    for reason in (
        "gas leak, kids in the home.",
        "gas leak, kids in the home",
        "gas leak, kids in the home...",
        "gas leak, kids in the home . ",
    ):
        msg = build_escalation_message(Business(business_name="B"), "+1", reason)
        assert ".." not in msg
        assert "Reason: gas leak, kids in the home. Call them back" in msg


def test_an_empty_reason_does_not_render_a_stray_period():
    from notifications import build_escalation_message

    msg = build_escalation_message(Business(business_name="B"), "+1", "")
    assert ".." not in msg
