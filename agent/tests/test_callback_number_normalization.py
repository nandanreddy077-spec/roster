"""callback_number is written canonical, so every reader can match it.

Found 2026-09-01. The column was stored exactly as the model produced it —
for a voice booking that is a transcription of a spoken number, so
"(512) 555-0149" rather than "+15125550149". It is simultaneously:

  * the join key for FIVE lookups that compare it with `==` against a Twilio
    `From`, which is always E.164; and
  * the OUTBOUND destination for every customer-facing booking text.

So a customer answering the owner's proposed window fell through the whole
inbound cascade into Frontdesk as a stranger, and the confirmation text went
to a number Twilio resolves against the sender's country — the exact failure
channels.normalize_phone was written for, where a send succeeds and reaches
the wrong person.

This is the same bug class as the escalation_phone comparison app._is_owner
already fixed; these tests keep it fixed at the write path, which is the one
door every callback_number comes through.
"""

from datetime import datetime

import pytest
from booking_manager import _customer_number, find_active_owner_proposal
from bookings import book_job
from db_models import BOOKING_PROPOSED, Business, Job

E164 = "+15125550149"


@pytest.fixture
def business(session):
    biz = Business(business_name="Kestrel", trade="plumbing", inbound_number="+15125557777")
    session.add(biz)
    session.commit()
    session.refresh(biz)
    return biz


@pytest.mark.parametrize(
    "spoken",
    ["(512) 555-0149", "512-555-0149", "512.555.0149", " 5125550149 ", "+1 512 555 0149"],
)
def test_every_shape_a_caller_might_give_is_stored_canonically(session, business, spoken):
    job, _ = book_job(
        session,
        business,
        "xai-voice:call-1",
        "xai-voice:call-1",
        {"service_type": "Drain cleaning", "callback_number": spoken},
    )

    assert job.callback_number == E164


def test_a_merge_normalizes_a_corrected_number_too(session, business):
    """The customer corrects their number mid-conversation and the model
    re-calls log_job. The merge path is a second door into the same column."""
    book_job(
        session,
        business,
        "xai-voice:call-2",
        "xai-voice:call-2",
        {"service_type": "Drain cleaning", "callback_number": "+15125550000"},
    )

    merged, created = book_job(
        session,
        business,
        "xai-voice:call-2",
        "xai-voice:call-2",
        {"service_type": "Drain cleaning", "callback_number": "(512) 555-0149"},
    )

    assert created is False
    assert merged.callback_number == E164


def test_the_customers_reply_to_a_proposed_window_is_matched(session, business):
    """The consequence, end to end: before the fix this returned None and the
    customer's answer was handed to Frontdesk as a brand-new conversation."""
    job, _ = book_job(
        session,
        business,
        "xai-voice:call-3",
        "xai-voice:call-3",
        {"service_type": "Drain cleaning", "callback_number": "(512) 555-0149"},
    )
    job.booking_status = BOOKING_PROPOSED
    job.owner_proposed_at = datetime.utcnow()
    session.add(job)
    session.commit()

    assert find_active_owner_proposal(session, business.id, E164) is not None


def test_an_outbound_text_never_leaves_without_a_country_code(session, business):
    """Twilio resolves a bare number against the SENDER's country and reports
    success, so this failure delivers to a stranger rather than erroring.
    Normalized at the send boundary as well, which also covers rows written
    before the write-path fix."""
    job = Job(
        business_id=business.id,
        customer_phone="xai-voice:legacy",
        service_type="Drain cleaning",
        urgency="routine",
        callback_number="(512) 555-0149",  # a row that predates the fix
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    assert _customer_number(job) == E164


def test_an_unrecognisable_number_is_left_alone_rather_than_invented(session, business):
    """normalize_phone returns an odd shape untouched so Twilio rejects it
    loudly. Silently inventing a destination is the worse failure."""
    job, _ = book_job(
        session,
        business,
        "xai-voice:call-4",
        "xai-voice:call-4",
        {"service_type": "Drain cleaning", "callback_number": "ask for Dave"},
    )

    assert job.callback_number == "ask for Dave"
