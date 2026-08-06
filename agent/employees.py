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
    # The one question this employee's own workspace page answers immediately,
    # before any activity row (founder, 2026-07-29) — e.g. "Is Frontdesk
    # answering customers?" Left "" for every `planned` entry: writing this
    # copy for an employee with no engine would be inventing marketing content
    # for a product that doesn't exist yet, the same rule that kept "Reviews
    # received" off the dashboard. Populated only for employees with a real
    # engine (frontdesk, quote_chaser, retention_manager, reviews, referral,
    # lead_qualifier, dispatcher).
    mission: str = ""


REGISTRY: list[EmployeeDefinition] = [
    # Customer Service
    EmployeeDefinition("frontdesk", "customer_service", "live", "Frontdesk",
                       mission="Is Frontdesk answering customers?"),
    EmployeeDefinition("support", "customer_service", "planned", "Support"),
    # Reviews' capability ships today inside the same engine as Retention
    # Manager, but it belongs to the department that owns the outcome
    # (founder, 2026-07-28). roles.ROLE_KEYS already emits this key, so real
    # Employee rows can carry it — it was missing from the registry, not
    # from the product.
    # Graduated internal -> live (founder, 2026-08-04): the engine, the tick,
    # the reply classification and the negative-reply escalation are all
    # built, and deploying it is now a standing console action with no
    # bespoke work per business. review_service gates its sends on the
    # Employee row (runner.is_active), so hiring actually turns it on —
    # before that gate existed, "live" would have meant nothing.
    EmployeeDefinition("reviews", "customer_service", "live", "Reviews",
                       mission="Is Reviews requesting feedback?"),
    # Sales
    # Deterministic — no AgentEngine, no LLM call (2026-07-30 design review):
    # enriches every deployed business's new jobs with structured
    # classification (job_type, priority, financing/membership candidacy,
    # possible_spam).
    # Graduated internal -> live (founder, 2026-08-06) on the same two
    # conditions Reviews and Quote Chaser had to meet. Gated: qualify_new_jobs
    # goes through runner.dispatch_tick, so only businesses with an Employee
    # row are ever touched. Verified end to end against production data — three
    # jobs booked through bookings.book_job came back classified
    # repair/replacement with the expected priority and financing/membership
    # flags, and a second tick produced zero rows (idempotent).
    EmployeeDefinition("lead_qualifier", "sales", "live", "Lead Qualifier",
                       mission="Is Lead Qualifier enriching new jobs?"),
    # Graduated internal -> live (founder, 2026-08-04) on the same two
    # conditions Reviews had to meet: the whole journey verified end to end
    # against real Claude — estimate marked done, auto-enrolled, chased, "yes",
    # slots offered, slot chosen, job booked, owner texted — and enrolment
    # gated on the Employee row (recovery_service.enroll_completed_estimates).
    # Before that gate, `live` would have meant a business could be chased
    # without ever hiring anyone. Deploying is now a standing console action
    # with no bespoke work per business.
    EmployeeDefinition("quote_chaser", "sales", "live", "Quote Chaser",
                       mission="Is Quote Chaser recovering revenue?"),
    EmployeeDefinition("membership_agent", "sales", "planned", "Membership Agent"),
    EmployeeDefinition("upsell_agent", "sales", "planned", "Upsell Agent"),
    # Operations
    # Deterministic, same reasoning as Lead Qualifier above — combines
    # Job.urgency and JobQualification's already-structured output into a
    # scheduling recommendation, no LLM involved.
    # Graduated internal -> live (founder, 2026-08-06) alongside Lead
    # Qualifier, same two conditions. Gated through runner.dispatch_tick.
    # Verified end to end on the same three production jobs: emergency ->
    # immediate + requires_dispatch_review, replacement -> same_day/today via
    # the qualifier high bump, routine repair -> normal/tomorrow. Second tick
    # produced zero rows.
    EmployeeDefinition("dispatcher", "operations", "live", "Dispatcher",
                       mission="Is Dispatcher planning today's work?"),
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
    # Graduated internal -> live (founder, 2026-08-06). Its gate is the one
    # dispatch_tick's docstring calls out as deliberate rather than missing:
    # sends only ever work RecoveryJobs belonging to a campaign, and a campaign
    # has exactly two origins — create_campaign (a console action, consent by
    # definition) and enroll_completed_estimates (gated on the Employee row).
    # The send path itself is the same recovery_service.tick() verified against
    # real Claude for Quote Chaser on 2026-08-04, and renewal campaigns are
    # driven through it in test_recovery_service.
    # HONEST LIMIT: unlike the other two, no real rebooker/renewals campaign
    # has been run against a live customer — the send path is covered by tests
    # and shared with a verified employee, not exercised in production.
    EmployeeDefinition("retention_manager", "customer_success", "live", "Retention Manager",
                       mission="Is Retention Manager bringing customers back?"),
    # Marketing
    EmployeeDefinition("reactivation", "marketing", "planned", "Reactivation"),
    EmployeeDefinition("referral", "marketing", "planned", "Referral",
                       mission="Is Referral bringing in new business?"),
    EmployeeDefinition("campaign_manager", "marketing", "planned", "Campaign Manager"),
    # Leadership
    EmployeeDefinition("business_analyst", "leadership", "planned", "Business Analyst"),
    EmployeeDefinition("operations_manager", "leadership", "planned", "Operations Manager"),
]
