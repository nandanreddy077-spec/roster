"""Runner: the seam that lets a future AI employee react to something that
happened (e.g. "a job completed") without re-wiring the app around it.

This is the RoleDefinition/Runner shape from the platform PRD (§11), kept
deliberately EMPTY of registered employees. Roster's roadmap is
demand-driven: an employee is built only after a discovery call proves a
customer will pay for it, then registered here. The long-term org chart is
company vision (see the PRD and employees.py) — it is NOT an implementation
backlog, so no speculative roles live in this file.

Design choices, honestly:
- Does NOT use the `eventbus.bus` singleton. That singleton binds its
  SQLAlchemy engine at import time (see eventbus.py); every existing test
  constructs its own `EventBus(test_engine)` to avoid it. A live route
  wired to the global `bus` would write real Event rows into whatever
  database db.py resolves to on import. So the Runner takes an explicit
  `session`, matching recovery_service/referral_service.
- `JOB_COMPLETED_ROLES` is empty until the first job-completion employee is
  actually built and validated. The hook in app.py's complete_job already
  calls `dispatch_job_completed`, so that first employee plugs in by adding
  one `RoleDefinition` here — no other change.
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
    capability: Capability


def is_active(session, business: Business, role_key: str) -> bool:
    """Is `role_key` deployed and working for this business?

    Reads the Employee row, which IS the deployment record. This replaces the
    old requested_roster/frontdesk_live check, which conflated "the customer
    asked for this" with "this is running" — requested_roster was both (see
    docs/superpowers/specs/2026-07-29-phase-4-deployment-path-audit.md F6).

    Legacy key spellings are normalized: real rows carry "retention" while
    callers ask for "retention_manager".

    Takes a session because deployment state is a row now, not a column. The
    only caller, dispatch_job_completed, already has one.
    """
    from sqlmodel import select

    from db_models import Employee
    from departments import canonical_role_key

    wanted = canonical_role_key(role_key)
    for e in session.exec(
        select(Employee).where(Employee.business_id == business.id)
    ).all():
        if canonical_role_key(e.role_key) == wanted and e.status != "fired":
            return True
    return False


def dispatch_job_completed(session: Session, business: Business, job: Job) -> None:
    """Fire every deployed employee that reacts to a completed job. Empty
    until the first such employee is built and validated — a no-op today."""
    for defn in JOB_COMPLETED_ROLES:
        if is_active(session, business, defn.role_key):
            defn.capability(session, business, job)


# The first validated job-completion employee registers here (one line).
JOB_COMPLETED_ROLES: List[RoleDefinition] = []


def deployed_businesses(session: Session, role_key: str) -> List[Business]:
    """Every Business with `role_key` deployed and not fired — the bulk-query
    sibling of is_active, for tick-shaped access ("scan every deployed
    business") rather than request-shaped access ("check this one business").
    Same Employee-row source of truth, same legacy-key normalization, so the
    two can never disagree about the same row (2026-07-30, Critical Finding
    #2: this is the shared resolver every tick-based employee goes through —
    none of them queries deployment state independently)."""
    from sqlmodel import select

    from db_models import Employee
    from departments import canonical_role_key

    wanted = canonical_role_key(role_key)
    business_ids = {
        e.business_id
        for e in session.exec(select(Employee).where(Employee.status != "fired")).all()
        if canonical_role_key(e.role_key) == wanted
    }
    if not business_ids:
        return []
    return session.exec(select(Business).where(Business.id.in_(business_ids))).all()


TickCapability = Callable[[Session, Business], list]


def dispatch_tick(session: Session, role_key: str, capability: TickCapability) -> list:
    """The tick-triggered counterpart to dispatch_job_completed: resolve
    which businesses have `role_key` deployed, and only then call
    `capability(session, business)` for each — once per deployed business,
    never for anything else. This is the single path every tick-based
    employee's own processing function is reached through; none of them
    queries Job/JobQualification/etc. across businesses on its own, so there
    is no reachable code path to an undeployed business's data to forget to
    guard.

    Aggregates and returns whatever each capability call returns (typically
    the rows it created), matching the existing return shape of
    qualify_new_jobs/recommend_dispatch/etc. so callers don't need to
    change."""
    results: list = []
    for business in deployed_businesses(session, role_key):
        produced = capability(session, business)
        if produced:
            results.extend(produced)
    return results
