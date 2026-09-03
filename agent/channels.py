"""Outbound SMS.

Inbound *replies* go out as TwiML from the webhook (no credentials needed), so
this module is only for proactively starting a conversation — e.g. the
missed-call text-back, where Roster texts the customer first.

Set TWILIO_ACCOUNT_SID + TWILIO_AUTH_TOKEN to send for real; otherwise messages
print to the console so the flow is fully testable without an account.
"""

import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional, Protocol

logger = logging.getLogger(__name__)


def send_hours_ok(now: Optional[datetime] = None) -> bool:
    """True only during the UTC window that's guaranteed to be 9am-8pm local
    time in every mainland US timezone at once (Eastern through Pacific,
    either side of DST) — so a proactive outbound text (Quote Chaser,
    Retention Manager, Reviews, Referral) never lands at 3am wherever the
    recipient actually is. Gates the scheduler's send calls in
    recovery_tick.py; a skipped send just gets retried next hourly tick.

    ponytail: one fixed UTC window (17:00-23:59 UTC) instead of a real
    per-business timezone lookup — Business has no timezone field yet. Safe
    (never sends outside 9am-8pm local anywhere in the mainland US) but
    conservative (~7 send hours/day instead of the full ~11); also doesn't
    account for Alaska/Hawaii. Upgrade: add Business.timezone and check
    against the recipient's actual zone once that's needed.
    """
    hour = (now or datetime.now(timezone.utc)).hour
    return 17 <= hour < 24


# The words that mean "stop texting me". Lives here, next to send_hours_ok,
# because it is a compliance rule about the SMS channel itself — not one
# employee's behaviour. Every employee that runs an outbound sequence checks
# the SAME set: a second copy drifting out of date is how a business ends up
# texting someone who opted out.
STOP_KEYWORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit"}


def sms_deliverable(business) -> bool:
    """Whether a PROACTIVE text from this business's number should be sent now.

    False in exactly one state: `pending_campaign` — a number is bought but its
    A2P 10DLC campaign isn't approved yet, so carriers filter the traffic
    SILENTLY (the send succeeds, Twilio reports success, nobody receives it).
    Holding the send means it goes out for real once the campaign clears
    instead of vanishing (docs/PRODUCTION_READINESS.md P1-5).

    Any other state falls through to the normal send path: `active` means the
    campaign is approved; `not_configured` means we don't know enough to
    second-guess — the send either works (dev console) or fails loudly at
    Twilio if there is genuinely no number.

    Every proactive send path checks this, next to its opt-out check. Voice
    never does. Owner alerts don't either — one recipient, low volume,
    time-sensitive, and their real outcome is on OwnerNotification.delivered.

    Duck-typed on the attribute so it needs no db_models import and unit-tests
    with a bare object.
    """
    return getattr(business, "sms_delivery_status", None) != "pending_campaign"


def normalize_phone(raw: str) -> str:
    """Coerce a human-typed number to E.164 so Twilio can't guess wrong.

    Twilio resolves a number without a `+` against the *sender's* country. Our
    senders are US, so an owner who typed `770-288-1238` had their escalation
    alerts delivered to a stranger in Georgia — and Twilio reported success,
    because the number was perfectly valid, just not theirs. Roster is US-only
    (`../roster-growth/knowledge/home-services/market.md`), so a bare 10-digit
    number is a US number and gets +1.

    Anything already `+`-prefixed keeps its country code — that's how a founder
    testing from India writes `+919876543210` and actually receives the text.
    An unrecognised shape is returned untouched so Twilio rejects it loudly
    rather than us silently inventing a destination.
    """
    if not raw:
        return raw
    s = raw.strip()
    digits = re.sub(r"\D", "", s)
    if s.startswith("+"):
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return s


class SMSChannel(Protocol):
    def send(self, from_number: str, to_number: str, body: str) -> None: ...


class ConsoleChannel:
    def send(self, from_number: str, to_number: str, body: str) -> None:
        print(f"[SMS {from_number} -> {normalize_phone(to_number)}] {body}")


class TwilioChannel:
    def __init__(self, account_sid: str, auth_token: str, messaging_service_sid: str = ""):
        from twilio.rest import Client as TwilioRest

        self._client = TwilioRest(account_sid, auth_token)
        self._messaging_service_sid = messaging_service_sid

    def send(self, from_number: str, to_number: str, body: str) -> None:
        # Normalized here as well as at every input point: these two lines are
        # the only door every outbound SMS in the product goes through, so a
        # number that predates the input-side fix (or arrives from a path added
        # later) still can't reach the wrong handset.
        to = normalize_phone(to_number)
        if self._messaging_service_sid:
            # A2P 10DLC: US carriers filter unregistered application traffic
            # sent from a bare long code. The Messaging Service carries the
            # registered campaign, and supplies the sender, so `from_` is
            # deliberately not passed here.
            self._client.messages.create(
                messaging_service_sid=self._messaging_service_sid, to=to, body=body
            )
            return
        self._client.messages.create(from_=normalize_phone(from_number), to=to, body=body)


def get_channel() -> SMSChannel:
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    if sid and token:
        messaging_service_sid = os.environ.get("TWILIO_MESSAGING_SERVICE_SID", "").strip()
        if not messaging_service_sid:
            # Not fatal — unregistered traffic still sends, it just gets
            # filtered by some US carriers, which looks exactly like "the
            # product doesn't work" from the owner's side.
            logger.warning(
                "TWILIO_MESSAGING_SERVICE_SID not set — SMS sends from a bare long code "
                "with no A2P 10DLC campaign attached and may be filtered by US carriers. "
                "See agent/README.md 'A2P 10DLC'."
            )
        return TwilioChannel(sid, token, messaging_service_sid)
    # Deliberately loud, not a silent no-op: in production this means real
    # customer SMS never sends and nothing else would ever surface that.
    logger.warning(
        "TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN not set — outbound SMS will only "
        "print to console, not actually send."
    )
    return ConsoleChannel()
