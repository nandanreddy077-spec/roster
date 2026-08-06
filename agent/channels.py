"""Outbound SMS.

Inbound *replies* go out as TwiML from the webhook (no credentials needed), so
this module is only for proactively starting a conversation — e.g. the
missed-call text-back, where Roster texts the customer first.

Set TWILIO_ACCOUNT_SID + TWILIO_AUTH_TOKEN to send for real; otherwise messages
print to the console so the flow is fully testable without an account.
"""
import os
import re
import sys
from typing import Protocol


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
            print(
                "[WARNING] TWILIO_MESSAGING_SERVICE_SID not set — SMS sends from a bare "
                "long code with no A2P 10DLC campaign attached and may be filtered by "
                "US carriers. See agent/README.md 'A2P 10DLC'.",
                file=sys.stderr,
            )
        return TwilioChannel(sid, token, messaging_service_sid)
    # Deliberately loud, not a silent no-op: in production this means real
    # customer SMS never sends and nothing else would ever surface that.
    print(
        "[WARNING] TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN not set — outbound SMS "
        "will only print to console, not actually send.",
        file=sys.stderr,
    )
    return ConsoleChannel()
