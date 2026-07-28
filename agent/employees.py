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
    # Reviews' capability ships today inside the same engine as Retention
    # Manager, but it belongs to the department that owns the outcome
    # (founder, 2026-07-28). roles.ROLE_KEYS already emits this key, so real
    # Employee rows can carry it — it was missing from the registry, not
    # from the product.
    EmployeeDefinition("reviews", "customer_service", "internal", "Reviews"),
    # Sales
    EmployeeDefinition("lead_qualifier", "sales", "planned", "Lead Qualifier"),
    EmployeeDefinition("quote_chaser", "sales", "internal", "Quote Chaser"),
    EmployeeDefinition("membership_agent", "sales", "planned", "Membership Agent"),
    EmployeeDefinition("upsell_agent", "sales", "planned", "Upsell Agent"),
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
    EmployeeDefinition("referral", "marketing", "planned", "Referral"),
    EmployeeDefinition("campaign_manager", "marketing", "planned", "Campaign Manager"),
    # Leadership
    EmployeeDefinition("business_analyst", "leadership", "planned", "Business Analyst"),
    EmployeeDefinition("operations_manager", "leadership", "planned", "Operations Manager"),
]
