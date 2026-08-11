"""Trial spend cap for self-serve signups. A caller (soft-buffer exhausted)
is never silently dropped mid-conversation; the buffer exists so one
expensive call doesn't cut off a live customer the moment the hard cap is
crossed. When a client first crosses the hard cap, the founder gets a
one-time best-effort SMS alert. See
docs/superpowers/specs/2026-07-10-self-serve-signup-dashboard-design.md.
"""

import os

from sqlalchemy import update as sa_update
from sqlmodel import Session

from channels import get_channel
from db_models import BILLING_PAID, Business

# Flat per-turn estimate, not real per-token billing — matches the founder's
# own ~$0.30-0.60-per-call all-in cost research (Twilio + orchestration +
# Anthropic) closely enough to be a meaningful cap, while staying
# deterministic and easy to test.
TRIAL_TURN_COST_CENTS = 50

# Module-level so tests can monkeypatch trial_cap.sms_channel with a recorder,
# the same way service.py exposes `agent`.
sms_channel = get_channel()


def can_respond(client: Business) -> bool:
    """Whether this business's employees may spend money on a turn.

    A paying customer is never gated. The cap is a guard against an unattended
    trial running up a bill, and it silently disabled five of the six live
    employees — Frontdesk over SMS, plus Quote Chaser, Reviews, Membership
    Agent and Referral replies — the moment it was crossed. On a paid account
    that is not a safety net, it is an outage.
    """
    if client.billing_state == BILLING_PAID:
        return True
    return client.trial_spend_cents < (client.trial_cap_cents + client.trial_soft_buffer_cents)


def record_usage(
    session: Session, client: Business, cost_cents: int = TRIAL_TURN_COST_CENTS
) -> None:
    # Atomic in-database increment: concurrent turns each add their cost even
    # when both loaded the same stale Business row (a read-modify-write here
    # silently loses updates under concurrency).
    session.execute(
        sa_update(Business)
        .where(Business.id == client.id)
        .values(trial_spend_cents=Business.trial_spend_cents + cost_cents)
    )
    # Spend is still tracked on a paid account — knowing what a customer costs
    # is the input to pricing — but there is no cap to cross, so no alert.
    if client.billing_state == BILLING_PAID:
        session.commit()
        session.refresh(client)
        return
    # Claim the one-time founder alert in the same atomic style: only the turn
    # whose conditional UPDATE actually flips trial_cap_notified sends it, so
    # two racing turns can never double-alert (and none can miss it).
    claimed = session.execute(
        sa_update(Business)
        .where(
            Business.id == client.id,
            Business.trial_cap_notified == False,  # noqa: E712 — SQL expression
            Business.trial_spend_cents >= Business.trial_cap_cents,
        )
        .values(trial_cap_notified=True)
    )
    session.commit()
    session.refresh(client)
    if claimed.rowcount == 1:
        _notify_founder_cap_reached(client)
        _notify_owner_cap_reached(session, client)


def _notify_owner_cap_reached(session, client: Business) -> None:
    """Tell the OWNER their office has gone quiet, and record it.

    Before this, crossing the cap paged the founder and did nothing else: the
    owner's employees stopped replying with no reply to the customer, no alert,
    and nothing in their notifications feed. The failure was invisible from the
    only side that matters. Gated by the same one-time trial_cap_notified claim
    as the founder alert, so it cannot repeat.

    Best-effort throughout, same posture as every other owner alert — the
    customer's turn has already been decided and must not fail on a notice.
    """
    from notifications import (
        KIND_TRIAL_CAP_REACHED,
        SOURCE_TRIAL_CAP,
        record_owner_notification,
    )

    message = (
        f"Your Roster trial has hit its usage limit, so your AI employees have "
        f"paused answering. Nothing is lost — get in touch and we'll switch you on."
    )
    delivered = False
    if client.escalation_phone:
        try:
            sms_channel.send(
                from_number=client.inbound_number or "",
                to_number=client.escalation_phone,
                body=message,
            )
            delivered = True
        except Exception as e:
            print(f"Trial cap: failed to notify owner for client {client.id}: {e}")
    record_owner_notification(
        session, client.id, KIND_TRIAL_CAP_REACHED, SOURCE_TRIAL_CAP, message, delivered
    )


def _notify_founder_cap_reached(client: Business) -> None:
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
