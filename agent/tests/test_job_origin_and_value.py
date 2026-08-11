"""Job attribution (`origin`) and job value (`value_cents`).

Both exist for one reason: the dashboard could count jobs but never say what
they were worth or which of them would otherwise have been lost — so it could
not show an ROI, and the one number it did show (jobs_booked) was inflated by
one per emergency, because an alert_owner page writes a Job row.

Everything here was found by the 2026-08-10 end-to-end walkthrough.
"""

import json

from bookings import book_job, parse_money_cents, record_escalation
from db_models import (
    ORIGIN_ESCALATION,
    ORIGIN_INBOUND,
    ORIGIN_MISSED_CALL,
    ORIGIN_QUOTE_RECOVERY,
    Business,
    Job,
    Message,
)
from memory import build_customer_context
from metrics import booked_jobs, employee_outcomes
from recovery_engine import clean_service_type
from service import handle_customer_message
from sqlmodel import select


class _FakeAgent:
    """Books one job, says nothing else."""

    def respond(self, client_config, history, tools=None, system_prompt=None, max_iters=None):
        return {
            "reply": "Booked.",
            "jobs": [
                {
                    "id": "t1",
                    "input": {
                        "service_type": "drain clear",
                        "urgency": "routine",
                        "callback_number": "+15552223333",
                    },
                }
            ],
            "new_messages": [],
            "pending_tool_call": None,
        }


def _business(session, **overrides):
    fields = dict(
        business_name="Kestrel",
        trade="plumbing",
        email="o@test.io",
        frontdesk_live=True,
        trial_cap_cents=100000,
    )
    fields.update(overrides)
    b = Business(**fields)
    session.add(b)
    session.commit()
    session.refresh(b)
    return b


# ---- origin: attribution ----------------------------------------------------


def test_a_thread_we_opened_is_a_recovered_missed_call(session, monkeypatch):
    """Roster only ever speaks first on the missed-call text-back, so an
    assistant-first thread IS a recovered missed call. Without this the most
    valuable thing the product does was indistinguishable from a customer who
    happened to text in."""
    import service

    monkeypatch.setattr(service, "agent", _FakeAgent())
    b = _business(session)
    session.add(
        Message(
            business_id=b.id,
            customer_phone="+15552223333",
            role="assistant",
            content_json=json.dumps([{"type": "text", "text": "Sorry we missed your call!"}]),
        )
    )
    session.commit()

    handle_customer_message(session, b, "+15552223333", "my drain is blocked")

    job = session.exec(select(Job).where(Job.business_id == b.id)).first()
    assert job.origin == ORIGIN_MISSED_CALL


def test_a_thread_the_customer_opened_is_ordinary_inbound(session, monkeypatch):
    import service

    monkeypatch.setattr(service, "agent", _FakeAgent())
    b = _business(session)

    handle_customer_message(session, b, "+15552223333", "my drain is blocked")

    job = session.exec(select(Job).where(Job.business_id == b.id)).first()
    assert job.origin == ORIGIN_INBOUND


def test_a_detail_merge_never_relabels_who_earned_the_job(session):
    """log_job fires again mid-conversation with new details; that must merge
    into the existing row without rewriting its attribution, or recovered
    revenue quietly becomes ordinary inbound work."""
    b = _business(session)
    first, created = book_job(
        session,
        b,
        "+15550001111",
        "+15550001111",
        {"service_type": "repipe", "urgency": "routine"},
        origin=ORIGIN_QUOTE_RECOVERY,
    )
    assert created

    again, created_again = book_job(
        session,
        b,
        "+15550001111",
        "+15550001111",
        {"service_type": "repipe", "urgency": "routine", "address": "88 Belvedere"},
        origin=ORIGIN_INBOUND,
    )

    assert created_again is False
    assert again.id == first.id
    assert again.address == "88 Belvedere"  # details did merge
    assert again.origin == ORIGIN_QUOTE_RECOVERY  # attribution did not move


# ---- origin: escalation rows are not work -----------------------------------


def test_paging_the_owner_does_not_count_as_a_booked_job(session):
    """Every emergency added one to the owner's headline number, because
    record_escalation writes a Job row for idempotency."""
    b = _business(session)
    book_job(
        session,
        b,
        "+15550001111",
        "+15550001111",
        {"service_type": "drain clear", "urgency": "routine"},
    )
    record_escalation(session, b, "+15552223333", "+15552223333", "gas smell")

    assert booked_jobs(session, b.id) == 1
    assert employee_outcomes(session, b.id, "frontdesk")["jobs_booked"] == 1


def test_an_escalation_never_makes_a_first_time_caller_look_like_a_regular(session):
    """The page was recalled as "Past jobs with us: Escalated call", so the
    agent greeted a brand-new customer as a returning one and apologised for a
    problem they had never had."""
    b = _business(session)
    record_escalation(session, b, "+15552223333", "+15552223333", "gas smell")

    assert build_customer_context(session, b.id, "+15552223333") == ""


def test_lead_qualifier_ignores_escalation_rows(session):
    """They were being classified high-priority and then handed to Dispatcher,
    which flagged them for review because an alert has no address."""
    import lead_qualifier_service
    from db_models import JobQualification

    b = _business(session)
    record_escalation(session, b, "+15552223333", "+15552223333", "gas smell")

    lead_qualifier_service.qualify_jobs_for_business(session, b)

    assert (
        session.exec(select(JobQualification).where(JobQualification.business_id == b.id)).all()
        == []
    )


def test_escalation_rows_are_labelled_at_the_source(session):
    b = _business(session)
    job, _ = record_escalation(session, b, "+15552223333", "+15552223333", "gas smell")
    assert job.origin == ORIGIN_ESCALATION


# ---- value: what the job was worth ------------------------------------------


def test_owner_typed_amounts_become_cents():
    assert parse_money_cents("1240") == 124000
    assert parse_money_cents("$1,240") == 124000
    assert parse_money_cents("1,240.50") == 124050
    assert parse_money_cents(" 89 ") == 8900


def test_an_unknown_amount_stays_unknown_rather_than_becoming_zero():
    """A silent 0 would land in a revenue total and understate it — the same
    class of error as the escalation rows that overstated the job count."""
    for junk in ("", "   ", "n/a", "abc", "1.2.3", "-50", "0", None):
        assert parse_money_cents(junk) is None, junk


def test_an_absurd_amount_is_rejected_not_recorded():
    """A missing decimal or a pasted phone number must not invent revenue."""
    assert parse_money_cents("99999999") is None


# ---- wording ----------------------------------------------------------------


def test_the_chaser_does_not_say_estimate_twice():
    """Templates supply the noun themselves ("that {service_type} estimate"),
    and service_type is free text the model writes — it logs "whole-house
    repipe estimate", which went out to a customer as "estimate estimate"."""
    assert clean_service_type("whole-house repipe estimate") == "whole-house repipe"
    assert clean_service_type("water heater quote") == "water heater"
    assert clean_service_type("drain clear") == "drain clear"
    assert clean_service_type("estimate") == "estimate"  # never blank it entirely


# ---- value: the route the owner actually clicks ------------------------------


def _complete(test_engine, monkeypatch, value: str):
    """POST "Mark done" the way the console form does, return the saved Job."""
    import app as app_module
    from conftest import DASH_AUTH
    from sqlmodel import Session as _Session
    from starlette.testclient import TestClient

    monkeypatch.setattr(app_module, "engine", test_engine)
    with _Session(test_engine) as s:
        b = _business(s)
        job, _ = book_job(
            s,
            b,
            "+15550001111",
            "+15550001111",
            {"service_type": "drain clear", "urgency": "routine"},
        )
        bid, jid = b.id, job.id

    TestClient(app_module.app, headers=DASH_AUTH).post(
        f"/clients/{bid}/jobs/{jid}/complete", data={"value": value}
    )

    with _Session(test_engine) as s:
        return s.get(Job, jid)


def test_marking_a_job_done_records_what_it_was_worth(test_engine, monkeypatch):
    job = _complete(test_engine, monkeypatch, "$1,240")
    assert job.completed_at is not None
    assert job.value_cents == 124000


def test_marking_a_job_done_without_an_amount_still_completes_it(test_engine, monkeypatch):
    """The amount is optional — requiring it would only get a number invented,
    and the job still needs to complete so Reviews and Quote Chaser fire."""
    job = _complete(test_engine, monkeypatch, "")
    assert job.completed_at is not None
    assert job.value_cents is None
