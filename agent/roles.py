"""Maps the internal agent architecture (Frontdesk, Chaser, Rebooker,
Renewals, Reviews) onto the customer-facing employee model. Customers never
see internal agent names — only roles tied to the problem being solved. See
docs/superpowers/specs/2026-07-10-self-serve-signup-dashboard-design.md.
"""

from typing import List, Optional

RECEPTIONIST_TRADE_NAMES = {
    "hvac": "CSR",
    "plumbing": "Receptionist",
    "electrical": "Office Manager",
    "roofing": "Office Coordinator",
}
DEFAULT_RECEPTIONIST_NAME = "Receptionist"


def receptionist_display_name(trade: str) -> str:
    return RECEPTIONIST_TRADE_NAMES.get((trade or "").strip().lower(), DEFAULT_RECEPTIONIST_NAME)


# Fixed hire sequence after the Receptionist (always hired first, at signup).
# "Retention Manager" is the customer-facing name for the internal
# Rebooker + Renewals + Reviews engine — one employee, adaptive behavior
# based on whatever data the business actually has.
ROSTER_HIRE_ORDER = ["Quote Chaser", "Retention Manager"]

ROSTER_DESCRIPTIONS = {
    "Quote Chaser": "Follows up every estimate automatically.",
    "Retention Manager": "Keeps customers coming back — renewals, rebooking, or review asks, whichever fits your business.",
}


def next_hire(requested_roster: List[str]) -> Optional[str]:
    """The single role that's currently hireable. Enforces one-role-at-a-time
    sequencing — a role later in ROSTER_HIRE_ORDER is never offered before
    the ones ahead of it have been requested."""
    for role in ROSTER_HIRE_ORDER:
        if role not in requested_roster:
            return role
    return None


def coming_later_after(next_role: Optional[str]) -> Optional[str]:
    """The role shown greyed-out, informational-only, right after `next_role`."""
    if next_role is None:
        return None
    idx = ROSTER_HIRE_ORDER.index(next_role)
    if idx + 1 < len(ROSTER_HIRE_ORDER):
        return ROSTER_HIRE_ORDER[idx + 1]
    return None


# Maps a requested_roster display name (as appended by portal.py's hire
# routes) to the Employee.role_key it's stored under. The single source of
# truth for both the live hire path and db.py's backfill, so they can never
# derive a different key for the same role name.
ROLE_KEYS = {
    "frontdesk": "frontdesk",
    "receptionist": "frontdesk",
    "quote chaser": "quote_chaser",
    "retention manager": "retention",
    "reviews": "reviews",
}


def role_key_for(role: str) -> str:
    name = role.strip().lower()
    return ROLE_KEYS.get(name, name.replace(" ", "_"))
