"""Phase 1 RED: prove Quote Chaser currently invents appointment availability.

ManualCalendarProvider generates plausible-looking weekday windows from nothing
but the clock — no calendar, no technician, no truck. recovery_service.py's own
comment admits it ("which invents plausible weekday windows"), and the booking
is honestly marked BOOKING_PROPOSED as a result. But the customer still receives
"Which works best: 1) Tuesday 08/19 morning..." for times the business never
agreed to.

These tests assert the end state: no code path may offer a time that wasn't
checked against a real calendar. They fail against ManualCalendarProvider,
which is the point.
"""

from datetime import datetime, timedelta


def test_a_business_with_no_calendar_connected_is_offered_no_slots():
    """The core fabrication bug. With nothing connected there is no source of
    truth for availability, so the honest answer is an empty list — never a
    guess dressed as an offer."""
    from calendar_provider import get_calendar_provider

    class NoCalendarBusiness:
        id = 1
        hours = "Mon-Sat 7am-7pm"
        timezone = "America/New_York"
        google_refresh_token = None

    provider = get_calendar_provider(NoCalendarBusiness())
    slots = provider.get_available_slots("Mon-Sat 7am-7pm", count=3)

    assert slots == [], f"availability was invented with no calendar connected: {slots}"


def test_offered_slots_are_never_derived_from_the_clock_alone():
    """ManualCalendarProvider's tell: ask twice for the same business and you
    get times that depend only on today's date, never on what is actually
    booked. Any provider that can answer without consulting a calendar is
    fabricating by construction."""
    from calendar_provider import get_calendar_provider

    class NoCalendarBusiness:
        id = 1
        hours = "Mon-Sat 7am-7pm"
        timezone = "America/New_York"
        google_refresh_token = None

    provider = get_calendar_provider(NoCalendarBusiness())
    slots = provider.get_available_slots("Mon-Sat 7am-7pm", count=3)

    # A fabricating provider always produces exactly `count` slots, because it
    # counts up to the number asked for instead of reporting what is free.
    assert len(slots) != 3 or slots == [], (
        f"provider produced exactly the requested count with no calendar to read: {slots}"
    )


def test_a_slot_is_only_offered_when_a_real_calendar_says_it_is_free():
    """The positive case, stated as an invariant: every offered slot must trace
    to a free/busy answer from a connected calendar."""
    from calendar_provider import GoogleCalendarProvider

    tomorrow = (datetime.utcnow() + timedelta(days=1)).date()

    class FakeFreeBusy:
        """Stands in for Google's freeBusy API: the whole day is busy."""

        def busy_periods(self, start, end, calendar_id="primary"):
            return [(start, end)]

    provider = GoogleCalendarProvider(
        freebusy=FakeFreeBusy(), timezone="America/New_York", hours="Mon-Sat 7am-7pm"
    )
    slots = provider.get_available_slots("Mon-Sat 7am-7pm", count=3)

    assert slots == [], f"offered a slot on a fully-booked calendar: {slots} ({tomorrow})"
