"""Who has told this business to stop texting them.

Before 2026-09-01 there was no such record. STOP was handled in exactly two of
the six inbound reply handlers (Quote Chaser and the Membership Agent), and
even there it only settled the ONE sequence the customer happened to be
replying to. So a customer who texted STOP to a quote follow-up still received
a review ask the next week, a referral ask after that, and a membership offer
after that — each from a different employee, none of which had any idea the
person had already asked to be left alone.

channels.py's own comment already asserted the invariant this module makes
true: "Every employee that runs an outbound sequence checks the SAME set: a
second copy drifting out of date is how a business ends up texting someone who
opted out." The set was shared; the *decision* was not.

Scope is (business, phone), like everything else in Roster: opting out of one
shop is not opting out of another, and the security boundary is business
isolation.

WHAT THIS DOES NOT COVER: replies to a conversation the customer themselves
started. A person who texts the shop's line and gets an answer is not being
solicited, and an opt-out must not turn Frontdesk mute on someone actively
asking for help. Only PROACTIVE sends — the ones Roster initiates — are gated.
"""

import logging
from datetime import datetime
from typing import Optional

from channels import STOP_KEYWORDS, normalize_phone
from db_models import Customer
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

# The words that undo it. Twilio honours START/UNSTOP natively at the carrier
# level, so a customer who sends one expects texts to resume — if our own
# record still said "opted out" we would stay silent for a person who has
# explicitly asked us not to.
START_KEYWORDS = {"start", "unstop", "yes"}


def is_stop(text: str) -> bool:
    return (text or "").strip().lower() in STOP_KEYWORDS


def is_start(text: str) -> bool:
    return (text or "").strip().lower() in START_KEYWORDS


def _row(session: Session, business_id: int, phone: str) -> Optional[Customer]:
    # Matched on the normalized number: Twilio delivers E.164, but a Customer
    # row created from a voice booking's transcribed callback number may not
    # be, so comparing raw strings would silently miss (the same failure class
    # as app._is_owner's, and as bookings._normalized's).
    wanted = normalize_phone(phone or "")
    rows = session.exec(select(Customer).where(Customer.business_id == business_id)).all()
    return next((c for c in rows if normalize_phone(c.phone or "") == wanted), None)


def record_opt_out(session: Session, business_id: int, phone: str) -> None:
    """Remember that this person asked to stop. Idempotent — a second STOP
    keeps the ORIGINAL timestamp, because when they first asked is the fact
    that matters if anyone ever has to answer for a message sent after it.

    Best-effort, like every other bookkeeping write on a live turn: the
    customer's reply has already been handled and must not fail on this. It
    logs loudly, though — an opt-out we failed to record is the one silence
    with a legal cost attached.

    ponytail: scans this business's customers in Python rather than adding a
    normalized-phone column to match on in SQL. Per-business customer counts
    are small today. Add `Customer.phone_e164` (indexed) if a business ever
    grows enough to feel it.
    """
    try:
        customer = _row(session, business_id, phone)
        if customer is None or customer.opted_out_at is not None:
            return
        customer.opted_out_at = datetime.utcnow()
        customer.updated_at = datetime.utcnow()
        session.add(customer)
        session.commit()
    except Exception as e:
        logger.error(
            "failed to record opt-out",
            exc_info=e,
            extra={"business_id": business_id},
        )
        try:
            session.rollback()
        except Exception:
            pass


def record_opt_in(session: Session, business_id: int, phone: str) -> None:
    """They asked to resume. Clears the record so proactive sends may start
    again — the mirror of record_opt_out, and the reason every opt-out reply
    tells them "Reply START to resume" is a promise we can keep."""
    try:
        customer = _row(session, business_id, phone)
        if customer is None or customer.opted_out_at is None:
            return
        customer.opted_out_at = None
        customer.updated_at = datetime.utcnow()
        session.add(customer)
        session.commit()
    except Exception as e:
        logger.error(
            "failed to record opt-in",
            exc_info=e,
            extra={"business_id": business_id},
        )
        try:
            session.rollback()
        except Exception:
            pass


def is_opted_out(session: Session, business_id: int, phone: str) -> bool:
    """Gate for every PROACTIVE send. Fails CLOSED on an unknown number? No —
    fails OPEN, deliberately: an unknown number has never opted out, and
    treating "we have no record" as "do not contact" would silence the
    missed-call text-back, which is the product.

    It fails SAFE on an error, though: if we cannot tell, we do not send. A
    skipped follow-up is recoverable on the next tick; a text to someone who
    said stop is not.
    """
    try:
        customer = _row(session, business_id, phone)
    except Exception as e:
        logger.error(
            "opt-out check failed; suppressing the send",
            exc_info=e,
            extra={"business_id": business_id},
        )
        return True
    return customer is not None and customer.opted_out_at is not None
