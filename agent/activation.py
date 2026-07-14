"""Self-serve activation: goes from a filled-in onboarding profile to a live
Frontdesk. Mirrors the tolerance app.py's founder-facing
/clients/{id}/provision-number route already has for voice provisioning
failures (e.g. missing XAI_API_KEY, or the xAI call itself failing) — SMS-only
is still a fully working Frontdesk, and voice can be finished later from the
admin dashboard.
"""
import sys
from datetime import datetime

from sqlmodel import Session

from db_models import Business
from provisioning import attach_number_to_xai_trunk, buy_twilio_number, register_number_with_xai


def activate_frontdesk(session: Session, client: Business) -> None:
    if not client.inbound_number:
        try:
            purchase = buy_twilio_number()
            client.inbound_number = purchase["phone_number"]
            client.twilio_number_sid = purchase["sid"]
            # Voice is best-effort. Register with xAI first; only route the
            # Twilio number's voice to xAI once that succeeds, so a failed
            # registration leaves a fully working SMS-only number rather than
            # voice calls routed to a number xAI doesn't recognize.
            try:
                xai_registration = register_number_with_xai(purchase["phone_number"])
                attach_number_to_xai_trunk(purchase["sid"], purchase["phone_number"])
                client.xai_phone_number = purchase["phone_number"]
                client.xai_signing_secret = xai_registration["signing_secret"]
            except Exception as e:
                # SMS-only (e.g. no XAI_API_KEY, or xAI/trunk error); founder can
                # finish voice from /clients/{id}. Log loudly — must never crash
                # onboarding, but the reason must be visible.
                print(f"[activation] voice provisioning failed for business {client.id}: {e}", file=sys.stderr)
        except Exception as e:
            # No number purchased (no Twilio creds, trial account can't buy a
            # number, no funds, geo-permissions, etc.) — still go live SMS-less;
            # founder can provision a number later from /clients/{id}. Onboarding
            # must ALWAYS complete, never 500 on a provisioning failure.
            print(f"[activation] number provisioning failed for business {client.id}: {e}", file=sys.stderr)

    client.frontdesk_live = True
    client.activated_at = datetime.utcnow()
    session.add(client)
    session.commit()
