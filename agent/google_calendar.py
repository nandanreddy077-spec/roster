"""Read-only Google Calendar free/busy lookup.

Answers exactly one question: which windows in a date range are already taken?
Nothing here writes an event, creates a booking, or syncs anything back — the
OAuth scope requested at connect time is `calendar.readonly`, so it structurally
cannot. Two-way sync is deliberately out of scope (ROADMAP Phase D).

Uses httpx directly against the REST endpoint rather than adding
google-api-python-client: the whole integration is one token refresh plus one
freeBusy POST, and httpx is already a dependency.

Every failure path raises CalendarUnavailable. That matters more than it looks:
the caller must never be able to mistake "I couldn't reach the calendar" for
"the calendar says you're free", because the second one invents availability and
that is the exact bug this module exists to remove.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_FREEBUSY_URL = "https://www.googleapis.com/calendar/v3/freeBusy"

# Read-only by construction. Requested at connect time and enforced by Google,
# so a bug here cannot escalate into write access.
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"

_TIMEOUT_SECONDS = 10.0


class CalendarUnavailable(Exception):
    """The calendar could not be read: not connected, token rejected, API down,
    request timed out, or a malformed response. Never means "you are free"."""


def _require(condition, message: str) -> None:
    if not condition:
        raise CalendarUnavailable(message)


class GoogleFreeBusy:
    """One business's read-only calendar connection."""

    def __init__(
        self,
        refresh_token: str,
        client_id: str,
        client_secret: str,
        calendar_id: str = "primary",
        http=None,
    ):
        self._refresh_token = refresh_token
        self._client_id = client_id
        self._client_secret = client_secret
        self._calendar_id = calendar_id or "primary"
        # Injectable for tests; no real network call in the suite.
        self._http = http or httpx

    def _access_token(self) -> str:
        """Exchange the stored refresh token for a short-lived access token.

        ponytail: refreshes on every lookup rather than caching until expiry.
        An availability lookup only happens when a customer actually replies
        "interested", so this is a handful of calls a day, not a hot path.
        Cache the token with its expiry if that ever stops being true.
        """
        _require(self._refresh_token, "no Google Calendar connected for this business")
        _require(
            self._client_id and self._client_secret,
            "GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET not configured",
        )
        try:
            resp = self._http.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": self._refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=_TIMEOUT_SECONDS,
            )
        except Exception as e:  # network error, DNS, timeout
            raise CalendarUnavailable(f"token refresh failed: {type(e).__name__}") from e

        if resp.status_code != 200:
            # A revoked or expired refresh token lands here. The owner has to
            # reconnect; surfacing it as unavailable is what triggers the
            # owner-visible escalation upstream.
            raise CalendarUnavailable(f"token refresh rejected ({resp.status_code})")
        token = (resp.json() or {}).get("access_token")
        if not isinstance(token, str) or not token:
            raise CalendarUnavailable("token refresh returned no access_token")
        return token

    def busy_periods(
        self, start: datetime, end: datetime, calendar_id: Optional[str] = None
    ) -> List[Tuple[datetime, datetime]]:
        """Every busy interval between start and end, as timezone-aware UTC.

        Both bounds must be timezone-aware — a naive datetime here would be
        interpreted by Google in an unspecified zone and quietly return the
        wrong day's availability.
        """
        _require(
            start.tzinfo is not None and end.tzinfo is not None,
            "free/busy window must be timezone-aware",
        )
        cal = calendar_id or self._calendar_id
        token = self._access_token()
        try:
            resp = self._http.post(
                GOOGLE_FREEBUSY_URL,
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "timeMin": start.astimezone(timezone.utc).isoformat(),
                    "timeMax": end.astimezone(timezone.utc).isoformat(),
                    "items": [{"id": cal}],
                },
                timeout=_TIMEOUT_SECONDS,
            )
        except Exception as e:
            raise CalendarUnavailable(f"freeBusy request failed: {type(e).__name__}") from e

        if resp.status_code != 200:
            raise CalendarUnavailable(f"freeBusy rejected ({resp.status_code})")

        body = resp.json() or {}
        cal_body = (body.get("calendars") or {}).get(cal)
        # A calendar Google refuses to read comes back with an `errors` key and
        # an empty busy list. Treating that as "free all week" is precisely the
        # silent failure this module refuses to produce.
        if not isinstance(cal_body, dict):
            raise CalendarUnavailable(f"freeBusy returned no data for calendar {cal!r}")
        if cal_body.get("errors"):
            raise CalendarUnavailable(f"freeBusy error for {cal!r}: {cal_body['errors']}")

        periods: List[Tuple[datetime, datetime]] = []
        for block in cal_body.get("busy") or []:
            try:
                busy_start = datetime.fromisoformat(block["start"].replace("Z", "+00:00"))
                busy_end = datetime.fromisoformat(block["end"].replace("Z", "+00:00"))
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise CalendarUnavailable(f"malformed busy period: {block!r}") from exc
            periods.append((busy_start.astimezone(timezone.utc), busy_end.astimezone(timezone.utc)))
        return periods
