"""Runner: dispatches a real occurrence (e.g. "a job completed") to the
capabilities of every RoleDefinition active for that business.

This is the RoleDefinition/Runner model from the platform PRD (§11),
scoped honestly to what's actually buildable without a schema change:

- Deliberately does NOT go through `eventbus.bus`, the existing dormant
  EventBus singleton. That singleton binds its SQLAlchemy engine once at
  import time (see eventbus.py) — every existing test avoids the global
  `bus` for exactly this reason and constructs its own `EventBus(test_engine)`
  instead. Wiring a live route handler to the global `bus` would make
  pytest runs write real Event rows into whatever database `db.py` resolves
  to on import. This Runner instead takes an explicit `session`, matching
  how every other capability in this codebase (recovery_service,
  referral_service) already works. Making the `bus` singleton safe to use
  everywhere is real, separate work — not done here.
- Most of the roster below has `capability=None`. That is not a placeholder
  to fill in later without comment — each one names exactly what's missing
  (usually a database table or an external integration this codebase does
  not have), so the gap is a documented fact, not a TODO.
"""
import json
from dataclasses import dataclass
from typing import Callable, List, Optional

from sqlmodel import Session

from db_models import Business, Job

Capability = Callable[[Session, Business, Job], None]


@dataclass(frozen=True)
class RoleDefinition:
    role_key: str
    trigger: str  # human-readable occurrence this fires on
    capability: Optional[Capability]
    blocked_on: Optional[str] = None  # required when capability is None


def is_active(business: Business, role_key: str) -> bool:
    """An employee is 'active' for a business if it's been deployed for
    them — reuses the existing Business.frontdesk_live flag and
    requested_roster list (both already exist; no schema change)."""
    if role_key == "frontdesk":
        return business.frontdesk_live
    requested = json.loads(business.requested_roster) if business.requested_roster else []
    name_by_key = {"quote_chaser": "Quote Chaser", "retention_manager": "Retention Manager"}
    return name_by_key.get(role_key, role_key) in requested


def dispatch_job_completed(session: Session, business: Business, job: Job) -> None:
    for defn in JOB_COMPLETED_ROLES:
        if defn.capability is not None and is_active(business, defn.role_key):
            defn.capability(session, business, job)


def _upsell_capability(session: Session, business: Business, job: Job) -> None:
    from upsell_engine import send_upsell_message
    from channels import get_channel
    send_upsell_message(business, job, get_channel())


JOB_COMPLETED_ROLES: List[RoleDefinition] = [
    RoleDefinition("upsell_agent", "job_completed", _upsell_capability),
]

# The full roster from the long-term vision, declared so the org chart is
# real, inspectable code — not a fake implementation. `capability=None`
# entries are honest stubs: they do nothing, and `blocked_on` says exactly
# why, per employees.py's quarterly graduation rule (a `planned` entry
# graduates to `internal`/`live` only when its blocker is actually resolved
# by real demand, not by writing fake code to look done).
DECLARED_ROLES: List[RoleDefinition] = [
    RoleDefinition("frontdesk", "message_received", None,
                    "Already runs directly via engine.py's AgentEngine, not through this Runner — "
                    "re-platforming the live wedge onto a new dispatch path isn't worth the risk."),
    RoleDefinition("quote_chaser", "quote_followup_due", None,
                    "Already runs via recovery_service.tick() on a schedule (recovery_tick.py). "
                    "Not re-platformed onto this event-driven Runner — tick() is a scheduled batch "
                    "job, not a per-event reaction, and it's working revenue code."),
    RoleDefinition("retention_manager", "customer_dormant", None,
                    "Same as quote_chaser: already runs via recovery_service.tick() + "
                    "referral_service.send_due_referral_asks(). Not re-platformed."),
    RoleDefinition("upsell_agent", "job_completed", _upsell_capability),
    RoleDefinition("support", "message_received", None,
                    "No distinct capability from Frontdesk's existing conversational loop — "
                    "status checks/rescheduling/warranty questions are already answerable by "
                    "engine.py's intake loop. Splitting it into a separate employee needs a real "
                    "routing decision (which employee answers an inbound message) this codebase "
                    "doesn't have yet."),
    RoleDefinition("reviews_respond", "review.posted", None,
                    "Needs a Google/Yelp review-platform API integration — does not exist. "
                    "(The 'ask for a review' half already runs today, inline in app.py's "
                    "complete_job — not through this Runner.)"),
    RoleDefinition("lead_qualifier", "lead.created", None,
                    "Needs a Lead concept distinct from Customer (a lead isn't a customer yet) — "
                    "no such table exists. Schema change required."),
    RoleDefinition("membership_agent", "membership.renewal_due", None,
                    "Needs a Membership/maintenance-agreement table — does not exist. "
                    "Schema change required."),
    RoleDefinition("dispatcher", "job.created", None,
                    "Needs a Technician model (skill, location, availability) plus real-time "
                    "location/traffic data — neither exists. Schema change + external "
                    "integration required."),
    RoleDefinition("route_optimizer", "schedule.tick", None,
                    "Same blocker as dispatcher: no Technician/scheduling model to optimize."),
    RoleDefinition("emergency_coordinator", "job.emergency_detected", None,
                    "Same blocker as dispatcher: 'find nearest technician' needs the Technician "
                    "model this codebase doesn't have."),
    RoleDefinition("collections", "invoice.overdue", None,
                    "Needs an Invoice/payment-status model and a payment processor integration — "
                    "neither exists. Schema change + external integration required."),
    RoleDefinition("financing", "quote.large_amount", None,
                    "Needs a lender/financing-partner API — does not exist."),
    RoleDefinition("rebooker", "schedule.due", None,
                    "Overlaps with retention_manager's existing reactivation/renewal faces "
                    "(recovery_service.tick()) — not a distinct capability yet."),
    RoleDefinition("reactivation", "customer_dormant", None,
                    "Already covered by retention_manager's reactivation face in "
                    "recovery_service.tick() — not a separate employee in practice."),
    RoleDefinition("referral", "job_completed", None,
                    "Already runs via referral_service.py + referral_engine.py, wired into the "
                    "existing Retention Manager hire flow — not through this Runner."),
    RoleDefinition("campaign_manager", "campaign.scheduled", None,
                    "Needs an email channel (deferred in ROADMAP.md — SMS-only for now) and, for "
                    "SMS blasts to existing customers, real consent/opt-out tracking this "
                    "codebase doesn't have. Given the TCPA exposure already flagged for cold "
                    "outreach this sprint, this one needs a compliance answer before code, not "
                    "just a schema change."),
    RoleDefinition("business_analyst", "nightly.tick", None,
                    "Needs real operational volume (calls/jobs/quotes/invoices/reviews across "
                    "many customers) to say anything true. With zero paying customers there is "
                    "nothing to analyze yet — building the mechanism now would produce fake or "
                    "empty-sounding output."),
    RoleDefinition("operations_manager", "daily.tick", None,
                    "Same blocker as business_analyst: no operational data yet."),
    RoleDefinition("office_manager", None, None,
                    "No responsibilities were ever defined for this role in any spec — "
                    "nothing to build until it's scoped."),
    RoleDefinition("ceo_assistant", None, None,
                    "No responsibilities were ever defined for this role in any spec — "
                    "nothing to build until it's scoped."),
    RoleDefinition("sales_assistant", None, None,
                    "No responsibilities were ever defined for this role in any spec, and it's "
                    "unclear how it differs from lead_qualifier — nothing to build until scoped."),
    RoleDefinition("csr", None, None,
                    "Not a distinct role — 'CSR' is already Frontdesk's HVAC-specific display "
                    "name (see roles.py's RECEPTIONIST_TRADE_NAMES). A separate 'csr' role would "
                    "collide with it."),
]
