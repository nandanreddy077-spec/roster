"""Bug B: a business past its trial cap kept answering the phone.

Every other live employee gates on trial_cap.can_respond — Frontdesk over
SMS, Quote Chaser, Reviews, Membership Agent, Referral. Voice did not, so a
capped account's texts went silent while its phone line kept answering and
kept spending. That is the worst possible shape for the failure: the owner
sees the cheap channel stop and the expensive one continue.

The fix deliberately does NOT refuse the call. trial_cap.py's own contract is
that a caller is "never silently dropped mid-conversation" — hanging up on a
real customer to save money is a worse outcome than the spend. A capped call
instead gets a much smaller duration and token budget: enough to take the
caller's number and hand off, not enough to run an unbounded bill.
"""

import asyncio

from db_models import BILLING_PAID, Business
from sqlmodel import Session


def _capped(**over) -> Business:
    """A trial business already past its hard cap + soft buffer."""
    b = Business(
        business_name="Ridgeline Plumbing",
        trade="Plumbing",
        escalation_phone="512-555-0148",
        inbound_number="+15125550100",
        xai_phone_number="+15125550100",
        frontdesk_live=True,
    )
    b.trial_spend_cents = b.trial_cap_cents + b.trial_soft_buffer_cents + 100
    for k, v in over.items():
        setattr(b, k, v)
    return b


def test_a_capped_account_gets_a_smaller_call_budget():
    from xai_voice_adapter import (
        CAPPED_CALL_MAX_DURATION_SECONDS,
        CAPPED_CALL_MAX_TOKEN_BUDGET,
        MAX_CALL_DURATION_SECONDS,
        MAX_CALL_TOKEN_BUDGET,
        call_budget_for,
    )

    duration, tokens = call_budget_for(_capped(), None, None)

    assert duration == CAPPED_CALL_MAX_DURATION_SECONDS
    assert tokens == CAPPED_CALL_MAX_TOKEN_BUDGET
    assert duration < MAX_CALL_DURATION_SECONDS
    assert tokens < MAX_CALL_TOKEN_BUDGET


def test_a_paying_account_is_never_capped():
    """A paid account is not on trial. Clamping its calls would be an outage,
    not a safety net — the same reasoning as can_respond's BILLING_PAID case."""
    from xai_voice_adapter import (
        MAX_CALL_DURATION_SECONDS,
        MAX_CALL_TOKEN_BUDGET,
        call_budget_for,
    )

    paid = _capped(billing_state=BILLING_PAID)
    duration, tokens = call_budget_for(paid, None, None)

    assert duration == MAX_CALL_DURATION_SECONDS
    assert tokens == MAX_CALL_TOKEN_BUDGET


def test_a_trial_account_under_its_cap_gets_the_full_budget():
    from xai_voice_adapter import (
        MAX_CALL_DURATION_SECONDS,
        MAX_CALL_TOKEN_BUDGET,
        call_budget_for,
    )

    under = _capped()
    under.trial_spend_cents = 0
    duration, tokens = call_budget_for(under, None, None)

    assert duration == MAX_CALL_DURATION_SECONDS
    assert tokens == MAX_CALL_TOKEN_BUDGET


def test_an_explicit_override_still_wins_over_the_cap():
    """Tests pass tiny budgets; an override must stay authoritative, and must
    never be raised by the cap logic."""
    from xai_voice_adapter import call_budget_for

    duration, tokens = call_budget_for(_capped(), 5.0, 50)

    assert duration == 5.0
    assert tokens == 50


def test_run_call_actually_applies_the_cap(test_engine):
    """The guard must be WIRED, not merely present. This repo's recurring
    failure mode is code that exists but nothing reaches — Referral has a
    complete engine that can never run. Assert the real run_call path records
    the capped budget."""
    from call_trace import CallTrace
    from xai_voice_adapter import CAPPED_CALL_MAX_DURATION_SECONDS, run_call

    from tests.test_voice_loop_integration import GREETING_DONE, FakeWS, connector_for

    with Session(test_engine) as s:
        client = _capped()
        s.add(client)
        s.commit()
        s.refresh(client)

    trace = CallTrace("call_capped")
    ws = FakeWS([GREETING_DONE])

    asyncio.run(
        run_call(
            "call_capped",
            client,
            "+15125559999",
            lambda: Session(test_engine),
            connect=connector_for(ws),
            trace=trace,
        )
    )

    capped = [r for r in trace.records if r.get("stage") == "trial_cap_reached"]
    assert capped, f"no trial_cap_reached stage: {[r.get('stage') for r in trace.records]}"
    assert capped[0]["max_duration_seconds"] == CAPPED_CALL_MAX_DURATION_SECONDS
