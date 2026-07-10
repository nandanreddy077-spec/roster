"""Trial spend cap for self-serve signups. A caller (soft-buffer exhausted)
is never silently dropped mid-conversation; the buffer exists so one
expensive call doesn't cut off a live customer the moment the hard cap is
crossed. When a client first crosses the hard cap, the founder gets a
one-time best-effort SMS alert. See
docs/superpowers/specs/2026-07-10-self-serve-signup-dashboard-design.md.
"""
import os

from sqlmodel import Session

from channels import get_channel
from db_models import Client

# Flat per-turn estimate, not real per-token billing — matches the founder's
# own ~$0.30-0.60-per-call all-in cost research (Twilio + orchestration +
# Anthropic) closely enough to be a meaningful cap, while staying
# deterministic and easy to test.
TRIAL_TURN_COST_CENTS = 50

# Module-level so tests can monkeypatch trial_cap.sms_channel with a recorder,
# the same way service.py exposes `agent`.
sms_channel = get_channel()


def can_respond(client: Client) -> bool:
    return client.trial_spend_cents < (client.trial_cap_cents + client.trial_soft_buffer_cents)


def record_usage(session: Session, client: Client, cost_cents: int = TRIAL_TURN_COST_CENTS) -> None:
    client.trial_spend_cents += cost_cents
    # Fire the founder alert only on the first turn that reaches the hard cap.
    crossed_cap = not client.trial_cap_notified and client.trial_spend_cents >= client.trial_cap_cents
    if crossed_cap:
        client.trial_cap_notified = True
    session.add(client)
    session.commit()
    if crossed_cap:
        _notify_founder_cap_reached(client)


def _notify_founder_cap_reached(client: Client) -> None:
    """Best-effort founder alert. Never raises — a failed alert must not break
    the customer's turn (same best-effort posture as the Reviews SMS in
    app.py). If FOUNDER_ALERT_PHONE isn't set, we simply don't alert."""
    founder_phone = os.environ.get("FOUNDER_ALERT_PHONE")
    if not founder_phone:
        return
    try:
        sms_channel.send(
            from_number=client.inbound_number or "",
            to_number=founder_phone,
            body=(
                f"Roster alert: {client.business_name or 'a trial client'} hit its "
                f"${client.trial_cap_cents / 100:.0f} trial cap "
                f"(${client.trial_spend_cents / 100:.2f} spent). Raise the cap or move them to paid."
            ),
        )
    except Exception as e:
        print(f"Trial cap: failed to notify founder for client {client.id}: {e}")
