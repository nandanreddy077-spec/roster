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
from deployment import deploy_role
from provisioning import buy_twilio_number, provision_voice


def activate_frontdesk(session: Session, client: Business) -> None:
    if not client.inbound_number:
        try:
            purchase = buy_twilio_number()
            client.inbound_number = purchase["phone_number"]
            client.twilio_number_sid = purchase["sid"]
            # Commit the purchase BEFORE attempting voice: the number is real
            # and billable the moment Twilio returns, so it must be on file even
            # if everything after this fails — otherwise a crash here loses track
            # of a number we are now paying for.
            session.add(client)
            session.commit()
            # Voice is best-effort and SMS-only is a fully working Frontdesk, so
            # this never raises. provision_voice persists the once-only xAI
            # signing secret before any retryable step and records the reason on
            # the business row, so a failure is visible in the founder console
            # (voice_provisioning_error) and retryable rather than silent.
            provision_voice(session, client)
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
    # The Employee row IS the deployment record (audit F1): without this the
    # business shows zero departments until the app restarts and the backfill
    # runs. Best-effort like the provisioning above — activation must always
    # complete — but loud, because a missing row is invisible otherwise.
    try:
        deploy_role(session, client.id, "frontdesk")
    except Exception as e:
        print(f"[activation] failed to create frontdesk employee for business "
              f"{client.id}: {e}", file=sys.stderr)
