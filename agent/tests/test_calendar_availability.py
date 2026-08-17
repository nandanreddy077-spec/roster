"""Real calendar availability: what may and may not be offered.

The single invariant under test: a slot reaches a customer only when a
connected calendar said that exact window is free. Everything else — no
calendar, unreadable calendar, closed that day, outside opening hours, already
booked — must produce no offer rather than a guess.
"""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from business_hours import parse as parse_hours
from calendar_provider import (
    GoogleCalendarProvider,
    UnconnectedCalendarProvider,
    get_calendar_provider,
)
from google_calendar import CalendarUnavailable

NY = ZoneInfo("America/New_York")


class FakeFreeBusy:
    """Records the window it was asked about and returns scripted busy blocks."""

    def __init__(self, busy=None, raises=None):
        self._busy = busy or []
        self._raises = raises
        self.calls = []

    def busy_periods(self, start, end, calendar_id="primary"):
        self.calls.append((start, end, calendar_id))
        if self._raises:
            raise self._raises
        return self._busy


def _first_offered_day(tz="America/New_York", hours="Mon-Sat 7am-7pm"):
    """The date of the first morning window this provider would offer against a
    completely empty calendar.

    Anchoring on the provider's own answer keeps these tests deterministic
    whatever day the suite runs — picking an arbitrary future weekday instead
    lands outside the first `count` slots and silently asserts nothing.
    """
    probe = GoogleCalendarProvider(freebusy=FakeFreeBusy(busy=[]), timezone=tz, hours=hours)
    first = probe.get_available_slots(count=1)[0]
    zone = ZoneInfo(tz)
    md = first.split()[1]  # "08/19"
    month, dayn = (int(x) for x in md.split("/"))
    today = datetime.now(zone).date()
    year = today.year + (1 if month < today.month else 0)
    return datetime(year, month, dayn, tzinfo=zone).date()


def _window(day, start_h, end_h):
    return (
        datetime.combine(day, time(start_h), tzinfo=NY).astimezone(timezone.utc),
        datetime.combine(day, time(end_h), tzinfo=NY).astimezone(timezone.utc),
    )


# --- the calendar is the only source of truth ---------------------------------


def test_a_free_slot_can_be_offered():
    provider = GoogleCalendarProvider(
        freebusy=FakeFreeBusy(busy=[]), timezone="America/New_York", hours="Mon-Sat 7am-7pm"
    )
    slots = provider.get_available_slots(count=3)
    assert len(slots) == 3, slots
    assert all("morning" in s or "afternoon" in s for s in slots)


def test_a_busy_window_is_never_offered():
    day = _first_offered_day()
    provider = GoogleCalendarProvider(
        freebusy=FakeFreeBusy(busy=[_window(day, 9, 12)]),
        timezone="America/New_York",
        hours="Mon-Sat 7am-7pm",
    )
    slots = provider.get_available_slots(count=6)
    offered_that_morning = f"{day.strftime('%A %m/%d')} morning (9am-12pm)"
    assert offered_that_morning not in slots, f"offered a busy morning: {slots}"


def test_a_partial_overlap_still_blocks_the_window():
    """An 11am-11:30 appointment kills the whole 9-12 window — half a window is
    not bookable, and offering it would be the same fabrication in miniature."""
    day = _first_offered_day()
    provider = GoogleCalendarProvider(
        freebusy=FakeFreeBusy(busy=[_window(day, 11, 12)]),
        timezone="America/New_York",
        hours="Mon-Sat 7am-7pm",
    )
    slots = provider.get_available_slots(count=6)
    assert f"{day.strftime('%A %m/%d')} morning (9am-12pm)" not in slots, slots


def test_a_busy_block_ending_exactly_at_the_window_start_does_not_block_it():
    """Half-open comparison: an 8-9am job does not collide with a 9am start."""
    day = _first_offered_day()
    provider = GoogleCalendarProvider(
        freebusy=FakeFreeBusy(busy=[_window(day, 8, 9)]),
        timezone="America/New_York",
        hours="Mon-Sat 7am-7pm",
    )
    slots = provider.get_available_slots(count=6)
    assert f"{day.strftime('%A %m/%d')} morning (9am-12pm)" in slots, slots


def test_a_fully_booked_calendar_offers_nothing():
    now = datetime.now(NY)
    provider = GoogleCalendarProvider(
        freebusy=FakeFreeBusy(
            busy=[
                (
                    now.astimezone(timezone.utc),
                    (now + timedelta(days=30)).astimezone(timezone.utc),
                )
            ]
        ),
        timezone="America/New_York",
        hours="Mon-Sat 7am-7pm",
    )
    assert provider.get_available_slots(count=3) == []


# --- timezone -----------------------------------------------------------------


def test_the_freebusy_window_is_timezone_aware_and_in_the_business_zone():
    """A naive datetime would be read by Google in an unspecified zone and
    return the wrong day's availability."""
    fb = FakeFreeBusy(busy=[])
    provider = GoogleCalendarProvider(
        freebusy=fb, timezone="America/Los_Angeles", hours="Mon-Sat 7am-7pm"
    )
    provider.get_available_slots(count=1)

    start, end, _ = fb.calls[0]
    assert start.tzinfo is not None and end.tzinfo is not None
    assert start.utcoffset() == datetime.now(ZoneInfo("America/Los_Angeles")).utcoffset()


def test_the_same_busy_block_blocks_different_windows_in_different_zones():
    """16:00 UTC is 12pm in New York (blocks the morning window) but 9am in Los
    Angeles (blocks nothing, since the LA morning window starts at 9am local =
    16:00 UTC and a block ending then is half-open)."""
    day = _first_offered_day()
    ny_busy = _window(day, 9, 12)  # 9-12 New York local

    ny = GoogleCalendarProvider(
        freebusy=FakeFreeBusy(busy=[ny_busy]),
        timezone="America/New_York",
        hours="Mon-Sat 7am-7pm",
    )
    la = GoogleCalendarProvider(
        freebusy=FakeFreeBusy(busy=[ny_busy]),
        timezone="America/Los_Angeles",
        hours="Mon-Sat 7am-7pm",
    )
    label = f"{day.strftime('%A %m/%d')} morning (9am-12pm)"
    assert label not in ny.get_available_slots(count=6)
    # Same absolute instant, three hours earlier locally — LA's 9am-12pm is
    # 16:00-19:00 UTC, so a 13:00-16:00 UTC block cannot touch it.
    assert label in la.get_available_slots(count=6)


def test_an_unknown_timezone_falls_back_without_crashing():
    provider = GoogleCalendarProvider(
        freebusy=FakeFreeBusy(busy=[]), timezone="Mars/Olympus_Mons", hours="Mon-Sat 7am-7pm"
    )
    assert len(provider.get_available_slots(count=2)) == 2


# --- opening hours ------------------------------------------------------------


def test_a_day_the_business_is_closed_is_never_offered():
    provider = GoogleCalendarProvider(
        freebusy=FakeFreeBusy(busy=[]), timezone="America/New_York", hours="Mon-Fri 7am-7pm"
    )
    slots = provider.get_available_slots(count=10, days_ahead=14)
    for s in slots:
        assert not s.startswith("Saturday"), s
        assert not s.startswith("Sunday"), s


def test_a_window_outside_opening_hours_is_rejected():
    """A business open 9am-12pm can be offered the morning window but never the
    1pm-4pm one, even on a completely empty calendar."""
    provider = GoogleCalendarProvider(
        freebusy=FakeFreeBusy(busy=[]), timezone="America/New_York", hours="Mon-Sat 9am-12pm"
    )
    slots = provider.get_available_slots(count=10)
    assert slots, "expected the morning window to still be offerable"
    assert all("afternoon" not in s for s in slots), slots


def test_unparseable_hours_fall_back_to_a_conservative_window_not_a_permissive_one():
    hours = parse_hours("whenever we feel like it")
    assert hours.parsed is False
    assert hours.weekdays == {0, 1, 2, 3, 4}  # Mon-Fri, not all week
    assert (hours.open_hour, hours.close_hour) == (9, 17)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Mon-Sat 7am-7pm", ({0, 1, 2, 3, 4, 5}, 7, 19)),
        ("9-5", ({0, 1, 2, 3, 4}, 9, 17)),
        ("Mon-Fri 8am-6pm", ({0, 1, 2, 3, 4}, 8, 18)),
        ("Monday to Friday 10am-4pm", ({0, 1, 2, 3, 4}, 10, 16)),
    ],
)
def test_common_owner_typed_hours_parse(text, expected):
    h = parse_hours(text)
    assert (h.weekdays, h.open_hour, h.close_hour) == expected, text


# --- failure never becomes fabrication ---------------------------------------


def test_no_calendar_connected_offers_nothing():
    assert UnconnectedCalendarProvider().get_available_slots("Mon-Sat 7am-7pm") == []


def test_get_calendar_provider_returns_unconnected_without_a_token():
    class B:
        id = 1
        hours = "Mon-Sat 7am-7pm"
        timezone = "America/New_York"
        google_refresh_token = None

    assert isinstance(get_calendar_provider(B()), UnconnectedCalendarProvider)


def test_a_stored_token_without_app_credentials_is_treated_as_unconnected(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)

    class B:
        id = 1
        hours = "Mon-Sat 7am-7pm"
        timezone = "America/New_York"
        google_refresh_token = "stored-but-unusable"

    assert isinstance(get_calendar_provider(B()), UnconnectedCalendarProvider)


def test_an_unreadable_calendar_raises_rather_than_returning_no_slots():
    """The caller must distinguish "genuinely nothing free" from "couldn't
    read it" — they warrant different messages to customer and owner. Silently
    returning [] here would collapse the two."""
    provider = GoogleCalendarProvider(
        freebusy=FakeFreeBusy(raises=CalendarUnavailable("token revoked")),
        timezone="America/New_York",
        hours="Mon-Sat 7am-7pm",
    )
    with pytest.raises(CalendarUnavailable):
        provider.get_available_slots(count=3)


# --- repeated lookups ---------------------------------------------------------


def test_repeated_lookups_are_side_effect_free_and_agree():
    """Availability is a read. Asking twice must not book, hold, or mutate
    anything, and must give the same answer for the same calendar."""
    fb = FakeFreeBusy(busy=[])
    provider = GoogleCalendarProvider(
        freebusy=fb, timezone="America/New_York", hours="Mon-Sat 7am-7pm"
    )
    first = provider.get_available_slots(count=3)
    second = provider.get_available_slots(count=3)
    assert first == second
    assert len(fb.calls) == 2  # read twice, wrote nothing
