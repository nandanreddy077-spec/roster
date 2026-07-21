"""Employee-first registry of Roster's long-term roster (spec:
docs/superpowers/specs/2026-07-21-ai-staffing-repositioning-design.md §4).

This mirrors the RoleDefinition pattern in the platform architecture PRD
(docs/superpowers/specs/2026-07-13-roster-platform-architecture-prd.md §6):
a code registry, not a table. `department` is a descriptive tag, not a
structural entity — departments may be reorganized later, the employees
Roster commits to are what matters.

Inclusion in this registry does NOT imply implementation, availability, or
customer visibility. Nothing in the running app reads this module yet.

Governance rule (founder, 2026-07-21): "internal" is not a resting state.
Every quarter, review every `internal` entry and move it to `live`
(repeatable, customer-ready) or back to `planned` (not valuable enough to
keep half-finished). An entry still `internal` after a review with no
graduation decision is a signal to force the call, not to leave it. See
`ROADMAP.md` "Employee registry discipline."

Companion file: `runner.py`'s `DECLARED_ROLES` is the execution-dispatch
side of this same roster — it names, per role, either a real capability or
exactly what blocks one. It also declares a few names (csr, office_manager,
ceo_assistant, sales_assistant) that this registry deliberately does NOT
include, per the 2026-07-21 decision to leave this list as-is rather than
add speculative entries from a chat sketch with no defined responsibilities.
"""
from dataclasses import dataclass
from typing import Literal

Status = Literal["live", "internal", "planned"]
# live     -> a customer can be hired into this employee today, self-serve or
#             founder-configured, no bespoke engineering per customer.
# internal -> the engine exists and the founder can manually deploy it for a
#             customer on request; not yet a standing dashboard offer.
# planned  -> vision only. No engine, no route, no UI.


@dataclass(frozen=True)
class EmployeeDefinition:
    key: str
    department: str
    status: Status
    display_name: str


REGISTRY: list[EmployeeDefinition] = [
    # Customer Service
    EmployeeDefinition("frontdesk", "customer_service", "live", "Frontdesk"),
    EmployeeDefinition("support", "customer_service", "planned", "Support"),
    # Sales
    EmployeeDefinition("lead_qualifier", "sales", "planned", "Lead Qualifier"),
    EmployeeDefinition("quote_chaser", "sales", "internal", "Quote Chaser"),
    EmployeeDefinition("membership_agent", "sales", "planned", "Membership Agent"),
    # upsell_agent graduated planned -> internal 2026-07-21: real capability
    # in upsell_engine.py, dispatched via runner.py, founder-deployable
    # through POST /clients/{id}/employees/deploy. Not `live` -- no
    # customer-facing way to request it yet.
    EmployeeDefinition("upsell_agent", "sales", "internal", "Upsell Agent"),
    # Operations
    EmployeeDefinition("dispatcher", "operations", "planned", "Dispatcher"),
    EmployeeDefinition("route_optimizer", "operations", "planned", "Route Optimizer"),
    EmployeeDefinition("emergency_coordinator", "operations", "planned", "Emergency Coordinator"),
    # Finance
    EmployeeDefinition("collections", "finance", "planned", "Collections"),
    EmployeeDefinition("financing", "finance", "planned", "Financing"),
    # Customer Success — "Retention Manager" is Roster's existing consolidated
    # name for what the long-term vision doc splits into Rebooker + Retention
    # (and Reviews, listed under Customer Service there); roles.py already
    # ships this as one employee, so the registry follows the shipped shape
    # rather than the doc's finer split.
    EmployeeDefinition("retention_manager", "customer_success", "internal", "Retention Manager"),
    # Marketing
    EmployeeDefinition("reactivation", "marketing", "planned", "Reactivation"),
    # referral corrected planned -> internal 2026-07-21: referral_service.py
    # + referral_engine.py already run in production, wired into the
    # existing Retention Manager hire flow. Not `live` on its own.
    EmployeeDefinition("referral", "marketing", "internal", "Referral"),
    EmployeeDefinition("campaign_manager", "marketing", "planned", "Campaign Manager"),
    # Intelligence
    EmployeeDefinition("business_analyst", "intelligence", "planned", "Business Analyst"),
    EmployeeDefinition("operations_manager", "intelligence", "planned", "Operations Manager"),
]
