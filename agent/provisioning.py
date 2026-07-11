"""Automates buying a client a phone number and wiring it for both SMS and
live voice, so onboarding a new client doesn't require hand-clicking through
Twilio's console.

Two halves, two different confidence levels:

1. Twilio (buy number, create/reuse an Elastic SIP Trunk pointed at xAI,
   attach the number) — this is Twilio's long-stable, fully documented REST
   API. Implemented for real below.

2. Registering that number with xAI's Voice Agent API (what actually turns
   the SIP trunk on and returns the per-number webhook signing secret) — xAI
   does not publish this endpoint anywhere in their public docs (confirmed
   2026-07-07; their SIP guide describes numbers being "registered" and a
   secret being "returned" but gives no POST URL or request/response shape).
   `register_number_with_xai()` below is a deliberate stub, not a guess — do
   not fill it in with an invented endpoint. Get the real one from xAI's
   dashboard/API reference after signing up (it's likely only shown there,
   not in the public docs), then implement it for real.
"""
import os
from typing import Optional

from twilio.rest import Client as TwilioRestClient

from db_models import Client

XAI_TRUNK_FRIENDLY_NAME = "Roster - xAI Voice"
DEFAULT_PUBLIC_BASE_URL = "https://rosterhires.com"


class ProvisioningError(Exception):
    pass


def _twilio_client() -> TwilioRestClient:
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    if not sid or not token:
        raise ProvisioningError("TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN must be set to buy a number")
    return TwilioRestClient(sid, token)


def buy_twilio_number(area_code: Optional[str] = None) -> dict:
    """Searches for and purchases one available US local number, and points
    its "a message comes in" webhook at our /webhook/sms endpoint — without
    this, a purchased number can receive texts but nothing ever reaches the
    app, since Twilio doesn't know where to forward them. Returns
    {"phone_number": "+1...", "sid": "PN..."}. Raises ProvisioningError if
    none are available for the given area code (try a nearby one, or omit
    area_code to let Twilio pick anywhere)."""
    client = _twilio_client()
    available = client.available_phone_numbers("US").local.list(area_code=area_code, limit=1)
    if not available:
        raise ProvisioningError(
            f"No numbers available for area_code={area_code!r} — try a different area code"
        )
    base_url = os.environ.get("PUBLIC_BASE_URL", DEFAULT_PUBLIC_BASE_URL)
    purchased = client.incoming_phone_numbers.create(
        phone_number=available[0].phone_number,
        sms_url=f"{base_url}/webhook/sms",
        sms_method="POST",
    )
    return {"phone_number": purchased.phone_number, "sid": purchased.sid}


def _get_or_create_xai_trunk(client: TwilioRestClient):
    """Reuses one shared trunk across all clients — the trunk itself is just a
    container Twilio numbers attach to. What's NOT shared is the origination
    URI: per xAI's SIP docs, that URI embeds the specific phone number
    (sip:{number}@sip.voice.x.ai) so xAI can tell which registered number an
    inbound call belongs to. So origination URLs are added per-number by the
    caller, not once here at trunk-creation time."""
    existing = client.trunking.v1.trunks.list(limit=20)
    for trunk in existing:
        if trunk.friendly_name == XAI_TRUNK_FRIENDLY_NAME:
            return trunk
    return client.trunking.v1.trunks.create(friendly_name=XAI_TRUNK_FRIENDLY_NAME)


def attach_number_to_xai_trunk(phone_number_sid: str, phone_number: str) -> None:
    """Assigns a purchased Twilio number to the shared xAI-origination trunk
    and adds that number's own origination URI (xAI's routing requires the
    E.164 number embedded in the SIP URI, per number — see
    docs.x.ai/.../voice-agent/sip's Twilio section), so inbound calls to it
    route to xAI instead of ringing nowhere."""
    client = _twilio_client()
    trunk = _get_or_create_xai_trunk(client)
    client.trunking.v1.trunks(trunk.sid).origination_urls.create(
        friendly_name=f"xAI Voice Agent API - {phone_number}",
        sip_url=f"sip:{phone_number}@sip.voice.x.ai;transport=tls",
        weight=1,
        priority=1,
        enabled=True,
    )
    client.trunking.v1.trunks(trunk.sid).phone_numbers.create(phone_number_sid=phone_number_sid)


def register_number_with_xai(phone_number: str) -> dict:
    """NOT IMPLEMENTED — see module docstring. xAI's number-registration REST
    endpoint isn't in their public docs, so there's nothing correct to write
    here yet. Calling this raises rather than silently no-op'ing or hitting a
    guessed URL that could fail confusingly (or worse, succeed against the
    wrong endpoint) mid-provisioning."""
    raise NotImplementedError(
        "xAI's number-registration API isn't publicly documented. Get the real "
        "endpoint from your xAI dashboard/API reference after signing up, then "
        "implement this — see provisioning.py's module docstring."
    )


def provision_client_number(client: Client, area_code: Optional[str] = None) -> dict:
    """Full flow: buy a Twilio number, wire it to SMS (just by being the
    number — /webhook/sms routes by Client.inbound_number, no extra Twilio
    config needed for that part), attach it to the xAI-origination trunk for
    voice, and register it with xAI.

    Returns the fields to save on the Client: inbound_number,
    twilio_number_sid, xai_phone_number, xai_signing_secret. Caller is
    responsible for persisting them (kept pure/no DB writes here so this is
    easy to test and to retry a failed step without re-buying a number).

    Raises ProvisioningError or NotImplementedError (from the xAI step) on
    failure — a caller should show the error and let the number-buying part's
    result be reused rather than re-purchasing on retry.
    """
    purchase = buy_twilio_number(area_code)
    attach_number_to_xai_trunk(purchase["sid"])
    xai_registration = register_number_with_xai(purchase["phone_number"])

    return {
        "inbound_number": purchase["phone_number"],
        "twilio_number_sid": purchase["sid"],
        "xai_phone_number": purchase["phone_number"],
        "xai_signing_secret": xai_registration["signing_secret"],
    }
