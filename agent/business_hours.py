"""Parse a business's free-text opening hours into something schedulable.

`Business.hours` is owner-typed free text ("Mon-Sat 7am-7pm", "9-5",
"Mon-Fri 8am-6pm"), because that is what the onboarding form asks for and what
the AI prompt already consumes. Availability needs it as structure.

An unparseable string falls back to a deliberately CONSERVATIVE window
(Mon-Fri 9am-5pm) rather than a permissive one. That is not fabrication: every
slot is still checked against the real calendar before being offered — the
window only ever narrows what can be proposed. Guessing wide (say, 7am-9pm
every day) would risk offering a genuinely free calendar slot at a time the
business is shut.
"""

import re
from typing import NamedTuple, Set

DAY_INDEX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_DAY_ALIASES = {
    "mon": "mon",
    "monday": "mon",
    "tue": "tue",
    "tues": "tue",
    "tuesday": "tue",
    "wed": "wed",
    "weds": "wed",
    "wednesday": "wed",
    "thu": "thu",
    "thur": "thu",
    "thurs": "thu",
    "thursday": "thu",
    "fri": "fri",
    "friday": "fri",
    "sat": "sat",
    "saturday": "sat",
    "sun": "sun",
    "sunday": "sun",
}

DEFAULT_OPEN_HOUR = 9
DEFAULT_CLOSE_HOUR = 17
DEFAULT_WEEKDAYS = frozenset({0, 1, 2, 3, 4})  # Mon-Fri


class BusinessHours(NamedTuple):
    weekdays: Set[int]  # Python weekday(): Monday=0 .. Sunday=6
    open_hour: int
    close_hour: int
    parsed: bool  # False when we fell back to the conservative default

    def is_open_on(self, weekday: int) -> bool:
        return weekday in self.weekdays


def _to_24h(value: int, meridiem: str, is_close: bool) -> int:
    """'7' + 'pm' -> 19. Bare numbers use opening-hours convention: a closing
    hour of 5 means 5pm, not 5am, which is how owners actually write '9-5'."""
    if meridiem == "am":
        return 0 if value == 12 else value
    if meridiem == "pm":
        return value if value == 12 else value + 12
    if is_close and value < 12:
        return value + 12  # "9-5" closes at 17:00
    return value


def parse(raw: str) -> BusinessHours:
    text = (raw or "").strip().lower()
    if not text:
        return BusinessHours(set(DEFAULT_WEEKDAYS), DEFAULT_OPEN_HOUR, DEFAULT_CLOSE_HOUR, False)

    # Times: "7am-7pm", "9 - 5", "8:30am-6pm" (minutes are read but floored to
    # the hour — slot windows are hour-granular by design).
    time_match = re.search(
        r"(\d{1,2})(?::\d{2})?\s*(am|pm)?\s*(?:-|–|to)\s*(\d{1,2})(?::\d{2})?\s*(am|pm)?", text
    )
    if not time_match:
        return BusinessHours(set(DEFAULT_WEEKDAYS), DEFAULT_OPEN_HOUR, DEFAULT_CLOSE_HOUR, False)

    o_val, o_mer, c_val, c_mer = time_match.groups()
    open_hour = _to_24h(int(o_val), o_mer or "", is_close=False)
    close_hour = _to_24h(int(c_val), c_mer or "", is_close=True)
    if not (0 <= open_hour < close_hour <= 24):
        return BusinessHours(set(DEFAULT_WEEKDAYS), DEFAULT_OPEN_HOUR, DEFAULT_CLOSE_HOUR, False)

    weekdays = _parse_days(text)
    return BusinessHours(weekdays or set(DEFAULT_WEEKDAYS), open_hour, close_hour, True)


def _parse_days(text: str) -> Set[int]:
    """ "mon-sat" -> {0..5}; "mon, wed, fri" -> {0,2,4}; nothing found -> empty
    (the caller substitutes Mon-Fri)."""
    span = re.search(r"\b([a-z]{3,9})\s*(?:-|–|to)\s*([a-z]{3,9})\b", text)
    if span:
        a, b = _DAY_ALIASES.get(span.group(1)), _DAY_ALIASES.get(span.group(2))
        if a and b:
            start, end = DAY_INDEX[a], DAY_INDEX[b]
            if start <= end:
                return set(range(start, end + 1))
            # Wrap-around ranges like "sat-tue".
            return set(range(start, 7)) | set(range(0, end + 1))

    found = {
        DAY_INDEX[_DAY_ALIASES[w]] for w in re.findall(r"\b[a-z]{3,9}\b", text) if w in _DAY_ALIASES
    }
    return found
