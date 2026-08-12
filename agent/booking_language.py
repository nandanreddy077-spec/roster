"""The one place in the codebase allowed to decide what "confirmed" means.

Found live in production language (Milestone 2 booking-honesty audit,
2026-08-11): recovery_service told a customer "Perfect, you're booked for
Wednesday 9am-12pm!" off a slot ManualCalendarProvider invented — never
checked against a real technician, truck, or calendar. The customer had
every reason to expect a truck at that door at that time. Nobody was coming
unless the owner happened to notice and act.

The fix is not a calendar integration — that's a different, much larger
project, and building one wasn't what the trust problem needed. The fix is
that no code path may claim a booking is CONFIRMED unless
db_models.BOOKING_CONFIRMED is genuinely true, and there is exactly one
honest way to say each of the other two states out loud.

Every customer-facing string that mentions a specific appointment time goes
through render_slot_language() or gets checked by assert_no_confirmation_claim()
in a test. Two different enforcement mechanisms for two different kinds of
text:

  - Deterministic strings (recovery_service's slot-confirmation reply,
    review/membership/referral templates) are 100% under code control, so
    render_slot_language() is a hard, testable guarantee — call it with the
    wrong status for confirmation wording and it raises.
  - LLM freeform text (Frontdesk's voice/SMS replies) cannot be constrained
    this way; a model generates prose, not a fixed template. Honesty there is
    enforced through an explicit, strongly-worded prompt instruction
    (engine.py's _CONFIRMATION_HONESTY_NOTE) — the same enforcement class
    this codebase already uses for other model-authored-text invariants (the
    voice prompt's "never say transfer" rule), verified by real inbound
    conversations against real Claude, not just source review.
"""

from db_models import BOOKING_PROPOSED, BOOKING_REQUESTED

# Every phrase below claims, as fact, that a specific appointment time is
# locked in — as opposed to requested, offered, or picked-but-unverified.
# Checked case-insensitively. Deliberately phrase-level, not word-level
# ("confirm" alone would also flag "please confirm your address", which is a
# completely different, harmless use of the word) — see
# test_booking_honesty.py for both directions of that distinction.
CONFIRMATION_PHRASES = (
    "you're booked for",
    "you are booked for",
    "your appointment is confirmed",
    "your appointment is set",
    "your booking is confirmed",
    "it's confirmed",
    "confirmed for",
    "we'll see you at",
    "we will see you at",
    "see you then",
    "you're all set for",
    "you are all set for",
    "locked in for",
    "your slot is confirmed",
)


class BookingLanguageError(ValueError):
    """A customer-facing string claimed a confirmed appointment without the
    booking actually being confirmed."""


def assert_no_confirmation_claim(text: str) -> None:
    """Raise if `text` claims a firm, confirmed appointment time. Call this
    on any candidate customer-facing string in a test, so a future edit that
    reintroduces "you're booked for Wednesday!" fails loudly in CI instead of
    shipping quietly to a real customer."""
    lowered = text.lower()
    hit = next((p for p in CONFIRMATION_PHRASES if p in lowered), None)
    if hit:
        raise BookingLanguageError(
            f"customer-facing text claims a confirmed appointment ({hit!r}) "
            f"without a Confirmed booking status: {text!r}"
        )


def render_slot_language(status: str, slot_text: str) -> str:
    """The one honest sentence for 'here is the time in play', for each real
    status. Raises for BOOKING_CONFIRMED: nothing in this codebase can reach
    that status automatically (see db_models.py's BOOKING_CONFIRMED
    docstring), so no caller should ever be asking this function to phrase
    one — the one route that sets booking_status=confirmed does so through a
    founder action, not a customer-facing send, and has no reason to call
    this function at all.
    """
    if status == BOOKING_PROPOSED:
        return (
            f"Got it — {slot_text} works! I've noted that down and the "
            "office will reach out to confirm."
        )
    if status == BOOKING_REQUESTED:
        return (
            f"Got it — {slot_text} noted as what works best for you. "
            "Someone from the office will be in touch to confirm a time."
        )
    raise BookingLanguageError(
        f"render_slot_language() has no honest confirmation-claiming sentence to "
        f"give you, on purpose — status={status!r} is not something this function "
        "generates language for. A genuinely confirmed appointment goes through "
        "render_confirmed_language(), which booking_manager alone calls, after a "
        "real human has actually verified the time."
    )


# ---- the one door confirmation language may come through --------------------
#
# Milestone A gave BOOKING_CONFIRMED its first legitimate customer-facing
# message: the owner has now actually confirmed, and the customer — who was
# told "the office will confirm and text you back" — has to hear about it, or
# the booking loop still never ends.
#
# This is deliberately a SEPARATE function rather than a fourth branch in
# render_slot_language(). That function keeps raising for BOOKING_CONFIRMED,
# so the accidental-caller guard it has always provided is unchanged: a caller
# that wanders in with a status variable still cannot produce confirmation
# language by accident. Reaching this text requires naming this function, and
# the name makes misuse obvious in review.
#
# These sentences contain phrases from CONFIRMATION_PHRASES on purpose — that
# is what makes them confirmation language. This module is the one file the
# static audit excludes, for exactly this reason.


def render_confirmed_language(slot_text: str) -> str:
    """The message a customer gets when the owner has genuinely confirmed.
    Callers must have already moved the booking to BOOKING_CONFIRMED — this
    function trusts its caller, which is why booking_manager is the only one."""
    return (
        f"Good news — you're confirmed for {slot_text}. "
        "See you then! Reply here if anything changes."
    )


def render_cancelled_language(slot_text: str) -> str:
    """The booking is off. Says so plainly and leaves the door open, without
    inventing a reason (the owner's note is internal) and without promising a
    replacement time nobody has agreed to yet."""
    when = f" for {slot_text}" if slot_text else ""
    return (
        f"Sorry — we're not able to make it work{when} after all. "
        "Someone from the office will be in touch if you'd like to find another time."
    )


def render_owner_proposed_language(slot_text: str) -> str:
    """The owner offered a different window. Asks rather than tells: nobody has
    agreed to this yet, so it must not read as a booking."""
    return f"Update from the office — the closest we can do is {slot_text}. Does that work for you?"
