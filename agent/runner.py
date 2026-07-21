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


def is_active(business: Business, role_key: str) -> bool:
    """Is `role_key` deployed for this business? Reuses existing fields — no
    schema change: Frontdesk is the `frontdesk_live` flag; everything else is
    membership in `requested_roster`.

    ponytail: requested_roster has two writers with two formats — the
    customer prompt (portal.py) writes display names ("Quote Chaser"), the
    founder deploy route writes raw keys ("quote_chaser"). This bridges the
    two known display-name roles and otherwise matches the raw key. Reconcile
    to one format when the first job.completed employee actually ships and
    needs is_active to be authoritative."""
    if role_key == "frontdesk":
        return business.frontdesk_live
    requested = json.loads(business.requested_roster) if business.requested_roster else []
    name_by_key = {"quote_chaser": "Quote Chaser", "retention_manager": "Retention Manager"}
    return name_by_key.get(role_key, role_key) in requested


def dispatch_job_completed(session: Session, business: Business, job: Job) -> None:
    """Fire every deployed employee that reacts to a completed job. Empty
    until the first such employee is built and validated — a no-op today."""
    for defn in JOB_COMPLETED_ROLES:
        if is_active(business, defn.role_key):
            defn.capability(session, business, job)


# The first validated job-completion employee registers here (one line).
JOB_COMPLETED_ROLES: List[RoleDefinition] = []
