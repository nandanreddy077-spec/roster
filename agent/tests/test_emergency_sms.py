"""Emergency escalation over SMS.

Before this, the SMS prompt told the model to say "I'm alerting someone
immediately" while service.py exposed only LOG_JOB_TOOL — so a gas-leak text
produced an ordinary "Frontdesk just booked a job" notification, visually
identical to a routine AC call, and `alert_owner` did not exist on the channel
at all. These tests pin the promise to the mechanism: if Frontdesk says the
owner was alerted, an urgent page actually went out, or the customer is told
plainly that it didn't.
"""

from datetime import datetime, timedelta

from sqlmodel import Session, select

import notifications
import service
from bookings import ESCALATION_SERVICE_TYPE, record_escalation
from db_models import Business, Job, OwnerNotification
from engine import LOG_JOB_TOOL, TRANSFER_CALL_TOOL, build_system_prompt
from notifications import KIND_ESCALATION, SOURCE_ALERT_OWNER

EMERGENCY_TEXT = "I smell gas near my furnace and my kids are home"


class SpyChannel:
    def __init__(self, explode=False):
        self.sent = []
        self._explode = explode

    def send(self, from_number, to_number, body):
        if self._explode:
            raise RuntimeError("twilio down")
        self.sent.append({"to": to_number, "body": body})


class EscalatingAgent:
    """Model turn that calls alert_owner and log_job together — the shape
    engine.respond returns for an emergency (it captures the job, then stops
    the loop and hands the passthrough tool back to the caller)."""

    def __init__(self, reason="gas leak, children in home", with_job=True):
        self._reason = reason
        self._with_job = with_job

    def respond(self, client_config, history, tools=None, system_prompt=None, max_iters=None):
        self.tools_seen = tools
        jobs = []
        if self._with_job:
            jobs = [
                {
                    "id": "t1",
                    "input": {
                        "service_type": "Gas smell from furnace",
                        "urgency": "emergency",
                    },
                }
            ]
        return {
            "reply": "I'm alerting the team right now. Please get outside and call 911.",
            "jobs": jobs,
            "new_messages": [],
            "pending_tool_call": {"name": "alert_owner", "input": {"reason": self._reason}},
        }


def _business(session, **overrides):
    fields = dict(
        business_name="Ridgeline HVAC",
        trade="hvac",
        services_json="[]",
        hours="9-5",
        escalation_phone="+15125550149",
        inbound_number="+15125557777",
        frontdesk_live=True,
        trial_cap_cents=100000,
    )
    fields.update(overrides)
    b = Business(**fields)
    session.add(b)
    session.commit()
    session.refresh(b)
    return b


def _run(
    session,
    client,
    monkeypatch,
    agent=None,
    phone="+15125559999",
    channel=None,
    text=EMERGENCY_TEXT,
):
    monkeypatch.setattr(service, "agent", agent or EscalatingAgent())
    monkeypatch.setattr(notifications, "_owner_channel", channel or SpyChannel())
    return service.handle_customer_message(session, client, phone, text)


# ---- the tool is actually reachable on SMS ---------------------------------


def test_sms_prompt_no_longer_promises_an_alert_it_cannot_send():
    """The old prompt said "tell the customer you're alerting someone" with no
    alert_owner tool exposed. Whatever the wording, the promise and the tool
    must now appear together."""
    from models import ClientConfig

    prompt = build_system_prompt(
        ClientConfig(
            client_id="1",
            business_name="B",
            trade="hvac",
            services=["AC"],
            hours="9-5",
            pricing_faq="",
            escalation_phone="+15125550149",
        )
    )
    assert "alert_owner" in prompt


def test_sms_turn_exposes_both_tools(test_engine, monkeypatch):
    with Session(test_engine) as session:
        client = _business(session, email="tools@test.io")
        agent = EscalatingAgent()
        _run(session, client, monkeypatch, agent=agent)
        names = [t["name"] for t in agent.tools_seen]
        assert names == [LOG_JOB_TOOL["name"], TRANSFER_CALL_TOOL["name"]]


# ---- the page actually goes out -------------------------------------------


def test_emergency_sms_sends_an_urgent_page_not_a_booking_notice(test_engine, monkeypatch):
    with Session(test_engine) as session:
        client = _business(session, email="urgent@test.io")
        spy = SpyChannel()
        _run(session, client, monkeypatch, channel=spy)

        urgent = [m for m in spy.sent if m["body"].startswith("URGENT")]
        assert len(urgent) == 1, spy.sent
        assert "+15125559999" in urgent[0]["body"]
        assert urgent[0]["to"] == "+15125550149"


def test_emergency_sms_records_an_escalation_notification(test_engine, monkeypatch):
    with Session(test_engine) as session:
        client = _business(session, email="notif@test.io")
        _run(session, client, monkeypatch)

        kinds = [
            n.kind
            for n in session.exec(
                select(OwnerNotification).where(OwnerNotification.business_id == client.id)
            ).all()
        ]
        assert KIND_ESCALATION in kinds

        row = session.exec(
            select(OwnerNotification).where(
                OwnerNotification.business_id == client.id,
                OwnerNotification.kind == KIND_ESCALATION,
            )
        ).first()
        assert row.source == SOURCE_ALERT_OWNER
        assert row.delivered is True


def test_emergency_sms_persists_the_escalation_job_and_stamps_the_page(test_engine, monkeypatch):
    with Session(test_engine) as session:
        client = _business(session, email="job@test.io")
        _run(session, client, monkeypatch)

        job = session.exec(
            select(Job).where(
                Job.business_id == client.id,
                Job.service_type == ESCALATION_SERVICE_TYPE,
            )
        ).first()
        assert job is not None
        assert job.urgency == "emergency"
        assert job.owner_alerted_at is not None
        assert job.customer_id is not None


def test_the_lead_is_still_captured_alongside_the_escalation(test_engine, monkeypatch):
    """engine.respond captures a same-turn log_job before breaking on the
    passthrough tool; an emergency caller must never be lost."""
    with Session(test_engine) as session:
        client = _business(session, email="lead@test.io")
        result = _run(session, client, monkeypatch)

        assert [j.service_type for j in result["jobs"]] == ["Gas smell from furnace"]
        assert result["jobs"][0].urgency == "emergency"


# ---- honesty when the page fails ------------------------------------------


def test_a_failed_page_tells_the_customer_and_gives_the_real_number(test_engine, monkeypatch):
    with Session(test_engine) as session:
        client = _business(session, email="fail@test.io")
        result = _run(session, client, monkeypatch, channel=SpyChannel(explode=True))

        assert "+15125550149" in result["reply"]
        assert "wasn't able to reach the team" in result["reply"]


def test_a_failed_page_is_logged_as_undelivered(test_engine, monkeypatch):
    with Session(test_engine) as session:
        client = _business(session, email="undeliv@test.io")
        _run(session, client, monkeypatch, channel=SpyChannel(explode=True))

        row = session.exec(
            select(OwnerNotification).where(
                OwnerNotification.business_id == client.id,
                OwnerNotification.kind == KIND_ESCALATION,
            )
        ).first()
        assert row.delivered is False


def test_no_escalation_phone_configured_falls_back_to_911_not_an_empty_number(
    test_engine, monkeypatch
):
    with Session(test_engine) as session:
        client = _business(session, email="nophone@test.io", escalation_phone="")
        result = _run(session, client, monkeypatch)

        assert "911" in result["reply"]
        # Never "call ." — the bug an unguarded f-string would produce.
        assert "please call ." not in result["reply"]


def test_a_failed_page_does_not_stamp_owner_alerted_at(test_engine, monkeypatch):
    """owner_alerted_at is what suppresses a retry — stamping it on a failed
    send would permanently silence the page for this emergency."""
    with Session(test_engine) as session:
        client = _business(session, email="nostamp@test.io")
        _run(session, client, monkeypatch, channel=SpyChannel(explode=True))

        job = session.exec(
            select(Job).where(
                Job.business_id == client.id,
                Job.service_type == ESCALATION_SERVICE_TYPE,
            )
        ).first()
        assert job.owner_alerted_at is None


def test_a_retry_after_a_failed_page_still_reaches_the_owner(test_engine, monkeypatch):
    with Session(test_engine) as session:
        client = _business(session, email="retry@test.io")
        _run(session, client, monkeypatch, channel=SpyChannel(explode=True))

        working = SpyChannel()
        _run(session, client, monkeypatch, channel=working, text="its getting worse, please hurry")

        assert [m for m in working.sent if m["body"].startswith("URGENT")]


# ---- idempotency ----------------------------------------------------------


def test_a_second_emergency_text_in_the_same_conversation_does_not_double_page(
    test_engine, monkeypatch
):
    with Session(test_engine) as session:
        client = _business(session, email="dedup@test.io")
        _run(session, client, monkeypatch)

        second = SpyChannel()
        _run(session, client, monkeypatch, channel=second, text="are they on the way?")

        assert [m for m in second.sent if m["body"].startswith("URGENT")] == []
        jobs = session.exec(
            select(Job).where(
                Job.business_id == client.id,
                Job.service_type == ESCALATION_SERVICE_TYPE,
            )
        ).all()
        assert len(jobs) == 1


def test_a_new_emergency_after_the_window_pages_the_owner_again(test_engine, monkeypatch):
    """The regression this window exists to prevent: an SMS thread is the
    customer's phone number forever, so without a window the first emergency
    they ever reported would silently suppress every later one."""
    with Session(test_engine) as session:
        client = _business(session, email="window@test.io")
        _run(session, client, monkeypatch)

        stale = session.exec(
            select(Job).where(
                Job.business_id == client.id,
                Job.service_type == ESCALATION_SERVICE_TYPE,
            )
        ).first()
        stale.created_at = datetime.utcnow() - timedelta(hours=30)
        session.add(stale)
        session.commit()

        later = SpyChannel()
        _run(
            session,
            client,
            monkeypatch,
            channel=later,
            text="different problem, water heater is leaking everywhere",
        )

        assert [m for m in later.sent if m["body"].startswith("URGENT")]


def test_voice_style_per_call_threads_are_unaffected_by_the_window(test_engine):
    """A voice thread is unique per call, so it can never recur inside or
    outside the window — the window must change nothing for it."""
    with Session(test_engine) as session:
        client = _business(session, email="voice@test.io")
        job_a, notify_a = record_escalation(
            session, client, "xai-voice:call-1", "+15125559999", "emergency"
        )
        job_b, notify_b = record_escalation(
            session, client, "xai-voice:call-1", "+15125559999", "still needs help"
        )
        assert notify_a is True
        assert job_b.id == job_a.id and notify_b is True  # not yet paged

        job_c, notify_c = record_escalation(
            session, client, "xai-voice:call-2", "+15125559999", "new call"
        )
        assert job_c.id != job_a.id and notify_c is True


# ---- the owner's own test chat must never page them ------------------------


def test_the_dashboard_test_chat_never_pages_the_owner(test_engine, monkeypatch):
    with Session(test_engine) as session:
        client = _business(session, email="dash@test.io")
        spy = SpyChannel()
        _run(session, client, monkeypatch, channel=spy, phone="dashboard")

        assert [m for m in spy.sent if m["body"].startswith("URGENT")] == []
        assert (
            session.exec(
                select(OwnerNotification).where(
                    OwnerNotification.business_id == client.id,
                    OwnerNotification.kind == KIND_ESCALATION,
                )
            ).first()
            is None
        )


# ---- an ordinary conversation is untouched ---------------------------------


def test_a_routine_conversation_still_sends_no_urgent_page(test_engine, monkeypatch):
    class RoutineAgent:
        def respond(self, client_config, history, tools=None, system_prompt=None, max_iters=None):
            return {
                "reply": "You're booked!",
                "jobs": [
                    {
                        "id": "t1",
                        "input": {
                            "service_type": "AC tune-up",
                            "urgency": "routine",
                        },
                    }
                ],
                "new_messages": [],
                "pending_tool_call": None,
            }

    with Session(test_engine) as session:
        client = _business(session, email="routine@test.io")
        spy = SpyChannel()
        result = _run(
            session,
            client,
            monkeypatch,
            agent=RoutineAgent(),
            channel=spy,
            text="can I get a tune up sometime next week",
        )

        assert result["reply"] == "You're booked!"
        assert [m for m in spy.sent if m["body"].startswith("URGENT")] == []
        assert (
            session.exec(
                select(Job).where(
                    Job.business_id == client.id,
                    Job.service_type == ESCALATION_SERVICE_TYPE,
                )
            ).first()
            is None
        )
