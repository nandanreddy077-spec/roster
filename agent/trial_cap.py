"""Trial spend cap for self-serve signups. A caller (soft-buffer exhausted)
is never silently dropped mid-conversation; the buffer exists so one
expensive call doesn't cut off a live customer the moment the hard cap is
crossed. See docs/superpowers/specs/2026-07-10-self-serve-signup-dashboard-design.md.
"""
from sqlmodel import Session

from db_models import Client

# Flat per-turn estimate, not real per-token billing — matches the founder's
# own ~$0.30-0.60-per-call all-in cost research (Twilio + orchestration +
# Anthropic) closely enough to be a meaningful cap, while staying
# deterministic and easy to test.
TRIAL_TURN_COST_CENTS = 50


def can_respond(client: Client) -> bool:
    return client.trial_spend_cents < (client.trial_cap_cents + client.trial_soft_buffer_cents)


def record_usage(session: Session, client: Client, cost_cents: int = TRIAL_TURN_COST_CENTS) -> None:
    client.trial_spend_cents += cost_cents
    session.add(client)
    session.commit()
