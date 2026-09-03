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

import logging
import os
from datetime import datetime, timedelta
from typing import Optional

import httpx
from db_models import Business
from sqlalchemy import or_
from sqlalchemy import update as sa_update
from sqlmodel import Session, col
from twilio.rest import Client as TwilioRestClient

logger = logging.getLogger(__name__)

XAI_TRUNK_FRIENDLY_NAME = "Roster - xAI Voice"
DEFAULT_PUBLIC_BASE_URL = "https://rosterhires.com"
XAI_PHONE_NUMBERS_URL = "https://api.x.ai/v2/phone-numbers"
XAI_INCOMING_CALL_PATH = "/webhook/xai-incoming-call"

# How long a provisioning claim is honored before it's treated as abandoned.
# buy_twilio_number + the xAI round-trips finish in seconds; anything still
# "in progress" after this is a process that died mid-purchase. Long enough
# that a slow-but-live request is never stolen from, short enough that a real
# crash doesn't wedge a customer's provisioning until someone notices.
_PROVISIONING_CLAIM_TTL = timedelta(minutes=10)


def public_base_url() -> str:
    """Where this app lives, publicly. THE one answer — webhook registration,
    the Twilio signature check and the founder's access link must all build
    URLs against the same host or they disagree silently: a signature check
    against a host Twilio wasn't configured with 403s every inbound text, and
    nothing says why. PUBLIC_BASE_URL is unset in production today and the
    fallback below is the live domain, so setting it changes nothing — which
    is exactly why it must stay one constant instead of four spellings."""
    return os.environ.get("PUBLIC_BASE_URL") or DEFAULT_PUBLIC_BASE_URL


class ProvisioningError(Exception):
    pass


def provisioning_allowed(business) -> bool:
    """Whether Roster may spend money buying this business a Twilio number.

    True once EITHER a payment method is verified (Stripe, later) OR a founder
    has explicitly unlocked it from the console (the free hand-onboarded first
    cohort). Self-serve /signup was closed on 2026-08-06 because the wizard
    bought a number for anyone; this is the backend gate that lets it reopen
    (docs/PRODUCTION_READINESS.md P1-1). Every purchase path — provision_number,
    retry_xai_registration, and the self-serve activation flow — checks it.

    Duck-typed on the two attributes so it unit-tests with a bare object and
    never needs the row loaded from a particular session.
    """
    return (
        getattr(business, "payment_method_verified_at", None) is not None
        or getattr(business, "provisioning_unlocked_at", None) is not None
    )


def claim_provisioning(session: Session, business_id: int) -> bool:
    """Atomically claim the right to buy a Twilio number for this business.

    Returns True exactly once per business-without-a-number. A concurrent or
    repeated call returns False. This is the fix for the concurrent double-buy
    (docs/PRODUCTION_READINESS.md P0-1): the old `if client.twilio_number_sid`
    guard is a read-then-act check that two in-flight requests both pass.

    The claim is a single conditional UPDATE — the same claim-before-act
    pattern recovery_service.tick, booking_manager._claim and
    trial_cap.record_usage already use — so exactly one of two racing callers
    gets rowcount 1.

    A claim older than _PROVISIONING_CLAIM_TTL with still no twilio_number_sid
    is re-claimable: the process that made it died mid-purchase, and wedging a
    customer's provisioning forever is worse than the small window where a
    genuinely-slow request could be double-run (buy_twilio_number itself is
    idempotent only by luck, but the TTL is far longer than it ever takes).
    """
    cutoff = datetime.utcnow() - _PROVISIONING_CLAIM_TTL
    claimed = session.execute(
        sa_update(Business)
        .where(
            col(Business.id) == business_id,
            col(Business.twilio_number_sid).is_(None),
            or_(
                col(Business.provisioning_started_at).is_(None),
                col(Business.provisioning_started_at) < cutoff,
            ),
        )
        .values(provisioning_started_at=datetime.utcnow())
    )
    session.commit()
    return claimed.rowcount == 1  # type: ignore[attr-defined]


def release_provisioning_claim(session: Session, business_id: int) -> None:
    """Undo a claim after a FAILED purchase, so a retry can re-claim at once
    instead of waiting out the TTL. Scoped to `twilio_number_sid IS NULL`: if
    the purchase actually succeeded (and only a later step failed), the SID is
    already on file and both blocks re-claim and marks the number as real —
    releasing the claim there would be wrong."""
    session.execute(
        sa_update(Business)
        .where(
            col(Business.id) == business_id,
            col(Business.twilio_number_sid).is_(None),
        )
        .values(provisioning_started_at=None)
    )
    session.commit()


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
        base_url = public_base_url()
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


def provision_voice(session, client: Business) -> Optional[str]:
    """Take a business that already owns a Twilio number all the way to a
    call-answering voice line. Returns None on success, or an error string.

    THE ORDERING HERE IS THE WHOLE POINT — it is a correctness constraint, not
    style. xAI returns a number's webhook signing secret exactly ONCE, at
    registration: there is no read-back endpoint (GET /v2/webhooks is a 404),
    and re-registering the same number answers 409 (see register_number_with_xai).
    So the secret is irreplaceable, while attaching the number to a Twilio SIP
    trunk is an ordinary retryable API call.

    All three call sites used to run:

        registration = register_number_with_xai(...)   # once-only secret
        attach_number_to_xai_trunk(...)                # retryable, and it threw
        client.xai_signing_secret = registration[...]  # never reached

    which destroyed an irreplaceable secret whenever a retryable step failed,
    permanently bricking the number for voice — the only recovery being to buy
    a different one. That is what happened to +16187473488 (registered with
    xAI 2026-07-21, no Twilio trunk ever created, no secret stored).

    So: persist the secret the instant it exists, THEN do the retryable work.
    Every step after registration can be re-run safely, which is what makes
    this function idempotent — a second call with a secret already stored skips
    registration entirely and just finishes the trunk wiring.
    """
    if not client.inbound_number or not client.twilio_number_sid:
        return "No purchased Twilio number to wire up for voice yet."

    try:
        if not client.xai_signing_secret:
            registration = register_number_with_xai(client.inbound_number)
            # COMMIT BEFORE the retryable step below. If the process dies on the
            # next line, the secret is still on disk and this is resumable.
            client.xai_phone_number = client.inbound_number
            client.xai_signing_secret = registration["signing_secret"]
            session.add(client)
            session.commit()

        attach_number_to_xai_trunk(client.twilio_number_sid, client.inbound_number)
    except ProvisioningError as e:
        return _record_voice_error(session, client, str(e))
    except Exception as e:
        # Anything unexpected is still a failed provision, not a crashed
        # signup: record it and degrade to SMS-only like the callers expect.
        return _record_voice_error(session, client, f"unexpected error: {e}")

    _record_voice_error(session, client, None)
    return None


def _record_voice_error(session, client: Business, error: Optional[str]) -> Optional[str]:
    """Persist (or clear) the last voice-provisioning failure and return it, so
    a failure is visible in the founder console instead of only on stderr."""
    client.voice_provisioning_error = error
    session.add(client)
    session.commit()
    if error:
        logger.error("voice provisioning failed", extra={"business_id": client.id, "error": error})
    return error


def verify_voice_wiring(client: Business) -> dict:
    """Read-only preflight: is this number actually able to receive a call?

    Checks the four independent things that must ALL be true, because each can
    fail on its own and three of them are invisible from our own database:
    xAI registration, our stored signing secret, the number's Twilio trunk
    attachment, and that trunk's origination URI pointing at xAI.

    Returns {"ready": bool, "checks": {name: (ok, detail)}}. Makes no changes.
    A True here means the wiring is complete — it does NOT mean a call has been
    proven to work end to end; only a real phone call establishes that.
    """
    checks: dict = {}

    checks["signing_secret_stored"] = (
        bool(client.xai_signing_secret),
        "stored"
        if client.xai_signing_secret
        else "MISSING — webhooks cannot be verified; the number must be re-registered",
    )
    checks["xai_number_stored"] = (
        bool(client.xai_phone_number),
        client.xai_phone_number or "MISSING — inbound calls cannot be routed to this business",
    )

    number = client.xai_phone_number or client.inbound_number
    try:
        registered = {n["phoneNumber"] for n in _list_xai_numbers()}
        checks["registered_with_xai"] = (
            number in registered,
            "registered" if number in registered else f"{number} not registered with xAI",
        )
    except Exception as e:
        checks["registered_with_xai"] = (False, f"could not check: {e}")

    try:
        twilio = _twilio_client()
        attached = False
        origination = False
        for trunk in twilio.trunking.v1.trunks.list(limit=20):
            numbers = twilio.trunking.v1.trunks(trunk.sid).phone_numbers.list(limit=50)
            if any(n.phone_number == number for n in numbers):
                attached = True
                urls = twilio.trunking.v1.trunks(trunk.sid).origination_urls.list(limit=50)
                origination = any(number in (u.sip_url or "") and u.enabled for u in urls)
                break
        checks["twilio_trunk_attached"] = (
            attached,
            "attached" if attached else "NOT attached to any SIP trunk — calls reach nothing",
        )
        checks["trunk_origination_uri"] = (
            origination,
            "points at xAI"
            if origination
            else "no enabled origination URI for this number — calls cannot reach xAI",
        )
    except Exception as e:
        checks["twilio_trunk_attached"] = (False, f"could not check: {e}")
        checks["trunk_origination_uri"] = (False, f"could not check: {e}")

    return {"ready": all(ok for ok, _ in checks.values()), "checks": checks}


def _list_xai_numbers() -> list:
    api_key = os.environ.get("XAI_API_KEY")
    if not api_key:
        raise ProvisioningError("XAI_API_KEY is not set")
    resp = httpx.get(
        XAI_PHONE_NUMBERS_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json().get("phoneNumbers", [])


def _extract_signing_secret(payload: dict) -> Optional[str]:
    """Pull the webhook signing secret out of xAI's registration response.

    CONFIRMED AGAINST A LIVE REGISTRATION (2026-08-04): the real shape is

        {"phoneNumber": {...}, "webhook": {"dispatchSigningSecret": "...",
                                           "webhookId": "..."}}

    `dispatchSigningSecret` matched none of the names originally guessed from
    the docs, which say only that the response "includes a signing secret".
    That miss cost a burned registration — the secret is returned exactly once,
    so failing to read it is indistinguishable from never having registered.

    So this matches on SHAPE rather than an exact name list: any string field
    whose key contains "secret", at the top level or inside `webhook`. A future
    rename (dispatch_signing_secret, signingSecret, ...) keeps working instead
    of costing another number. Still fails loud when nothing matches — the
    caller raises rather than storing None, because a None secret would make
    every real webhook fail verification, invisibly.
    """
    if not isinstance(payload, dict):
        return None
    for scope in (payload, payload.get("webhook")):
        if not isinstance(scope, dict):
            continue
        for key, value in scope.items():
            if "secret" in key.lower() and isinstance(value, str) and value:
                return value
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

    base_url = public_base_url()
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
