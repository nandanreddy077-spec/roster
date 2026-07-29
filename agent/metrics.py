"""Outcome metrics — one definition, shared by every surface.

Blueprint §6 puts an outcomes strip at the top of every department page, so
these numbers get rendered in many places. Defining them once is what stops
the founder console and the customer dashboard reporting different figures for
the same business — which they did before Phase 5: the customer excluded the
owner's own test conversations, the founder counted test jobs as real work.

FACTS ONLY, keyed by a stable metric key — never a label. Each surface maps
keys to its own wording, the same rule departments.DepartmentStatus follows.

Honesty rule: a department with no engine reports NO metrics rather than
zeros. "0 jobs dispatched" implies a department that ran and achieved
nothing; the truth is that it was never built.
"""
from sqlmodel import func, select

from db_models import Job, RecoveryCampaign, RecoveryJob, ReferralLead
from notifications import is_test_thread

JOBS_BOOKED = "jobs_booked"
QUOTES_CHASED = "quotes_chased"
QUOTES_RECOVERED = "quotes_recovered"
CUSTOMERS_REACHED = "customers_reached"
CUSTOMERS_RETURNED = "customers_returned"
REFERRALS_RECEIVED = "referrals_received"

# EVERY CUSTOMER-VISIBLE METRIC MUST HAVE A FUTURE DRILL-DOWN PATH (founder,
# 2026-07-29). A number on the dashboard represents concrete records the owner
# could one day tap into — never an opaque summary. "12 jobs booked" is 12 rows;
# "an efficiency score of 84" is an assertion, not a number.
#
# This map is that declaration: which records each metric counts. A metric
# cannot ship without an entry (test_metrics asserts it), which rules out
# derived scores by construction — a number that can't name the rows behind it
# can't be declared here, so it can't ship.
METRIC_RECORDS = {
    JOBS_BOOKED: "job where not is_test_thread(customer_phone)",
    QUOTES_CHASED: "recoveryjob via recoverycampaign.face == 'quote'",
    QUOTES_RECOVERED: "recoveryjob via recoverycampaign.face == 'quote', current_status == 'booked'",
    CUSTOMERS_REACHED: "recoveryjob via recoverycampaign.face in ('reactivation', 'membership')",
    CUSTOMERS_RETURNED: (
        "recoveryjob via recoverycampaign.face in ('reactivation', 'membership'), "
        "current_status == 'booked'"
    ),
    REFERRALS_RECEIVED: "referrallead",
}


def booked_jobs(session, business_id: int) -> int:
    """Real customer jobs booked. Excludes the owner's own dashboard tests —
    testing your own AI is not revenue, and both surfaces must agree on that.
    """
    rows = session.exec(
        select(Job.customer_phone).where(Job.business_id == business_id)
    ).all()
    return sum(1 for phone in rows if not is_test_thread(phone))


def _recovery_counts(session, business_id: int, face: str) -> tuple:
    """(worked, recovered) for one Recovery face. `booked` is the only status
    that means money came back."""
    rows = session.exec(
        select(RecoveryJob.current_status)
        .join(RecoveryCampaign, RecoveryCampaign.id == RecoveryJob.campaign_id)
        .where(RecoveryJob.business_id == business_id, RecoveryCampaign.face == face)
    ).all()
    return len(rows), sum(1 for status in rows if status == "booked")


def _customer_service(session, business_id):
    return {JOBS_BOOKED: booked_jobs(session, business_id)}


def _sales(session, business_id):
    chased, recovered = _recovery_counts(session, business_id, "quote")
    return {QUOTES_CHASED: chased, QUOTES_RECOVERED: recovered}


def _customer_success(session, business_id):
    reached, returned = _recovery_counts(session, business_id, "reactivation")
    renewals_reached, renewals_returned = _recovery_counts(session, business_id, "membership")
    return {
        CUSTOMERS_REACHED: reached + renewals_reached,
        CUSTOMERS_RETURNED: returned + renewals_returned,
    }


def _marketing(session, business_id):
    return {
        REFERRALS_RECEIVED: session.exec(
            select(func.count(ReferralLead.id)).where(ReferralLead.business_id == business_id)
        ).one()
    }


# Only departments with a real engine appear here. Operations, Finance and
# Leadership are absent on purpose — see the honesty rule above.
_BY_DEPARTMENT = {
    "customer_service": _customer_service,
    "sales": _sales,
    "customer_success": _customer_success,
    "marketing": _marketing,
}


def department_outcomes(session, business_id: int, department_key: str) -> dict:
    """This department's business outcomes for this business, as
    {metric_key: value}. Empty when the department has no engine to report on
    or the key is unknown — never fabricated zeros.
    """
    compute = _BY_DEPARTMENT.get(department_key)
    return compute(session, business_id) if compute else {}
