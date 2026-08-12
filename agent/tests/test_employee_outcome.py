"""The Employee Outcome Contract (Milestone B).

Generalizes the pattern xai_voice_adapter.run_call already hand-writes three
times: when work doesn't complete, tell the owner why, and never just stop.

The mechanism under test: report_employee_blocked and report_llm_failure both
notify the owner on the FIRST occurrence of a cause, and go quiet on repeats
of the SAME cause — state, not a stream, so a business stuck on one missing
precondition doesn't train the owner to ignore Roster's texts (the alert-
fatigue failure mode named in the design doc's Challenge 1).
"""

from datetime import datetime, timedelta

import employee_outcome
import pytest
from db_models import Business, Event, OwnerNotification
from employee_outcome import report_employee_blocked, report_llm_failure
from sqlmodel import Session, select

OWNER = "+15125550149"
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


def _business(test_engine, **overrides):
    with Session(test_engine) as session:
        fields = {
            "business_name": "Ridgeline HVAC",
            "trade": "hvac",
            "services_json": "[]",
            "hours": "9-5",
            "escalation_phone": OWNER,
            "inbound_number": LINE,
            "email": "outcome-test@test.io",
        }
        fields.update(overrides)
        b = Business(**fields)
        session.add(b)
        session.commit()
        session.refresh(b)
        return b.id


# ---- report_employee_blocked -----------------------------------------------


def test_the_first_block_notifies_the_owner(test_engine, spy):
    bid = _business(test_engine)
    with Session(test_engine) as session:
        business = session.get(Business, bid)
        report_employee_blocked(
            session, business, "reviews", "missing_review_link", "no review link is set"
        )

    assert len(spy.sent) == 1
    assert spy.sent[0]["to"] == OWNER
    assert "review link" in spy.sent[0]["body"].lower()


def test_the_first_block_records_an_owner_notification(test_engine, spy):
    bid = _business(test_engine)
    with Session(test_engine) as session:
        business = session.get(Business, bid)
        report_employee_blocked(session, business, "reviews", "missing_review_link", "detail")
        rows = session.exec(
            select(OwnerNotification).where(OwnerNotification.business_id == bid)
        ).all()

    assert len(rows) == 1
    assert rows[0].delivered is True


def test_a_repeat_of_the_same_cause_does_not_re_notify(test_engine, spy):
    """The whole point: a business stuck on one missing precondition must not
    train the owner to ignore Roster's texts."""
    bid = _business(test_engine)
    with Session(test_engine) as session:
        business = session.get(Business, bid)
        for _ in range(5):
            report_employee_blocked(session, business, "reviews", "missing_review_link", "detail")

    assert len(spy.sent) == 1


def test_a_different_cause_notifies_again(test_engine, spy):
    bid = _business(test_engine)
    with Session(test_engine) as session:
        business = session.get(Business, bid)
        report_employee_blocked(session, business, "reviews", "missing_review_link", "detail")
        report_employee_blocked(session, business, "reviews", "no_callback_number", "detail")

    assert len(spy.sent) == 2


def test_a_different_business_is_independent(test_engine, spy):
    b1 = _business(test_engine, email="a@test.io", inbound_number="+15125551111")
    b2 = _business(test_engine, email="b@test.io", inbound_number="+15125552222")
    with Session(test_engine) as session:
        report_employee_blocked(
            session, session.get(Business, b1), "reviews", "missing_review_link", "detail"
        )
        report_employee_blocked(
            session, session.get(Business, b2), "reviews", "missing_review_link", "detail"
        )

    assert len(spy.sent) == 2


def test_a_different_employee_is_independent(test_engine, spy):
    """membership_agent and reviews both being blocked on their own missing
    precondition are two distinct, separately-actionable facts."""
    bid = _business(test_engine)
    with Session(test_engine) as session:
        business = session.get(Business, bid)
        report_employee_blocked(session, business, "reviews", "missing_review_link", "detail")
        report_employee_blocked(session, business, "membership_agent", "missing_plan", "detail")

    assert len(spy.sent) == 2


def test_a_missing_owner_phone_does_not_crash_the_caller(test_engine, spy):
    """No escalation_phone means Roster has no channel to the owner at all —
    the deepest version of this failure mode. Must not raise; the caller
    (a tick loop processing many businesses) cannot be taken down by one
    business with no phone on file."""
    bid = _business(test_engine, escalation_phone="")
    with Session(test_engine) as session:
        business = session.get(Business, bid)
        report_employee_blocked(session, business, "reviews", "missing_review_link", "detail")

    assert spy.sent == []


def test_blocked_publishes_an_event_for_the_audit_trail(test_engine, spy):
    bid = _business(test_engine)
    with Session(test_engine) as session:
        business = session.get(Business, bid)
        report_employee_blocked(session, business, "reviews", "missing_review_link", "detail")
        events = session.exec(select(Event).where(Event.business_id == bid)).all()

    assert any(e.type == "employee.blocked" for e in events)


# ---- report_llm_failure -----------------------------------------------------


def test_the_first_llm_failure_today_notifies_the_owner(test_engine, spy):
    bid = _business(test_engine)
    with Session(test_engine) as session:
        business = session.get(Business, bid)
        report_llm_failure(session, business, "frontdesk", "APIError: timeout")

    assert len(spy.sent) == 1
    assert spy.sent[0]["to"] == OWNER


def test_a_second_llm_failure_the_same_day_does_not_re_notify(test_engine, spy):
    """Anthropic outages fail many turns in a row. One alert, not one per
    turn, or the owner's phone becomes useless during the exact outage that
    made this alert matter."""
    bid = _business(test_engine)
    with Session(test_engine) as session:
        business = session.get(Business, bid)
        report_llm_failure(session, business, "frontdesk", "error 1")
        report_llm_failure(session, business, "frontdesk", "error 2")

    assert len(spy.sent) == 1


def test_an_llm_failure_the_next_day_notifies_again(test_engine, spy, monkeypatch):
    bid = _business(test_engine)
    with Session(test_engine) as session:
        business = session.get(Business, bid)
        report_llm_failure(session, business, "frontdesk", "error 1")

    monkeypatch.setattr(
        employee_outcome, "_today", lambda: (datetime.utcnow() + timedelta(days=1)).date()
    )
    with Session(test_engine) as session:
        business = session.get(Business, bid)
        report_llm_failure(session, business, "frontdesk", "error 2")

    assert len(spy.sent) == 2


# ---- the owner-alert half of a failed live turn -----------------------------


class _RaisingAgent:
    def respond(self, *a, **k):
        from engine import FALLBACK_REPLY

        return {
            "reply": FALLBACK_REPLY,
            "jobs": [],
            "new_messages": [],
            "pending_tool_call": None,
            "failed": True,
            "error": "APIError: upstream timeout",
        }


def test_a_failed_frontdesk_turn_alerts_the_owner(test_engine, spy, monkeypatch):
    """engine.respond already answered the CUSTOMER honestly. This is the
    other half of the same turn: the owner has to learn their office is
    struggling, or they find out from an angry customer instead."""
    import service as service_module

    monkeypatch.setattr(service_module, "agent", _RaisingAgent())
    bid = _business(test_engine)
    with Session(test_engine) as session:
        business = session.get(Business, bid)

        result = service_module.handle_customer_message(
            session, business, "+15125550001", "my AC died"
        )

    from engine import FALLBACK_REPLY

    assert result["reply"] == FALLBACK_REPLY  # customer answered
    assert len(spy.sent) == 1  # owner told
    assert spy.sent[0]["to"] == OWNER


def test_every_module_that_calls_the_agent_also_reports_a_failure():
    """Structural guard (Milestone B). The engine cannot alert the owner
    itself — it has a ClientConfig, not a session or a Business row — so each
    live caller must report. A caller that runs the agent and ignores
    `failed` re-opens the exact silent-failure hole this milestone closed.
    """
    from pathlib import Path

    agent_dir = Path(__file__).resolve().parent.parent
    offenders = []
    for path in agent_dir.glob("*.py"):
        text = path.read_text()
        if "agent.respond(" not in text:
            continue
        if "report_if_failed" not in text:
            offenders.append(path.name)
    assert offenders == [], "these modules run the agent but never report a failure: " + ", ".join(
        offenders
    )


# ---- B5: structural adoption ------------------------------------------------
#
# The design doc's biggest named risk (Challenge 4): a framework nobody adopts
# is WORSE than eight patches, because it adds a layer and leaves the bugs.
# Adoption has to be enforced by a failing test, not by a docstring. Same
# idiom this codebase already uses for test_tick_deployment_gate's
# TICK_FUNCTIONS and the booking single-writer scan.


def _agent_dir():
    from pathlib import Path

    return Path(__file__).resolve().parent.parent


def test_no_live_reply_handler_returns_bare_none_on_the_trial_cap():
    """Every `if not can_respond(...)` must answer the customer. Returning
    None there is exactly the bug Milestone B closed, and it is a two-line
    change for a future author to reintroduce."""
    offenders = []
    for path in _agent_dir().glob("*.py"):
        lines = path.read_text().splitlines()
        for i, line in enumerate(lines):
            if "if not can_respond(" not in line:
                continue
            # Look at the handful of lines this guard controls.
            window = "\n".join(lines[i : i + 12])
            if "return None" in window or 'return {"reply": None' in window:
                offenders.append(f"{path.name}:{i + 1}")
    assert offenders == [], (
        "a trial-cap branch returns silence instead of answering the customer: "
        + ", ".join(offenders)
    )


def test_every_precondition_skip_reports_rather_than_continuing_silently():
    """The four employees whose missing configuration used to be a bare
    `continue` must all report. Named explicitly rather than pattern-matched:
    this list IS the contract, and adding a fifth employee should mean adding
    a line here."""
    expected = {
        "review_service.py": "missing_review_link",
        "membership_service.py": "missing_membership_plan",
        "referral_service.py": "missing_referral_incentive",
        "dispatcher_service.py": "lead_qualifier_not_deployed",
    }
    missing = []
    for filename, cause in expected.items():
        text = (_agent_dir() / filename).read_text()
        if "report_employee_blocked" not in text or cause not in text:
            missing.append(f"{filename} ({cause})")
    assert missing == [], "these employees can still fail silently: " + ", ".join(missing)


def test_the_engine_never_lets_an_api_failure_escape():
    """engine.respond is the single boundary that keeps a customer from
    receiving nothing when Claude is down. If the try/except around the API
    call is removed, every live caller silently regains the ability to 500."""
    text = (_agent_dir() / "engine.py").read_text()
    assert "FALLBACK_REPLY" in text
    call_site = text.index("self.client.messages.create")
    preceding = text[max(0, call_site - 400) : call_site]
    assert "try:" in preceding, "the Anthropic call is no longer wrapped"
