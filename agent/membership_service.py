"""Membership Agent: turns one-time repair customers into plan members.

The employee that finally consumes JobQualification.membership_candidate —
Lead Qualifier has been computing that flag on every job since 2026-07-30 and
nothing read it except a dashboard counter (design doc §2.1).

Shape follows Reviews and Referral, NOT Quote Chaser: this is a two-touch
offer with a classified reply, not a six-touch sequence with slot booking, so
it needs no RecoveryCampaign/RecoveryJob machinery. What it borrows from
Recovery is the claim-before-send discipline, because "never double-text a
customer" is the one guarantee that can't be added later.

Deployment gate: job-shaped tick (the query starts from due jobs, not from
businesses), so it uses the in-loop `is_active` check rather than
runner.dispatch_tick — the second of the two shapes runner.dispatch_tick's
docstring documents. Registered in test_tick_deployment_gate.TICK_FUNCTIONS.

Design doc: docs/superpowers/specs/2026-08-10-gen-2-workforce-design.md §4.1.
"""

from datetime import datetime, timedelta
from typing import List, Optional

from channels import STOP_KEYWORDS, get_channel
from db_models import Business, Customer, Job, JobQualification, MembershipOffer
from engine import AgentEngine
from membership_engine import (
    MEMBERSHIP_FOLLOWUP_DELAY_DAYS,
    MEMBERSHIP_FOLLOWUP_TEMPLATE,
    MEMBERSHIP_OFFER_DELAY_DAYS,
    MEMBERSHIP_OFFER_TEMPLATE,
    MEMBERSHIP_REPLY_WINDOW_DAYS,
    OUTCOME_REPLIES,
    RECORD_MEMBERSHIP_REPLY_TOOL,
    build_membership_reply_prompt,
    render_membership_template,
)
from notifications import (
    KIND_ESCALATION,
    KIND_MEMBERSHIP_ACCEPTED,
    SOURCE_MEMBERSHIP_ACCEPTED,
    SOURCE_MEMBERSHIP_QUESTION,
    build_escalation_message,
    build_membership_message,
    notify_owner_of_escalation,
    notify_owner_of_membership,
    record_owner_notification,
)
from repositories import get_or_create_customer
from runner import is_active
from sqlalchemy import delete as sa_delete
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select
from trial_cap import can_respond, record_usage

agent = AgentEngine()
sms_channel = get_channel()

ROLE_KEY = "membership_agent"

# Two different questions, two different answers — this is the distinction
# real-Claude testing surfaced (2026-08-10), where "sounds interesting"
# classified as `unclear`:
#
#   Does this offer still get its scheduled nudge?  -> NUDGEABLE_OUTCOMES
#   Does an inbound text still route to this offer? -> outcome == "pending"
#
# A customer who said something ambiguous ("sounds interesting") has NOT
# decided, and is the single warmest lead the sequence produces — dropping the
# nudge there loses exactly the conversion this employee exists for. But they
# HAVE spent their one classification, so their next text falls through to
# Frontdesk rather than being re-classified (and re-billed) forever. This is
# the same split Reviews makes: `unclear` is the one outcome that leaves the
# door open for the scheduled nudge.
NUDGEABLE_OUTCOMES = ("pending", "unclear")

# What the model is allowed to return. A model that invents an intent outside
# this set gets treated as "unclear" rather than crashing or, worse, silently
# matching nothing and leaving the offer pending forever.
_VALID_INTENTS = ("accepted", "declined", "question", "unsubscribe", "unclear")


def _customer_for(
    session: Session, business_id: int, phone: Optional[str], customer_id: Optional[int]
) -> Optional[Customer]:
    """Look up a customer without creating one — a plan check must never have
    the side effect of creating the record it's checking.

    Deliberately a local copy of lead_qualifier_service._find_customer rather
    than an import: every employee module in this codebase is independent of
    the others' internals by convention (see review_engine.py's docstring), and
    reaching across for a private helper is how that convention dies.
    """
    if customer_id is not None:
        return session.get(Customer, customer_id)
    if not phone:
        return None
    return session.exec(
        select(Customer).where(Customer.business_id == business_id, Customer.phone == phone)
    ).first()


def send_due_membership_offers(session: Session) -> List[MembershipOffer]:
    """Offer the maintenance plan on every completed job Lead Qualifier
    flagged as a membership candidate, once MEMBERSHIP_OFFER_DELAY_DAYS have
    passed. Meant to be called from the scheduler — safe to call more often,
    since the unique index on source_job_id makes a second send impossible
    rather than merely unlikely.

    Four independent reasons a flagged job still gets no offer, in order:
    the business hasn't hired this employee; the owner hasn't told us what
    their plan is; the customer already has a plan; or this customer has
    already been offered one.
    """
    sent: List[MembershipOffer] = []
    cutoff = datetime.utcnow() - timedelta(days=MEMBERSHIP_OFFER_DELAY_DAYS)
    jobs = session.exec(
        select(Job)
        .join(JobQualification, JobQualification.source_job_id == Job.id)
        .where(
            Job.completed_at.is_not(None),
            Job.completed_at <= cutoff,
            JobQualification.membership_candidate == True,  # noqa: E712
        )
    ).all()

    for job in jobs:
        if not job.callback_number:
            continue
        client = session.get(Business, job.business_id)
        # membership_plan is configuration, not consent — the Employee row is
        # the deployment record (ARCHITECTURE.md invariant 8). Both are
        # required: without the plan text there is nothing honest to say, and
        # without the Employee row nobody asked us to say it.
        if client is None or not client.membership_plan:
            continue
        if not is_active(session, client, ROLE_KEY):
            continue

        # Re-check the plan at SEND time, not just at qualification time.
        # Lead Qualifier decided membership_candidate when the job was
        # created; a week has passed since, and the customer may have signed
        # up in the meantime — through this agent on an earlier job, or
        # because the owner sold them one on the phone. Pitching a plan to
        # someone who already pays for it is the single most trust-destroying
        # thing this employee could do.
        customer = _customer_for(session, client.id, job.callback_number, job.customer_id)
        if customer is not None and customer.plan_notes:
            continue

        # One offer per customer, ever — not one per job. A customer with
        # three completed repairs must not be pitched three times.
        #
        # This read is an OPTIMISATION, not the guarantee: it skips the common
        # case cheaply instead of taking an IntegrityError per extra job. The
        # actual enforcement is the unique index on
        # (business_id, customer_phone), because this check and the insert
        # below are not one atomic step — see the IntegrityError branch.
        #
        # ponytail: no re-ask window, so someone who declined 18 months ago is
        # never asked again. Add a `sent_at < now - N months and outcome ==
        # 'declined'` exception if that turns out to leave money on the table.
        # NOTE: that change needs the per-customer index relaxed too.
        prior = session.exec(
            select(MembershipOffer).where(
                MembershipOffer.business_id == client.id,
                MembershipOffer.customer_phone == job.callback_number,
            )
        ).first()
        if prior is not None:
            continue

        # CLAIM, then send. The insert IS the claim, on both axes at once:
        # source_job_id is unique (never twice for a job) and
        # (business_id, customer_phone) is unique (never twice to a person), so
        # two overlapping ticks race here and exactly one wins whichever way
        # they collide. Recording the send afterwards (what Reviews does)
        # cannot prevent a double-text, only notice one after it arrived.
        offer = MembershipOffer(
            business_id=client.id,
            source_job_id=job.id,
            customer_phone=job.callback_number,
        )
        session.add(offer)
        try:
            session.commit()
        except IntegrityError:
            # Either index rejected us: another tick claimed this job, or
            # already offered this customer via a different job. Both mean
            # "not ours to send" — skipping is correct for both.
            session.rollback()
            continue
        session.refresh(offer)

        try:
            text = render_membership_template(
                MEMBERSHIP_OFFER_TEMPLATE,
                customer_name=job.customer_name or "there",
                service_type=job.service_type,
                membership_plan=client.membership_plan,
            )
            sms_channel.send(
                from_number=client.inbound_number or "",
                to_number=job.callback_number,
                body=text,
            )
            offer.sent_at = datetime.utcnow()
            session.add(offer)
            session.commit()
            sent.append(offer)
        except Exception as e:
            # Release the claim so the next tick retries this job. A skipped
            # retry is recoverable; a double-text is not.
            print(f"Membership Agent: failed to send offer for job {job.id}: {e}")
            session.rollback()
            session.execute(sa_delete(MembershipOffer).where(MembershipOffer.id == offer.id))
            session.commit()
            continue

    return sent


def send_due_membership_followups(session: Session) -> List[MembershipOffer]:
    """The one polite nudge, MEMBERSHIP_FOLLOWUP_DELAY_DAYS after the offer.
    Never a second one — followup_sent_at is a structural cap of one touch,
    not a runtime judgment call, exactly as Reviews guarantees.

    Only NUDGEABLE_OUTCOMES qualify: a customer who actually decided has told
    us where they stand, and nudging someone who said no — or who asked a
    question a human is currently answering — is worse than saying nothing.
    Silence and ambiguity both still get the one nudge.
    """
    sent: List[MembershipOffer] = []
    cutoff = datetime.utcnow() - timedelta(days=MEMBERSHIP_FOLLOWUP_DELAY_DAYS)
    offers = session.exec(
        select(MembershipOffer).where(
            MembershipOffer.sent_at.is_not(None),
            MembershipOffer.sent_at <= cutoff,
            MembershipOffer.followup_sent_at.is_(None),
            MembershipOffer.outcome.in_(NUDGEABLE_OUTCOMES),
        )
    ).all()

    for offer in offers:
        client = session.get(Business, offer.business_id)
        if client is None or not client.membership_plan:
            continue
        if not is_active(session, client, ROLE_KEY):
            continue

        # Claim by conditional UPDATE rather than by insert — the row already
        # exists, so the unique index can't do the work this time. Same
        # guarantee, same reasoning as recovery_service.tick's day claim.
        claimed = session.execute(
            sa_update(MembershipOffer)
            .where(
                MembershipOffer.id == offer.id,
                MembershipOffer.followup_sent_at.is_(None),
            )
            .values(followup_sent_at=datetime.utcnow())
        )
        session.commit()
        if claimed.rowcount != 1:
            continue  # another tick got here first
        session.refresh(offer)

        try:
            job = session.get(Job, offer.source_job_id)
            text = render_membership_template(
                MEMBERSHIP_FOLLOWUP_TEMPLATE,
                customer_name=(job.customer_name if job else None) or "there",
            )
            sms_channel.send(
                from_number=client.inbound_number or "",
                to_number=offer.customer_phone,
                body=text,
            )
            sent.append(offer)
        except Exception as e:
            print(f"Membership Agent: failed to send follow-up for offer {offer.id}: {e}")
            session.rollback()
            session.execute(
                sa_update(MembershipOffer)
                .where(MembershipOffer.id == offer.id)
                .values(followup_sent_at=None)
            )
            session.commit()
            continue

    return sent


def find_active_membership_offer(
    session: Session, client_id: int, customer_phone: str
) -> Optional[MembershipOffer]:
    """An offer is 'active' — eligible to have an inbound reply routed to it —
    while it's still unanswered and was sent within the reply window.

    Time-bounded for the same reason find_active_referral_ask is: without a
    bound, an unrelated text months later would still match and be misrouted
    forever. Settled offers deliberately fall through to Frontdesk's general
    handling, so a customer who already answered can just talk normally.
    """
    cutoff = datetime.utcnow() - timedelta(days=MEMBERSHIP_REPLY_WINDOW_DAYS)
    return session.exec(
        select(MembershipOffer)
        .where(
            MembershipOffer.business_id == client_id,
            MembershipOffer.customer_phone == customer_phone,
            MembershipOffer.outcome == "pending",
            MembershipOffer.sent_at.is_not(None),
            MembershipOffer.sent_at >= cutoff,
        )
        .order_by(MembershipOffer.sent_at.desc())
    ).first()


def _settle(session: Session, offer: MembershipOffer, outcome: str, text: str) -> None:
    """Record the reply and close the offer. The raw text is stored whatever
    the classification outcome — extraction being messy is never a reason to
    lose what the customer actually said."""
    offer.raw_reply_text = text
    offer.replied_at = datetime.utcnow()
    offer.outcome = outcome
    session.add(offer)
    session.commit()


def handle_membership_reply(
    session: Session, client: Business, offer: MembershipOffer, text: str
) -> Optional[str]:
    """Process an inbound reply to a membership offer. Returns the text to send
    back (the caller sends it — TwiML for SMS).

    Exactly one classification per offer: every path here settles the outcome,
    so a customer who keeps texting isn't re-classified (and re-billed) on
    every message — their next text falls through to Frontdesk, which is the
    right general handler anyway.
    """
    if text.strip().lower() in STOP_KEYWORDS:
        # Always honored, and always free: never behind the trial cap.
        _settle(session, offer, "unsubscribed", text)
        return OUTCOME_REPLIES["unsubscribed"]

    if not can_respond(client):
        # Past the soft buffer: skip the paid classification but still persist
        # the reply. `unclear` is the honest state — we genuinely don't know
        # what they said — and it keeps them eligible for the nudge, so a
        # customer isn't silently dropped because of OUR billing limit.
        _settle(session, offer, "unclear", text)
        return None

    history = [{"role": "user", "content": [{"type": "text", "text": text}]}]
    result = agent.respond(
        client.to_config(),
        history,
        tools=[RECORD_MEMBERSHIP_REPLY_TOOL],
        system_prompt=build_membership_reply_prompt(client.membership_plan or ""),
        max_iters=2,
    )
    record_usage(session, client)

    pending = result["pending_tool_call"]
    intent = "unclear"
    if pending and pending["name"] == "record_membership_reply":
        intent = pending["input"].get("intent") or "unclear"
    if intent not in _VALID_INTENTS:
        intent = "unclear"

    outcome = "unsubscribed" if intent == "unsubscribe" else intent
    _settle(session, offer, outcome, text)

    job = session.get(Job, offer.source_job_id)
    customer_name = job.customer_name if job else None

    if outcome == "accepted":
        # THE HANDOFF: writing plan_notes is what hands this customer to
        # Retention Manager's renewal face, and simultaneously what stops Lead
        # Qualifier flagging them as a membership candidate again
        # (classify_membership_candidate returns False once plan_notes is set).
        # The loop closes here — sale, then service, then renewal, with no
        # human in it.
        customer = get_or_create_customer(session, client.id, offer.customer_phone, customer_name)
        if not customer.plan_notes:
            customer.plan_notes = (
                f"Said yes to the maintenance plan on "
                f"{datetime.utcnow().strftime('%Y-%m-%d')} (Membership Agent) — "
                f"owner to confirm billing."
            )
            customer.updated_at = datetime.utcnow()
            session.add(customer)
            session.commit()
        delivered = notify_owner_of_membership(client, customer_name, offer.customer_phone)
        record_owner_notification(
            session,
            client.id,
            KIND_MEMBERSHIP_ACCEPTED,
            SOURCE_MEMBERSHIP_ACCEPTED,
            build_membership_message(customer_name, offer.customer_phone),
            delivered,
        )
    elif outcome == "question":
        # Every question goes to a human — the agent has no way to know whether
        # the owner's plan blurb is current, complete, or applies to this
        # customer, so answering from it would be guessing about money.
        reason = "Asked a question about the maintenance plan."
        alerted = notify_owner_of_escalation(client, offer.customer_phone, reason)
        record_owner_notification(
            session,
            client.id,
            KIND_ESCALATION,
            SOURCE_MEMBERSHIP_QUESTION,
            build_escalation_message(client, offer.customer_phone, reason),
            alerted,
        )

    return OUTCOME_REPLIES.get(outcome, OUTCOME_REPLIES["unclear"])
