"""Where Recovery gets bookable time slots.

Phase 1 (2026-08-18) replaced the invented-slot fallback with a real read-only
Google Calendar free/busy lookup. Before this, ManualCalendarProvider generated
plausible weekday windows from the clock alone — no calendar, no technician, no
truck — and Quote Chaser offered them to customers as if the business had agreed
to them. The booking was honestly marked BOOKING_PROPOSED, but the times
themselves were fiction.

The contract now: **a slot is only ever offered if a connected calendar said
that exact window is free.** When no calendar is connected, or the calendar
can't be read, the answer is an empty list — never a guess. Callers must treat
empty as "ask the customer when suits them and escalate to the owner", which is
what recovery_service does.

Adding another provider later (Jobber, Housecall Pro) is a new class here with
the same shape, exactly as channels.py does for SMS.
"""

import logging
import os
from datetime import datetime, time, timedelta, timezone
from typing import List, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import business_hours
from google_calendar import CalendarUnavailable, GoogleFreeBusy

logger = logging.getLogger(__name__)

# Matches the previous behaviour: never propose tomorrow, leave booking lead
# time so the owner can react to a proposal before it arrives.
LEAD_TIME_DAYS = 2

# The two windows a home-service business actually books in. Kept as the same
# human strings the old provider emitted so offered_slots_json, confirm_slot's
# index matching and render_slot_language all keep working unchanged.
MORNING = ("morning (9am-12pm)", 9, 12)
AFTERNOON = ("afternoon (1pm-4pm)", 13, 16)

# When a business has no timezone set we cannot place a window on the clock at
# all. US home services, and the send-window logic in channels.py already
# assumes mainland US, so this is the least-wrong default — but it is recorded
# as a fallback so it can be surfaced rather than silently assumed.
DEFAULT_TIMEZONE = "America/New_York"


class CalendarProvider(Protocol):
    def get_available_slots(
        self, business_hours: str, days_ahead: int = 7, count: int = 3
    ) -> List[str]: ...


class UnconnectedCalendarProvider:
    """No calendar connected. Offers nothing, ever.

    This class exists so the "no integration" case is a real, named, tested
    behaviour instead of an implicit fallthrough — and so it can never be
    mistaken for a provider that just happened to find no free time.
    """

    connected = False

    def get_available_slots(
        self, business_hours: str, days_ahead: int = 7, count: int = 3
    ) -> List[str]:
        return []


class GoogleCalendarProvider:
    """Read-only Google Calendar availability.

    Builds candidate morning/afternoon windows across the next `days_ahead`
    days, filtered by the business's opening hours, then removes every window
    that overlaps a real busy period. What survives is offered.
    """

    connected = True

    def __init__(self, freebusy, timezone: str = "", hours: str = "", calendar_id: str = "primary"):
        self._freebusy = freebusy
        self._tz_name = timezone or DEFAULT_TIMEZONE
        self._hours_text = hours
        self._calendar_id = calendar_id or "primary"

    def _zone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self._tz_name)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            logger.warning(
                "unknown business timezone %r — falling back to %s",
                self._tz_name,
                DEFAULT_TIMEZONE,
            )
            return ZoneInfo(DEFAULT_TIMEZONE)

    def get_available_slots(
        self, business_hours_text: str = "", days_ahead: int = 7, count: int = 3
    ) -> List[str]:
        # The caller passes client.hours positionally (recovery_service does);
        # prefer whatever the provider was constructed with, fall back to it.
        hours = business_hours.parse(self._hours_text or business_hours_text)
        zone = self._zone()

        first_day = (datetime.now(zone) + timedelta(days=LEAD_TIME_DAYS)).date()
        last_day = first_day + timedelta(days=days_ahead)
        window_start = datetime.combine(first_day, time(0, 0), tzinfo=zone)
        window_end = datetime.combine(last_day, time(23, 59), tzinfo=zone)

        # Any failure here propagates as CalendarUnavailable. It is NOT caught
        # and converted to an empty list, because the caller must be able to
        # tell "genuinely nothing free" from "couldn't read the calendar" —
        # they warrant different messages to the customer and the owner.
        busy = self._freebusy.busy_periods(window_start, window_end, self._calendar_id)

        slots: List[str] = []
        day = first_day
        while day < last_day and len(slots) < count:
            if hours.is_open_on(day.weekday()):
                for label, start_h, end_h in (MORNING, AFTERNOON):
                    if len(slots) >= count:
                        break
                    # The window must sit entirely inside opening hours.
                    if start_h < hours.open_hour or end_h > hours.close_hour:
                        continue
                    start = datetime.combine(day, time(start_h), tzinfo=zone)
                    end = datetime.combine(day, time(end_h), tzinfo=zone)
                    if start <= datetime.now(zone):
                        continue
                    if _overlaps_any(start, end, busy):
                        continue
                    slots.append(f"{day.strftime('%A %m/%d')} {label}")
            day += timedelta(days=1)
        return slots


def _overlaps_any(start: datetime, end: datetime, busy) -> bool:
    """Half-open overlap: a busy block ending exactly at the window's start
    does not collide with it."""
    s, e = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    return any(s < b_end and b_start < e for b_start, b_end in busy)


def get_calendar_provider(client) -> CalendarProvider:
    """The connected provider for this business, or the one that offers nothing.

    Deliberately never raises: a business with no calendar is a normal state,
    not an error. Failing to *read* a connected calendar is different and does
    raise, from inside GoogleCalendarProvider.
    """
    refresh_token = getattr(client, "google_refresh_token", None)
    if not refresh_token:
        return UnconnectedCalendarProvider()

    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    if not (client_id and client_secret):
        # A stored token is useless without the app credentials to refresh it.
        logger.warning(
            "business %s has a stored calendar token but GOOGLE_CLIENT_ID/SECRET "
            "are unset — treating the calendar as unconnected",
            getattr(client, "id", "?"),
        )
        return UnconnectedCalendarProvider()

    calendar_id = getattr(client, "google_calendar_id", "") or "primary"
    return GoogleCalendarProvider(
        freebusy=GoogleFreeBusy(
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
            calendar_id=calendar_id,
        ),
        timezone=getattr(client, "timezone", "") or "",
        hours=getattr(client, "hours", "") or "",
        calendar_id=calendar_id,
    )


__all__ = [
    "CalendarProvider",
    "CalendarUnavailable",
    "GoogleCalendarProvider",
    "UnconnectedCalendarProvider",
    "get_calendar_provider",
]
