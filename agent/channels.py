"""Outbound SMS.

Inbound *replies* go out as TwiML from the webhook (no credentials needed), so
this module is only for proactively starting a conversation — e.g. the
missed-call text-back, where Roster texts the customer first.

Set TWILIO_ACCOUNT_SID + TWILIO_AUTH_TOKEN to send for real; otherwise messages
print to the console so the flow is fully testable without an account.
"""
import os
import sys
from typing import Protocol


class SMSChannel(Protocol):
    def send(self, from_number: str, to_number: str, body: str) -> None: ...


class ConsoleChannel:
    def send(self, from_number: str, to_number: str, body: str) -> None:
        print(f"[SMS {from_number} -> {to_number}] {body}")


class TwilioChannel:
    def __init__(self, account_sid: str, auth_token: str):
        from twilio.rest import Client as TwilioRest

        self._client = TwilioRest(account_sid, auth_token)

    def send(self, from_number: str, to_number: str, body: str) -> None:
        self._client.messages.create(from_=from_number, to=to_number, body=body)


def get_channel() -> SMSChannel:
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    if sid and token:
        return TwilioChannel(sid, token)
    # Deliberately loud, not a silent no-op: in production this means real
    # customer SMS never sends and nothing else would ever surface that.
    print(
        "[WARNING] TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN not set — outbound SMS "
        "will only print to console, not actually send.",
        file=sys.stderr,
    )
    return ConsoleChannel()
