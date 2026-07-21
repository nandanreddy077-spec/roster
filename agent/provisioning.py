"""Automates buying a client a phone number and wiring it for both SMS and
live voice, so onboarding a new client doesn't require hand-clicking through
Twilio's console.

Two halves:

1. Twilio (buy number, create/reuse an Elastic SIP Trunk with a per-number
   origination URI pointed at xAI, attach the number) — Twilio's long-stable,
   fully documented REST API.

2. Registering that number with xAI's Voice Agent API (POST /v2/phone-numbers
   with origin=byo_trunk), which creates the incoming-call webhook route and
   returns the per-number webhook signing secret once. Implemented below
   against xAI's now-public SIP docs
   (docs.x.ai/developers/model-capabilities/audio/voice-agent/sip).
"""
import os
from typing import Optional

import httpx
from twilio.rest import Client as TwilioRestClient

from db_models import Business

XAI_TRUNK_FRIENDLY_NAME = "Roster - xAI Voice"
DEFAULT_PUBLIC_BASE_URL = "https://rosterhires.com"
XAI_PHONE_NUMBERS_URL = "https://api.x.ai/v2/phone-numbers"
XAI_INCOMING_CALL_PATH = "/webhook/xai-incoming-call"


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
    try:
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
    except ProvisioningError:
        raise
    except Exception as e:
        # Twilio REST errors (trial account can't buy numbers, no funds, geo-
        # permissions, etc.) are NOT ProvisioningError — wrap them so callers
        # get one exception type to degrade on, with the real reason attached.
        raise ProvisioningError(f"Twilio number purchase failed: {e}") from e
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
    try:
        trunk = _get_or_create_xai_trunk(client)
        client.trunking.v1.trunks(trunk.sid).origination_urls.create(
            friendly_name=f"xAI Voice Agent API - {phone_number}",
            sip_url=f"sip:{phone_number}@sip.voice.x.ai;transport=tls",
            weight=1,
            priority=1,
            enabled=True,
        )
        client.trunking.v1.trunks(trunk.sid).phone_numbers.create(phone_number_sid=phone_number_sid)
    except ProvisioningError:
        raise
    except Exception as e:
        raise ProvisioningError(f"attaching number to xAI trunk failed: {e}") from e


def _extract_signing_secret(payload: dict) -> Optional[str]:
    """xAI's docs say the response "includes a signing secret" but don't name
    the field, so check the plausible locations — both snake_case and
    camelCase, since xAI's confirmed response shape uses camelCase
    (`phoneNumber`). Fail loud (caller raises) if none are present, rather
    than silently storing None — a None secret would make every real webhook
    fail signature verification, invisibly."""
    if not isinstance(payload, dict):
        return None
    keys = (
        "signing_secret", "webhook_signing_secret", "signing_key", "secret",
        "signingSecret", "webhookSigningSecret", "signingKey",
    )
    for key in keys:
        if payload.get(key):
            return payload[key]
    webhook = payload.get("webhook")
    if isinstance(webhook, dict):
        for key in keys:
            if webhook.get(key):
                return webhook[key]
    return None


def register_number_with_xai(phone_number: str) -> dict:
    """Registers a customer-owned (byo_trunk) number with xAI's Voice Agent API
    so inbound SIP calls to it open a realtime voice session, and points xAI's
    incoming-call webhook at our /webhook/xai-incoming-call route. Returns
    {"signing_secret": ..., "xai_phone_number": ...}; the caller persists the
    secret (used to verify each call webhook — see xai_voice_adapter.py).

    Requires XAI_API_KEY. Optionally reads XAI_SIP_ALLOWED_ADDRESSES (comma-
    separated CIDRs — Twilio's SIP signaling ranges) for the IP-allowlist auth
    method; omitted if unset. Raises ProvisioningError on any failure so a
    caller can fall back to SMS-only rather than half-provisioning."""
    api_key = os.environ.get("XAI_API_KEY")
    if not api_key:
        raise ProvisioningError("XAI_API_KEY must be set to register a number for live voice")

    base_url = os.environ.get("PUBLIC_BASE_URL", DEFAULT_PUBLIC_BASE_URL)
    body = {
        "origin": "byo_trunk",
        "name": f"Roster {phone_number}",
        "phone_number": phone_number,
        "webhook": {
            "name": f"Roster incoming call {phone_number}",
            "url": f"{base_url}{XAI_INCOMING_CALL_PATH}",
        },
    }
    allowlist = os.environ.get("XAI_SIP_ALLOWED_ADDRESSES", "").strip()
    if allowlist:
        body["sip_auth"] = {
            "allowed_addresses": [a.strip() for a in allowlist.split(",") if a.strip()]
        }

    try:
        resp = httpx.post(
            XAI_PHONE_NUMBERS_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
            timeout=30.0,
        )
        if resp.status_code == 409:
            # xAI's docs say the signing secret is "returned only once" and
            # there's no documented GET/rotate endpoint -- so a 409 (number
            # already registered, almost always from an earlier attempt whose
            # response we failed to parse) is not retryable for this number.
            # Fail with an actionable message instead of the generic 409 text.
            raise ProvisioningError(
                f"xAI already has {phone_number} registered from an earlier attempt, and its "
                "signing secret can only be retrieved once — it's unrecoverable now. Retrying "
                "won't help; provision a different phone number instead."
            )
        resp.raise_for_status()
        payload = resp.json()
    except httpx.HTTPError as e:
        raise ProvisioningError(f"xAI number registration failed: {e}") from e

    secret = _extract_signing_secret(payload)
    if not secret:
        webhook = payload.get("webhook") if isinstance(payload, dict) else None
        raise ProvisioningError(
            "xAI registration returned no signing secret — cannot verify call webhooks. "
            f"Response keys: {sorted(payload) if isinstance(payload, dict) else type(payload)}; "
            f"webhook keys: {sorted(webhook) if isinstance(webhook, dict) else webhook}"
        )
    return {"signing_secret": secret, "xai_phone_number": phone_number}


def provision_client_number(client: Business, area_code: Optional[str] = None) -> dict:
    """Full flow: buy a Twilio number, wire it to SMS (just by being the
    number — /webhook/sms routes by Business.inbound_number, no extra Twilio
    config needed for that part), attach it to the xAI-origination trunk for
    voice, and register it with xAI.

    Returns the fields to save on the Business: inbound_number,
    twilio_number_sid, xai_phone_number, xai_signing_secret. Caller is
    responsible for persisting them (kept pure/no DB writes here so this is
    easy to test and to retry a failed step without re-buying a number).

    Raises ProvisioningError on failure — a caller should show the error and
    let the number-buying part's result be reused rather than re-purchasing
    on retry.
    """
    purchase = buy_twilio_number(area_code)
    attach_number_to_xai_trunk(purchase["sid"], purchase["phone_number"])
    xai_registration = register_number_with_xai(purchase["phone_number"])

    return {
        "inbound_number": purchase["phone_number"],
        "twilio_number_sid": purchase["sid"],
        "xai_phone_number": purchase["phone_number"],
        "xai_signing_secret": xai_registration["signing_secret"],
    }
