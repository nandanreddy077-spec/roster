"""Parsing what an owner texts back about a booking.

Pure — no DB, no channel, no state. booking_manager decides what a parsed
command DOES; this module only decides what the owner meant.

The job id rides inside the token (`Y412`, not `Y`) because an owner with
three bookings waiting who replies "Y" is genuinely ambiguous, and guessing
would confirm the wrong customer's appointment. A bare `Y` is still accepted —
it is what people actually type — but only resolves when exactly one booking
is waiting; otherwise the caller asks which.
"""

import re
from dataclasses import dataclass
from typing import Optional

CONFIRM = "confirm"
REJECT = "reject"
PROPOSE = "propose"
UNKNOWN = "unknown"

# `#` is optional so both "Y412" and "Y #412" work — owners type both, and a
# phone keyboard makes the # easy to hit by accident.
_CONFIRM_RE = re.compile(r"^(?:yes|y|ok|okay|confirm)\s*#?\s*(\d+)?$", re.IGNORECASE)
_REJECT_RE = re.compile(r"^(?:no|n|reject|decline|cancel)\s*#?\s*(\d+)?$", re.IGNORECASE)
# A leading id followed by anything non-numeric is "this job, this window".
_TARGETED_WINDOW_RE = re.compile(r"^#?(\d+)\s+(.+)$", re.DOTALL)

# WHY THIS EXISTS: whatever we call a "window" is texted VERBATIM to a real
# customer. Treating every unrecognised reply as a proposed time meant an
# owner answering "no thanks" had that phrase sent to their customer as an
# arrival window. Text that cannot be read as a time must fall through to the
# help message instead of being broadcast.
#
# Deliberately a whitelist of time markers rather than a blacklist of chatter:
# a blacklist fails open (anything unanticipated gets sent), and the cost of
# failing open here is a nonsense text to a paying customer. Deliberately
# excludes bare "week" — "not this week" is a refusal, not a window, and
# "next week" is not a specific enough window to offer anyone.
_TIME_MARKERS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
    "today",
    "tomorrow",
    "tonight",
    "morning",
    "afternoon",
    "evening",
    "midday",
    "noon",
)
_WEEKDAY_ABBREV_RE = re.compile(r"\b(mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b", re.IGNORECASE)
_CLOCK_RE = re.compile(r"\d\s*(?:am|pm)\b|\d{1,2}:\d{2}|\b\d{1,2}\s*/\s*\d{1,2}\b", re.IGNORECASE)


def looks_like_a_time(text: str) -> bool:
    """Could this plausibly be an arrival window a customer would understand?"""
    lowered = text.lower()
    if any(marker in lowered for marker in _TIME_MARKERS):
        return True
    return bool(_WEEKDAY_ABBREV_RE.search(lowered) or _CLOCK_RE.search(lowered))


@dataclass(frozen=True)
class OwnerCommand:
    action: str
    job_id: Optional[int] = None
    window: str = ""


def parse_owner_command(text: str) -> OwnerCommand:
    """Read one inbound owner SMS.

    Anything that isn't a recognised yes/no is treated as a proposed arrival
    window — that is the honest default, because an owner who texts back
    "Thursday 8-12 instead" is proposing a time, not issuing a command, and
    the alternative (rejecting it as unparseable) throws away the most useful
    thing they could have said.
    """
    body = (text or "").strip()
    if not body:
        return OwnerCommand(UNKNOWN)

    match = _CONFIRM_RE.match(body)
    if match:
        return OwnerCommand(CONFIRM, int(match.group(1)) if match.group(1) else None)

    match = _REJECT_RE.match(body)
    if match:
        return OwnerCommand(REJECT, int(match.group(1)) if match.group(1) else None)

    match = _TARGETED_WINDOW_RE.match(body)
    if match:
        window = match.group(2).strip()
        if looks_like_a_time(window):
            return OwnerCommand(PROPOSE, int(match.group(1)), window)
        return OwnerCommand(UNKNOWN)

    if looks_like_a_time(body):
        return OwnerCommand(PROPOSE, None, body)
    return OwnerCommand(UNKNOWN)
