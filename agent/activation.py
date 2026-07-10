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
            attach_number_to_xai_trunk(purchase["sid"])
            client.inbound_number = purchase["phone_number"]
            client.twilio_number_sid = purchase["sid"]
            try:
                xai_registration = register_number_with_xai(purchase["phone_number"])
                client.xai_phone_number = purchase["phone_number"]
                client.xai_signing_secret = xai_registration["signing_secret"]
            except NotImplementedError:
                pass  # SMS-only for now; voice needs a manual xAI step — see provisioning.py
        except ProvisioningError:
            pass  # No number purchased (e.g. no Twilio creds in dev) — still go live;
                  # founder can provision a number later from /clients/{id}.

    client.frontdesk_live = True
    client.activated_at = datetime.utcnow()
    session.add(client)
    session.commit()
