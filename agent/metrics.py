"""Outcome metrics — one definition, shared by every surface.

Two registries govern everything here, and both exist for the same reason:
a number on the customer's dashboard must be traceable to the rows behind it.

  METRIC_RECORDS    — for each metric, which records it counts.
  EMPLOYEE_RECORDS  — for each employee, which record types belong to them,
                      which metrics they expose, and how drill-down resolves
                      to underlying rows.

EVERY CUSTOMER-VISIBLE METRIC MUST HAVE A FUTURE DRILL-DOWN PATH (founder,
2026-07-29). "12 jobs booked" is 12 rows the owner could one day tap into;
"an efficiency score of 84" is an assertion, not a number. A metric that
cannot name its rows cannot be declared, so it cannot ship.

WHY ATTRIBUTION IS DECLARED RATHER THAN QUERIED: no record in the database
carries an employee id — the only such column is on `event`, and nothing
publishes events in the live path. So "these rows are Frontdesk's work" is a
convention, and a convention that isn't written down is just duplicated logic
waiting to drift. EMPLOYEE_RECORDS is that convention, written down once.
Employee and department pages render entirely from it; no role-specific
branching belongs in a route or a template.

Honesty rule: an employee or department with nothing attributable reports NO
metrics rather than zeros. "0 jobs dispatched" implies a department that ran
and achieved nothing; the truth is it was never built.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from sqlmodel import func, select

from db_models import (
    DispatchPlan, Job, JobQualification, MembershipOffer, Message, OwnerNotification,
    RecoveryCampaign, RecoveryJob, ReferralLead, ReviewReply,
)
from notifications import (
    KIND_CALL_DROPPED, KIND_ESCALATION, is_test_thread,
)

# Voice conversations thread under this prefix (xai_voice_adapter.VOICE_THREAD_PREFIX).
# Imported as a literal rather than from the adapter to keep this module free of
# the websocket/telephony import chain.
_VOICE_THREAD_PREFIX = "xai-voice:"

JOBS_BOOKED = "jobs_booked"
CALLS_ANSWERED = "calls_answered"
ESCALATIONS = "escalations"
REVIEW_REQUESTS_SENT = "review_requests_sent"
REVIEW_FOLLOWUPS_SENT = "review_followups_sent"
REVIEW_RESPONSES = "review_responses"
REVIEWS_SELF_REPORTED = "reviews_self_reported"
REVIEW_NEGATIVE_REPLIES = "review_negative_replies"
QUOTES_CHASED = "quotes_chased"
QUOTES_RECOVERED = "quotes_recovered"
CUSTOMERS_REACHED = "customers_reached"
CUSTOMERS_RETURNED = "customers_returned"
REFERRALS_RECEIVED = "referrals_received"
LEADS_QUALIFIED = "leads_qualified"
HIGH_PRIORITY_LEADS = "high_priority_leads"
FINANCING_CANDIDATES = "financing_candidates"
MEMBERSHIP_CANDIDATES = "membership_candidates"
POSSIBLE_SPAM_FLAGGED = "possible_spam_flagged"
MEMBERSHIP_OFFERS_SENT = "membership_offers_sent"
# "accepted", never "enrolled": Roster records that the customer said yes and
# texts the owner — it cannot charge a card, so the owner finalizes billing.
# Counting these as enrollments would be exactly the kind of flattering
# number ARCHITECTURE.md invariant 10 exists to keep off the dashboard.
MEMBERSHIPS_ACCEPTED = "memberships_accepted"
JOBS_DISPATCHED = "jobs_dispatched"
EMERGENCY_DISPATCHES = "emergency_dispatches"
SAME_DAY_DISPATCHES = "same_day_dispatches"
MANUAL_REVIEW_FLAGGED = "manual_review_flagged"

METRIC_RECORDS = {
    JOBS_BOOKED: "job where not is_test_thread(customer_phone)",
    CALLS_ANSWERED: "distinct message.customer_phone starting 'xai-voice:'",
    ESCALATIONS: "ownernotification where kind in ('escalation', 'call_dropped')",
    REVIEW_REQUESTS_SENT: "job where review_requested_at is not null",
    REVIEW_FOLLOWUPS_SENT: "job where review_followup_sent_at is not null",
    REVIEW_RESPONSES: "reviewreply (any outcome)",
    REVIEWS_SELF_REPORTED: "reviewreply where outcome == 'left_review' (self-reported, never verified)",
    REVIEW_NEGATIVE_REPLIES: "reviewreply where outcome == 'negative'",
    QUOTES_CHASED: "recoveryjob via recoverycampaign.face == 'quote'",
    QUOTES_RECOVERED: "recoveryjob via recoverycampaign.face == 'quote', current_status == 'booked'",
    CUSTOMERS_REACHED: "recoveryjob via recoverycampaign.face in ('reactivation', 'membership')",
    CUSTOMERS_RETURNED: (
        "recoveryjob via recoverycampaign.face in ('reactivation', 'membership'), "
        "current_status == 'booked'"
    ),
    REFERRALS_RECEIVED: "referrallead",
    LEADS_QUALIFIED: "jobqualification",
    HIGH_PRIORITY_LEADS: "jobqualification where priority == 'high'",
    FINANCING_CANDIDATES: "jobqualification where financing_candidate is true",
    MEMBERSHIP_CANDIDATES: "jobqualification where membership_candidate is true",
    POSSIBLE_SPAM_FLAGGED: "jobqualification where possible_spam is true",
    MEMBERSHIP_OFFERS_SENT: "membershipoffer where sent_at is not null",
    MEMBERSHIPS_ACCEPTED: "membershipoffer where outcome == 'accepted'",
    JOBS_DISPATCHED: "dispatchplan",
    EMERGENCY_DISPATCHES: "dispatchplan where dispatch_priority == 'emergency'",
    SAME_DAY_DISPATCHES: "dispatchplan where dispatch_priority == 'same_day'",
    MANUAL_REVIEW_FLAGGED: "dispatchplan where requires_dispatch_review is true",
}


@dataclass(frozen=True)
class ActivityRow:
    """One real record, normalized for display. `when` orders it; `summary`
    describes it; `kind` lets a template style it. Facts only — the wording of
    the surrounding page stays in the template."""
    when: datetime
    kind: str
    summary: str


@dataclass(frozen=True)
class RecordSource:
    """One kind of record an employee produces — and the drill-down target.

    `records` names the rows in words (the invariant's declaration), `fetch`
    resolves them, and `metric` is the metric key its count exposes (None for
    a source that is activity-only).
    """
    key: str
    records: str
    metric: Optional[str]
    fetch: Callable      # (session, business_id, since) -> list[ActivityRow]


@dataclass(frozen=True)
class EmployeeRecords:
    role_key: str
    sources: tuple


# ---- resolvers: every one returns real rows, business-scoped ----------------

def _jobs(session, business_id, since=None):
    q = select(Job).where(Job.business_id == business_id)
    if since is not None:
        q = q.where(Job.created_at >= since)
    return [
        ActivityRow(j.created_at, "job_booked",
                    f"Booked {j.service_type}" + (f" for {j.customer_name}" if j.customer_name else ""))
        for j in session.exec(q).all() if not is_test_thread(j.customer_phone)
    ]


def _voice_conversations(session, business_id, since=None):
    q = select(Message).where(
        Message.business_id == business_id,
        Message.customer_phone.startswith(_VOICE_THREAD_PREFIX),
    )
    if since is not None:
        q = q.where(Message.created_at >= since)
    first_turn = {}
    for m in session.exec(q).all():
        seen = first_turn.get(m.customer_phone)
        if seen is None or m.created_at < seen:
            first_turn[m.customer_phone] = m.created_at
    return [ActivityRow(when, "call_answered", "Answered a call")
            for when in first_turn.values()]


def _escalations(session, business_id, since=None):
    q = select(OwnerNotification).where(
        OwnerNotification.business_id == business_id,
        OwnerNotification.kind.in_((KIND_ESCALATION, KIND_CALL_DROPPED)),
    )
    if since is not None:
        q = q.where(OwnerNotification.created_at >= since)
    return [ActivityRow(n.created_at, n.kind, n.message) for n in session.exec(q).all()]


def _review_requests(session, business_id, since=None):
    q = select(Job).where(
        Job.business_id == business_id, Job.review_requested_at.is_not(None)
    )
    if since is not None:
        q = q.where(Job.review_requested_at >= since)
    return [ActivityRow(j.review_requested_at, "review_requested",
                        f"Asked for a review after {j.service_type}")
            for j in session.exec(q).all()]


def _review_followups(session, business_id, since=None):
    q = select(Job).where(
        Job.business_id == business_id, Job.review_followup_sent_at.is_not(None)
    )
    if since is not None:
        q = q.where(Job.review_followup_sent_at >= since)
    return [ActivityRow(j.review_followup_sent_at, "review_followup",
                        f"Follow-up nudge after {j.service_type}")
            for j in session.exec(q).all()]


def _review_replies(outcomes=None, kind="review_reply"):
    def _fetch(session, business_id, since=None):
        q = select(ReviewReply).where(ReviewReply.business_id == business_id)
        if outcomes is not None:
            q = q.where(ReviewReply.outcome.in_(outcomes))
        if since is not None:
            q = q.where(ReviewReply.created_at >= since)
        return [ActivityRow(r.created_at, kind, f"Review reply: {r.outcome}")
                for r in session.exec(q).all()]
    return _fetch


def _recovery(faces, only_booked=False, kind="recovery"):
    def _fetch(session, business_id, since=None):
        q = (
            select(RecoveryJob)
            .join(RecoveryCampaign, RecoveryCampaign.id == RecoveryJob.campaign_id)
            .where(RecoveryJob.business_id == business_id, RecoveryCampaign.face.in_(faces))
        )
        if only_booked:
            q = q.where(RecoveryJob.current_status == "booked")
        if since is not None:
            q = q.where(RecoveryJob.created_at >= since)
        return [ActivityRow(r.created_at, kind,
                            f"{r.service_type} — {r.current_status.replace('_', ' ')}")
                for r in session.exec(q).all()]
    return _fetch


def _referrals(session, business_id, since=None):
    q = select(ReferralLead).where(ReferralLead.business_id == business_id)
    if since is not None:
        q = q.where(ReferralLead.created_at >= since)
    return [ActivityRow(r.created_at, "referral",
                        f"Referral from {r.asker_phone}") for r in session.exec(q).all()]


def _job_qualifications(condition=None, kind="lead_qualified"):
    def _fetch(session, business_id, since=None):
        q = select(JobQualification).where(JobQualification.business_id == business_id)
        if condition is not None:
            q = q.where(condition)
        if since is not None:
            q = q.where(JobQualification.created_at >= since)
        return [ActivityRow(r.created_at, kind, f"{r.job_type} — {r.priority} priority")
                for r in session.exec(q).all()]
    return _fetch


def _membership_offers(condition=None, kind="membership_offer"):
    def _fetch(session, business_id, since=None):
        # sent_at, not created_at, is the anchor for both the filter and the
        # activity timestamp: a row exists from the moment the tick claims the
        # job, but nothing has happened from the customer's point of view until
        # the text actually goes out. An unsent claim (a send that failed and
        # hasn't been retried) must never appear as activity.
        q = select(MembershipOffer).where(
            MembershipOffer.business_id == business_id,
            MembershipOffer.sent_at.is_not(None),
        )
        if condition is not None:
            q = q.where(condition)
        if since is not None:
            q = q.where(MembershipOffer.sent_at >= since)
        return [ActivityRow(r.sent_at, kind,
                            f"Membership offer to {r.customer_phone} — {r.outcome}")
                for r in session.exec(q).all()]
    return _fetch


def _dispatch_plans(condition=None, kind="dispatch_planned"):
    def _fetch(session, business_id, since=None):
        q = select(DispatchPlan).where(DispatchPlan.business_id == business_id)
        if condition is not None:
            q = q.where(condition)
        if since is not None:
            q = q.where(DispatchPlan.created_at >= since)
        return [ActivityRow(r.created_at, kind,
                            f"{r.dispatch_priority} — {r.scheduling_window}")
                for r in session.exec(q).all()]
    return _fetch


# ---- the registry -----------------------------------------------------------
# Inclusion does NOT imply the employee is deployable — same rule as the
# employee registry itself. `referral`'s engine ships and runs on the cron
# (recovery_tick.py) but its registry status is still `planned`, so nothing
# deploys it and its records are never rendered. The declaration is here so the
# attribution is settled before Marketing productizes, not invented then.

EMPLOYEE_RECORDS = {
    "frontdesk": EmployeeRecords("frontdesk", (
        RecordSource("booked_jobs", METRIC_RECORDS[JOBS_BOOKED], JOBS_BOOKED, _jobs),
        RecordSource("voice_conversations", METRIC_RECORDS[CALLS_ANSWERED],
                     CALLS_ANSWERED, _voice_conversations),
        RecordSource("escalations", METRIC_RECORDS[ESCALATIONS], ESCALATIONS, _escalations),
    )),
    "reviews": EmployeeRecords("reviews", (
        RecordSource("review_requests", METRIC_RECORDS[REVIEW_REQUESTS_SENT],
                     REVIEW_REQUESTS_SENT, _review_requests),
        RecordSource("review_followups", METRIC_RECORDS[REVIEW_FOLLOWUPS_SENT],
                     REVIEW_FOLLOWUPS_SENT, _review_followups),
        RecordSource("review_responses", METRIC_RECORDS[REVIEW_RESPONSES],
                     REVIEW_RESPONSES, _review_replies()),
        RecordSource("reviews_self_reported", METRIC_RECORDS[REVIEWS_SELF_REPORTED],
                     REVIEWS_SELF_REPORTED, _review_replies(("left_review",), kind="review_self_reported")),
        RecordSource("review_negative_replies", METRIC_RECORDS[REVIEW_NEGATIVE_REPLIES],
                     REVIEW_NEGATIVE_REPLIES, _review_replies(("negative",), kind="review_negative")),
        # Reviews RECEIVED (a verified Google/Yelp count) is deliberately
        # absent: unknowable without that integration, and inventing it would
        # be the dashboard's first fabricated number (founder, 2026-07-29).
        # reviews_self_reported is NOT that number — it's what the customer
        # told us, never presented as verified.
    )),
    "quote_chaser": EmployeeRecords("quote_chaser", (
        RecordSource("recovery_jobs", METRIC_RECORDS[QUOTES_CHASED], QUOTES_CHASED,
                     _recovery(("quote",), kind="quote_chase")),
        RecordSource("booked_recoveries", METRIC_RECORDS[QUOTES_RECOVERED], QUOTES_RECOVERED,
                     _recovery(("quote",), only_booked=True, kind="quote_recovered")),
    )),
    "retention_manager": EmployeeRecords("retention_manager", (
        RecordSource("recovery_jobs", METRIC_RECORDS[CUSTOMERS_REACHED], CUSTOMERS_REACHED,
                     _recovery(("reactivation", "membership"), kind="retention")),
        RecordSource("booked_recoveries", METRIC_RECORDS[CUSTOMERS_RETURNED], CUSTOMERS_RETURNED,
                     _recovery(("reactivation", "membership"), only_booked=True,
                               kind="customer_returned")),
    )),
    "referral": EmployeeRecords("referral", (
        RecordSource("referral_leads", METRIC_RECORDS[REFERRALS_RECEIVED],
                     REFERRALS_RECEIVED, _referrals),
    )),
    "lead_qualifier": EmployeeRecords("lead_qualifier", (
        RecordSource("jobs_qualified", METRIC_RECORDS[LEADS_QUALIFIED],
                     LEADS_QUALIFIED, _job_qualifications()),
        RecordSource("high_priority_leads", METRIC_RECORDS[HIGH_PRIORITY_LEADS],
                     HIGH_PRIORITY_LEADS,
                     _job_qualifications(JobQualification.priority == "high", "high_priority_lead")),
        RecordSource("financing_candidates", METRIC_RECORDS[FINANCING_CANDIDATES],
                     FINANCING_CANDIDATES,
                     _job_qualifications(JobQualification.financing_candidate == True,  # noqa: E712
                                         "financing_candidate")),
        RecordSource("membership_candidates", METRIC_RECORDS[MEMBERSHIP_CANDIDATES],
                     MEMBERSHIP_CANDIDATES,
                     _job_qualifications(JobQualification.membership_candidate == True,  # noqa: E712
                                         "membership_candidate")),
        RecordSource("possible_spam_flagged", METRIC_RECORDS[POSSIBLE_SPAM_FLAGGED],
                     POSSIBLE_SPAM_FLAGGED,
                     _job_qualifications(JobQualification.possible_spam == True,  # noqa: E712
                                         "possible_spam")),
    )),
    "membership_agent": EmployeeRecords("membership_agent", (
        RecordSource("membership_offers", METRIC_RECORDS[MEMBERSHIP_OFFERS_SENT],
                     MEMBERSHIP_OFFERS_SENT, _membership_offers()),
        RecordSource("memberships_accepted", METRIC_RECORDS[MEMBERSHIPS_ACCEPTED],
                     MEMBERSHIPS_ACCEPTED,
                     _membership_offers(MembershipOffer.outcome == "accepted",
                                        "membership_accepted")),
    )),
    "dispatcher": EmployeeRecords("dispatcher", (
        RecordSource("dispatch_plans", METRIC_RECORDS[JOBS_DISPATCHED],
                     JOBS_DISPATCHED, _dispatch_plans()),
        RecordSource("emergency_dispatches", METRIC_RECORDS[EMERGENCY_DISPATCHES],
                     EMERGENCY_DISPATCHES,
                     _dispatch_plans(DispatchPlan.dispatch_priority == "emergency", "emergency_dispatch")),
        RecordSource("same_day_dispatches", METRIC_RECORDS[SAME_DAY_DISPATCHES],
                     SAME_DAY_DISPATCHES,
                     _dispatch_plans(DispatchPlan.dispatch_priority == "same_day", "same_day_dispatch")),
        RecordSource("manual_review_flagged", METRIC_RECORDS[MANUAL_REVIEW_FLAGGED],
                     MANUAL_REVIEW_FLAGGED,
                     _dispatch_plans(DispatchPlan.requires_dispatch_review == True,  # noqa: E712
                                     "manual_review_flagged")),
    )),
}


# ---- the public read model --------------------------------------------------

def employee_outcomes(session, business_id: int, role_key: str, since=None) -> dict:
    """{metric_key: count} for one employee. Empty for an employee with no
    declared records — never fabricated zeros."""
    from departments import canonical_role_key

    entry = EMPLOYEE_RECORDS.get(canonical_role_key(role_key))
    if entry is None:
        return {}
    return {
        source.metric: len(source.fetch(session, business_id, since))
        for source in entry.sources if source.metric is not None
    }


def employee_activity(session, business_id: int, role_key: str, limit: int = 20,
                      since=None) -> list:
    """The rows behind this employee's numbers, newest first — the drill-down
    the metric invariant promises, resolved through the same declaration that
    produced the count."""
    from departments import canonical_role_key

    entry = EMPLOYEE_RECORDS.get(canonical_role_key(role_key))
    if entry is None:
        return []
    rows = []
    for source in entry.sources:
        rows.extend(source.fetch(session, business_id, since))
    rows.sort(key=lambda r: r.when, reverse=True)
    return rows[:limit]


def department_outcomes(session, business_id: int, department_key: str,
                        role_keys, since=None) -> dict:
    """The union of what this department's DEPLOYED employees report.

    Derived from the employees rather than computed separately, so a
    department total can never disagree with the employees under it — and a
    department never reports numbers for an employee it hasn't deployed.
    """
    outcomes = {}
    for role_key in role_keys:
        outcomes.update(employee_outcomes(session, business_id, role_key, since))
    return outcomes


def department_activity(session, business_id: int, role_keys, since=None,
                        limit: int = 20) -> list:
    """The department's whole timeline — the union of its deployed employees'
    activity, newest first. Derived by unioning employee_activity, the same
    way department_outcomes unions employee_outcomes: a department's feed can
    never diverge from the rows behind its employees' own numbers."""
    rows = []
    for role_key in role_keys:
        rows.extend(employee_activity(session, business_id, role_key, since=since))
    rows.sort(key=lambda r: r.when, reverse=True)
    return rows[:limit]


def booked_jobs(session, business_id: int) -> int:
    """Real customer jobs booked. Excludes the owner's own dashboard tests —
    testing your own AI is not revenue, and both surfaces must agree on that."""
    return session.exec(
        select(func.count(Job.id)).where(Job.business_id == business_id)
    ).one() - sum(
        1 for phone in session.exec(
            select(Job.customer_phone).where(Job.business_id == business_id)
        ).all() if is_test_thread(phone)
    )
