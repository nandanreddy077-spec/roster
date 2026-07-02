"""Where Recovery gets bookable time slots.

Owner picks a calendar system per client at onboarding in the long run (Google
Calendar, Jobber, Housecall Pro), but building real OAuth/API integrations before
a real client asks for a specific one violates Roster's own build rule (ROSTER.md:
"Guess integrations — never do this"). Only the manual fallback is wired up today.
Adding a live provider later is a new class here, same shape as channels.py.
"""
from datetime import datetime, timedelta
from typing import List, Protocol


class CalendarProvider(Protocol):
    def get_available_slots(self, business_hours: str, days_ahead: int = 7, count: int = 3) -> List[str]: ...


class ManualCalendarProvider:
    """No live calendar connected: propose slots on the next weekdays (skipping
    Sunday), alternating morning/afternoon windows, starting 2 days out to leave
    booking lead time."""

    def get_available_slots(self, business_hours: str, days_ahead: int = 7, count: int = 3) -> List[str]:
        slots: List[str] = []
        day = datetime.utcnow().date() + timedelta(days=2)
        while len(slots) < count:
            if day.weekday() != 6:  # skip Sunday
                window = "morning (9am-12pm)" if len(slots) % 2 == 0 else "afternoon (1pm-4pm)"
                slots.append(f"{day.strftime('%A %m/%d')} {window}")
            day += timedelta(days=1)
        return slots


def get_calendar_provider(client) -> CalendarProvider:
    # Every client uses the manual fallback until a live Google/Jobber/Housecall
    # Pro adapter is built for a real client who asks for it.
    return ManualCalendarProvider()
