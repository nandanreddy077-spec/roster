"""Self-serve activation: goes from a filled-in onboarding profile to a live
Frontdesk. Mirrors the tolerance app.py's founder-facing
/clients/{id}/provision-number route already has for xAI's voice
registration not being implemented yet (see provisioning.py) — SMS-only is
still a fully working Frontdesk, and a missing Twilio number is something the
founder can provision later from the admin dashboard.
"""
from datetime import datetime

from sqlmodel import Session

from db_models import Client
from provisioning import ProvisioningError, attach_number_to_xai_trunk, buy_twilio_number, register_number_with_xai


def activate_frontdesk(session: Session, client: Client) -> None:
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
            except ProvisioningError:
                pass  # SMS-only (e.g. no XAI_API_KEY); founder can finish voice from /clients/{id}.
        except ProvisioningError:
            pass  # No number purchased (e.g. no Twilio creds in dev) — still go live SMS-less;
                  # founder can provision a number later from /clients/{id}.

    client.frontdesk_live = True
    client.activated_at = datetime.utcnow()
    session.add(client)
    session.commit()
