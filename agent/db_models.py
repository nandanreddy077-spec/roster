import json
from datetime import datetime
from typing import List, Optional

from models import ClientConfig
from sqlalchemy import Index, UniqueConstraint, text
from sqlmodel import Field, SQLModel

# What produced a Job row. The owner's dashboard can only claim revenue it can
# attribute: "62 jobs booked" is a count, "$18,420 you would not have had"
# needs to name what would have lost each job. That claim is exactly these
# labels, so they are a first-class column rather than a guess made at read
# time from notes or thread shape.
ORIGIN_INBOUND = "inbound"  # the customer reached us first
ORIGIN_MISSED_CALL = "missed_call"  # rang out, we texted back, they booked
ORIGIN_QUOTE_RECOVERY = "quote_recovery"  # a cold estimate Quote Chaser revived
ORIGIN_REACTIVATION = "reactivation"  # a dormant customer Retention brought back
# NOT work. bookings.record_escalation writes a Job purely to give alert_owner
# the same idempotency book_job gives log_job; it represents a page to the
# owner, never a booking. Every read path that counts or recalls jobs filters
# this out — metrics (it inflated jobs_booked by one per emergency),
# memory.build_customer_context (a first-time caller read as a returning one),
# and lead_qualifier_service (which was qualifying and dispatching alerts).
ORIGIN_ESCALATION = "escalation"

# What a Job's appointment time actually is, honestly. Exists because a real
# violation shipped without it: recovery_service told a customer "you're
# booked for Wednesday 9am-12pm!" off a slot ManualCalendarProvider invented —
# never checked against a real technician, truck, or calendar (Milestone 2,
# booking-honesty audit, 2026-08-11).
#
# BOOKING_REQUESTED: a stated time preference only (log_job's
#   preferred_window) — nobody has offered or picked a specific slot.
# BOOKING_PROPOSED: a specific slot was offered (from whatever the calendar
#   provider currently is — today that's ManualCalendarProvider's invented
#   plausible-weekday slots, not a real calendar) and the customer picked
#   one. Still not verified against real availability.
# BOOKING_CONFIRMED: a human — the owner, today; a real calendar integration,
#   later — has actually checked availability and locked the time in. This is
#   the ONLY status confirmation language may ever be used for, and nothing
#   in this codebase sets it automatically: there is no calendar integration,
#   so nothing CAN honestly claim this without a human saying so. See
#   app.py's /clients/{id}/jobs/{id}/confirm — the one route that sets it.
#
# Never "cancelled"/"declined" here — that's RecoveryJob.current_status's
# concern (the outreach SEQUENCE's outcome), a different axis. This field is
# only ever about the appointment TIME's own certainty.
BOOKING_REQUESTED = "requested"
BOOKING_PROPOSED = "proposed"
BOOKING_CONFIRMED = "confirmed"
# BOOKING_CANCELLED: terminal. The appointment is not happening — the owner
#   rejected the request, or the customer called it off. A customer who comes
#   back gets a NEW Job rather than a resurrected one, so `completed_at` and
#   every employee that reads it (Reviews, Referral, Membership, Quote Chaser)
#   keep their existing meaning untouched.
# BOOKING_RESCHEDULE_REQUESTED: a time exists and somebody wants a different
#   one; nobody has agreed to the new one yet. Frontdesk already RECOGNIZES
#   this (engine.py's _RESCHEDULE_NOTE) and until now had nowhere to put it.
BOOKING_CANCELLED = "cancelled"
BOOKING_RESCHEDULE_REQUESTED = "reschedule_requested"

# What a business is paying, which decides whether the trial spend cap applies.
# This exists because the cap had no exit: every business carried a $20 hard
# stop and there was no route, form, or console action anywhere in the codebase
# to lift it. A paying customer would have gone silent mid-conversation after
# roughly 44 turns — the founder console even rendered "raise the cap or move
# this client to paid" next to a control that did not exist.
#
# Deliberately a state, not a bigger number: raising the default would only
# move the cliff further out and leave a paying customer to fall off it later.
BILLING_TRIAL = "trial"
BILLING_PAID = "paid"


class Business(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    business_name: str = ""
    trade: str = ""
    services_json: str = "[]"  # JSON-encoded list[str]
    hours: str = ""
    pricing_faq: str = ""
    # Free text, e.g. "within 20 miles of Austin, TX" — unset means Frontdesk
    # asserts no service-area restriction (Sprint 2, 2026-07-29 audit).
    service_area: str = ""
    escalation_phone: str = ""
    answer_mode: str = Field(
        default="backup"
    )  # "primary" (AI picks up every call) or "backup" (AI catches only calls the owner misses) - asked during onboarding; drives the call-forwarding instructions
    business_phone: str = ""  # the number customers currently dial; the owner forwards it to inbound_number so calls reach the receptionist
    inbound_number: Optional[str] = (
        None  # the business line customers text/call; routes inbound SMS
    )
    xai_phone_number: Optional[str] = (
        None  # number registered with xAI's Voice Agent API (see xai_voice_adapter.py); unset = no live-voice receptionist configured for this client yet
    )
    xai_signing_secret: Optional[str] = (
        None  # webhook signing secret returned when xai_phone_number was registered (per-number, not account-wide — see provisioning.py)
    )
    # --- Read-only calendar availability (Phase 1, 2026-08-18) ---------------
    # IANA zone ("America/New_York"). Required to place an availability window
    # on the clock at all; calendar_provider falls back to Eastern and logs when
    # unset. Also the upgrade path named by channels.py's send-window ponytail.
    timezone: str = ""
    # Google OAuth refresh token, calendar.readonly scope. Stored the same way
    # xai_signing_secret is — a plain column. Availability lookups exchange it
    # for a short-lived access token; it never grants write access.
    google_refresh_token: Optional[str] = None
    # Which calendar to read. "primary" for almost everyone; named here so a
    # business that keeps its jobs on a separate calendar can point at it.
    google_calendar_id: str = "primary"
    google_calendar_connected_at: Optional[datetime] = None
    # Why the failure needs to be a ROW, not a log line: voice provisioning is
    # multi-step and partially retryable, and the self-serve activation path has
    # no request to redirect an error onto. Printing to stderr is what let a
    # half-provisioned number sit undetected — the number looked bought and
    # SMS-ready while voice silently never worked. Set on every failed step,
    # cleared on success (provisioning.provision_voice).
    voice_provisioning_error: Optional[str] = None
    twilio_number_sid: Optional[str] = (
        None  # Twilio's SID for the purchased number, needed to later attach it to a SIP trunk
    )
    review_link: Optional[str] = (
        None  # owner's Google/Yelp review URL; unset until they provide one
    )
    referral_incentive: Optional[str] = (
        None  # e.g. "$25 off"; unset = referrals off for this client
    )
    # The owner's maintenance plan in THEIR OWN words, e.g. "Comfort Club —
    # $19/month, two tune-ups a year plus priority scheduling and 15% off
    # repairs." Unset = Membership Agent sends nothing for this business.
    # One free-text field rather than name/price/benefits columns on purpose:
    # the offer text quotes it verbatim, so the agent can never invent a
    # benefit or a price the owner didn't actually offer (same precedent as
    # pricing_faq and referral_incentive).
    membership_plan: Optional[str] = None
    requested_roster: Optional[str] = (
        None  # JSON list of extra roles queued for founder setup — self-serve "Hire" clicks on Quote Chaser/Retention Manager append here (see agent/roles.py), same mechanism the old /hire addons used
    )
    email: Optional[str] = Field(default=None, unique=True, index=True)
    password_hash: Optional[str] = None
    tone: str = "professional and friendly"
    # Where this business sits in Roster's provisioning pipeline (blueprint
    # §10a). Set explicitly by the founder — never auto-advanced, so a
    # double-submit can't silently skip a stage.
    pipeline_stage: str = Field(default="lead")
    source: Optional[str] = (
        None  # how the owner heard about Roster; asked from the dashboard, not at signup
    )
    source_prompt_dismissed: bool = False
    frontdesk_live: bool = False
    activated_at: Optional[datetime] = None
    tested_at: Optional[datetime] = (
        None  # set the first time an owner's dashboard test message gets a real reply back — this, not form submission, is what earns the honest "Working" status (see portal.py)
    )
    trial_spend_cents: int = 0
    trial_cap_cents: int = 2000  # $20 hard cap
    trial_soft_buffer_cents: int = 200  # $2 grace on top of the hard cap — see trial_cap.py
    trial_cap_notified: bool = False  # set once the founder has been alerted the hard cap was crossed, so the alert fires only once
    # BILLING_TRIAL (capped) or BILLING_PAID (uncapped). Defaults to trial so a
    # newly provisioned business is still protected from runaway spend; the
    # founder flips it from the console when the customer starts paying.
    billing_state: str = BILLING_TRIAL
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def services(self) -> List[str]:
        return json.loads(self.services_json)

    def to_config(self) -> ClientConfig:
        return ClientConfig(
            client_id=str(self.id),
            business_name=self.business_name,
            trade=self.trade,
            services=self.services,
            hours=self.hours,
            pricing_faq=self.pricing_faq,
            escalation_phone=self.escalation_phone,
            answer_mode=self.answer_mode,
            tone=self.tone,
            service_area=self.service_area,
        )


class Customer(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("business_id", "phone", name="uq_customer_business_phone"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    business_id: int = Field(foreign_key="business.id", index=True)
    phone: str = Field(index=True)
    name: Optional[str] = None
    source: Optional[str] = None
    # Free text — a recurring plan, membership, or warranty status the owner
    # sets, e.g. "Quarterly pest plan, renews in Sept." Surfaced in
    # build_customer_context() so a plan customer isn't treated as a
    # brand-new lead (Sprint 2, 2026-07-29 conversation-quality audit). No
    # founder-console field to set it yet — out of this sprint's scope.
    plan_notes: Optional[str] = None
    tags_json: str = "[]"
    first_seen_at: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def tags(self) -> List[str]:
        return json.loads(self.tags_json)


class Message(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    business_id: int = Field(foreign_key="business.id")
    customer_id: Optional[int] = Field(default=None, foreign_key="customer.id", index=True)
    customer_phone: str = "dashboard"  # threads a conversation per customer on a business's line
    role: str  # "user" or "assistant"
    content_json: str  # JSON-encoded content (str or list of content blocks)
    external_id: Optional[str] = Field(
        default=None, index=True
    )  # provider message id (e.g. Twilio MessageSid) — lets a retried webhook skip re-inserting the same inbound message
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WebhookDelivery(SQLModel, table=True):
    """Inbound-webhook dedup ledger. One row per unique provider delivery
    (Twilio MessageSid, xAI call_id). The unique dedup_key makes 'claim' an
    atomic insert — a concurrent or retried delivery loses the race and is
    either replayed (response_text cached) or dropped, never reprocessed
    into duplicate side effects."""

    id: Optional[int] = Field(default=None, primary_key=True)
    provider: str
    dedup_key: str = Field(unique=True, index=True)
    response_text: Optional[str] = None  # cached response body (TwiML) to replay on retry
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Job(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    business_id: int = Field(foreign_key="business.id")
    customer_id: Optional[int] = Field(default=None, foreign_key="customer.id", index=True)
    customer_phone: str = "dashboard"
    customer_name: Optional[str] = None
    service_type: str
    urgency: str
    address: Optional[str] = None
    callback_number: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None  # set by the "Mark done" action; drives the Reviews SMS
    referral_sent_at: Optional[datetime] = (
        None  # set once the referral ask has gone out for this job
    )
    # Set where the review SMS is actually sent (review_service.
    # send_due_review_requests), so "review requests sent" counts real sends
    # rather than inferring them from completion. Every review request is a
    # first-class record we can drill into.
    review_requested_at: Optional[datetime] = None
    # Set once the one polite follow-up has gone out (review_service.
    # send_due_review_followups) — gates re-sending the same way
    # review_requested_at does for the initial ask. There is never a third
    # touch: "never spam" is a structural cap of one follow-up, not a
    # runtime judgment call.
    review_followup_sent_at: Optional[datetime] = None
    # A stated preference only ("Thursday afternoon"), never a confirmed
    # appointment — Frontdesk has no scheduling/dispatch system to actually
    # book a slot against (Sprint 1, 2026-07-29 conversation-quality audit).
    preferred_window: Optional[str] = None
    # Set only once alert_owner's SMS has actually been delivered for this
    # escalation Job — lets a retry after a FAILED page still go through while
    # a repeat call after a SUCCESSFUL one is deduped (bookings.record_escalation).
    owner_alerted_at: Optional[datetime] = None
    # Structured "what kind of job was this" flag (Quote Chaser PR #1,
    # 2026-07-30) — set by log_job when the call was a price/estimate/
    # replacement conversation rather than an active repair. The timing
    # signal for auto-enrollment is completed_at (already above), not this
    # field: an estimate can only be chased once it's actually been given,
    # which "Mark done" represents; this field only says which completed
    # jobs qualify.
    is_estimate: bool = False
    # Which employee's work produced this row (see the ORIGIN_* constants).
    # Defaults to inbound so an un-attributed job is never counted as
    # recovered revenue — the conservative direction.
    origin: str = ORIGIN_INBOUND
    # What the job was actually worth, entered by the owner at "Mark done".
    # Cents, not a float, because it is money. Optional because a job has no
    # value until the owner says so, and an unpriced job must read as UNKNOWN
    # rather than $0 (ARCHITECTURE.md invariant 10) — a zero would quietly
    # drag down every total it lands in.
    value_cents: Optional[int] = None
    # See BOOKING_* above. Defaults to REQUESTED — the honest default for
    # every existing row and every new one, since nothing automated can
    # honestly claim more than that.
    booking_status: str = BOOKING_REQUESTED
    # Set only by the one human-confirm action (app.py's /confirm route).
    # Kept distinct from `updated_at`-style bookkeeping other tables use,
    # because "when was this actually confirmed" is itself a fact the owner
    # or a future support conversation may need — not just an audit trail.
    confirmed_at: Optional[datetime] = None
    # Same reasoning as confirmed_at, for the terminal state: "when did this
    # stop being a booking" is a fact, not bookkeeping.
    cancelled_at: Optional[datetime] = None
    # Set ONLY when the owner offers a window (booking_manager.propose), never
    # when an employee books a slot the customer picked. That distinction is
    # what lets an inbound text be routed to "the customer is answering the
    # owner's offer" — both cases sit in BOOKING_PROPOSED, so the status alone
    # cannot tell them apart.
    owner_proposed_at: Optional[datetime] = None
    # The owner's own words when they reject or move a booking ("crew is on a
    # commercial job all Thursday"). Shown to the owner, never sent verbatim to
    # the customer — it is an internal note, and forwarding it unread would
    # publish whatever the owner typed.
    booking_notes: Optional[str] = None


class RecoveryCampaign(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    business_id: int = Field(foreign_key="business.id")
    face: str  # "quote" or "reactivation"
    name: str
    customer_list_json: str  # JSON list of {phone, name, service_type, estimate_amount, days_since}
    template_overrides_json: str = "{}"  # JSON map of message_day (str) -> custom text
    started_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def customer_list(self) -> List[dict]:
        return json.loads(self.customer_list_json)

    @property
    def template_overrides(self) -> dict:
        return json.loads(self.template_overrides_json)


class RecoveryJob(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="recoverycampaign.id")
    business_id: int = Field(foreign_key="business.id")
    customer_phone: str
    customer_name: Optional[str] = None
    service_type: str
    estimate_amount: Optional[str] = None
    days_since: Optional[str] = None
    anchor_date: Optional[str] = None  # ISO YYYY-MM-DD; only set for the "membership" face
    current_status: str = (
        "pending"  # pending, awaiting_slot, booked, declined, no_response, escalated
    )
    # Set alongside current_status == "escalated" (recovery_service._escalate,
    # PR #2) — the model's own short reason, drill-down-able next to the
    # status without cross-referencing the OwnerNotification it also created.
    escalation_reason: Optional[str] = None
    last_sent_day: Optional[int] = None
    offered_slots_json: str = "[]"
    booked_job_id: Optional[int] = Field(default=None, foreign_key="job.id")
    # Set only for auto-enrolled leads (recovery_service.
    # enroll_completed_estimates) — the completed Job an estimate follow-up
    # was detected from. None for founder-pasted CSV campaigns, which have
    # no source Job. Doubles as the idempotency key: a Job already linked to
    # a RecoveryJob here is never enrolled a second time.
    source_job_id: Optional[int] = Field(default=None, foreign_key="job.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def offered_slots(self) -> List[str]:
        return json.loads(self.offered_slots_json)


class RecoveryMessageLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    recovery_job_id: int = Field(foreign_key="recoveryjob.id")
    message_day: int
    message_text: str
    sent_at: datetime = Field(default_factory=datetime.utcnow)
    customer_reply: Optional[str] = None
    replied_at: Optional[datetime] = None


class ReferralLead(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    business_id: int = Field(foreign_key="business.id")
    source_job_id: int = Field(foreign_key="job.id")  # which completed job triggered this ask
    asker_phone: str  # the existing customer who was asked
    referred_name: Optional[str] = None  # extracted by Claude, if present
    referred_phone: Optional[str] = None  # extracted by Claude, if present
    raw_reply_text: str  # always stored, regardless of extraction outcome
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ReviewReply(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    business_id: int = Field(foreign_key="business.id")
    source_job_id: int = Field(foreign_key="job.id")  # which review ask this replies to
    customer_phone: str
    outcome: str  # "left_review" | "positive" | "neutral" | "negative" | "declined" | "unclear"
    raw_reply_text: str  # always stored, regardless of classification outcome
    created_at: datetime = Field(default_factory=datetime.utcnow)


class JobQualification(SQLModel, table=True):
    """Lead Qualifier's output — a satellite enrichment record, never a
    second Job model. One row per Job, written once (idempotent, gated by
    source_job_id), never updated. Deterministic: no AgentEngine involved in
    producing any field here (2026-07-30 design review)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    business_id: int = Field(foreign_key="business.id")
    source_job_id: int = Field(foreign_key="job.id")
    job_type: str  # "repair" | "replacement" | "maintenance" | "estimate"
    financing_candidate: bool
    membership_candidate: bool
    priority: str  # "high" | "normal"
    possible_spam: bool  # heuristic flag, not an objective claim — see field name
    # Comma-joined structured rule-code enums (lead_qualifier_rules.py), one
    # per axis — never English prose, so analytics can group/count by rule.
    reasoning: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class DispatchPlan(SQLModel, table=True):
    """Dispatcher's output — a satellite enrichment record, never a second
    Job model. One row per Job, written once qualification exists (idempotent,
    gated by source_job_id), never updated. Deterministic — every field is a
    lookup over Job.urgency and JobQualification's already-structured fields,
    no AgentEngine involved (2026-07-30 design review)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    business_id: int = Field(foreign_key="business.id")
    source_job_id: int = Field(foreign_key="job.id")
    dispatch_priority: str  # "emergency" | "same_day" | "normal"
    scheduling_window: str  # "immediate" | "today" | "tomorrow" | "flexible"
    requires_dispatch_review: bool
    # Comma-joined structured rule-code enums (dispatcher_rules.py), one per
    # axis — never English prose, so analytics can group/count by rule.
    dispatch_reason: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class MembershipOffer(SQLModel, table=True):
    """Membership Agent's record — one row per membership offer, and the
    employee's whole state machine. A satellite record keyed on source_job_id,
    the same shape as JobQualification and DispatchPlan, deliberately NOT two
    more timestamp columns on Job: Job already carries four employee-specific
    timestamps (review_requested_at, review_followup_sent_at, referral_sent_at,
    owner_alerted_at) and that pattern doesn't survive a sixth employee.

    THE CLAIM IS THE INSERT, and it is claimed on TWO axes:

      source_job_id unique          -> never offer twice for the same job
      (business_id, customer_phone) -> never offer twice to the same PERSON

    Creating this row is therefore an atomic claim on both — the same
    technique WebhookDelivery uses for inbound dedup. Two overlapping ticks
    both find no row, both try to insert, and exactly one wins; the loser
    catches IntegrityError and skips. That's why the row is written BEFORE the
    SMS goes out and deleted again if the send fails, rather than recorded
    after a successful send the way Reviews does it — recording after the send
    cannot prevent a double-text, it can only notice one.

    The per-person index is the one that needed a database to enforce it. The
    per-job rule is naturally serial (one job, one row), but "one offer per
    customer, not per job" was a read-then-insert: two tick processes could
    each check a DIFFERENT completed job for the SAME customer, both see no
    prior offer, and both text them. The service still does the cheap read
    first — it avoids a pointless IntegrityError for a customer with three
    completed jobs — but correctness now rests here, not on that read.

    NOT called an "enrollment": Roster records that the customer said yes and
    tells the owner. It cannot charge a card, so the owner still finalizes
    billing. `accepted` is the honest word for what this table knows, and the
    metric is named to match (ARCHITECTURE.md invariant 10).
    """

    __table_args__ = (
        Index("uq_membership_offer_source_job", "source_job_id", unique=True),
        Index(
            "uq_membership_offer_business_customer", "business_id", "customer_phone", unique=True
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    business_id: int = Field(foreign_key="business.id", index=True)
    source_job_id: int = Field(foreign_key="job.id")
    customer_phone: str
    # None between the claim and a successful send. A row with sent_at unset
    # is a claim whose send failed and hasn't been cleaned up — it is never
    # eligible for a follow-up, and never routes an inbound reply.
    sent_at: Optional[datetime] = None
    followup_sent_at: Optional[datetime] = None
    # pending    -> out and unanswered. Routes inbound replies, gets the nudge.
    # unclear    -> they replied, but said neither yes nor no. Their one
    #               classification is spent so further texts go to Frontdesk,
    #               but they STILL get the nudge — an ambiguous reply is the
    #               warmest lead the sequence produces, not a dead one.
    # accepted | declined | question | unsubscribed -> decided. No nudge, no
    #               routing; the customer's next text is normal conversation.
    outcome: str = "pending"
    raw_reply_text: Optional[str] = None  # always stored, whatever the classification
    replied_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Employee(SQLModel, table=True):
    __table_args__ = (
        # Index, NOT UniqueConstraint: a table-level UNIQUE becomes part of
        # CREATE TABLE under an auto-generated name, so db._migrate_add_indexes'
        # `CREATE UNIQUE INDEX IF NOT EXISTS uq_employee_business_role` would
        # then build a SECOND, separately-named object enforcing the same rule.
        # A named unique Index means fresh databases (create_all) and existing
        # ones (the DDL migration) converge on exactly one index, one name.
        Index("uq_employee_business_role", "business_id", "role_key", unique=True),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    business_id: int = Field(foreign_key="business.id", index=True)
    role_key: str
    display_name: str = ""
    status: str = "active"  # active | paused | fired
    policy_json: str = "{}"
    hired_at: datetime = Field(default_factory=datetime.utcnow)
    fired_at: Optional[datetime] = None


class AccessRequest(SQLModel, table=True):
    """A home-service owner who filled the landing's "Request early access"
    form. The only inbound conversion path while Twilio KYC is pending — the
    founder follows up from the /clients dashboard and hand-onboards the first
    few shops."""

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = ""
    business_name: str = ""
    phone: str = ""
    trade: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


class DepartmentInterest(SQLModel, table=True):
    """An existing customer asking Roster for one more department.

    STRICTLY a request record: recording interest deploys nothing, creates no
    Employee, and has no effect on runner.is_active(). That separation is the
    point — Business.requested_roster currently conflates "asked for" with
    "deployed" (runner.py:41), and the department model requires them to be
    different, separately-observable facts. Roster provisions after a
    discovery call, never on a click. Actioning a request likewise means
    "operations handled it", NOT "the department was deployed".

    Distinct from AccessRequest, which is the NEW-LEAD contact form (it has no
    business_id — the business doesn't exist yet).
    """

    __table_args__ = (
        # One OPEN request per business per department, enforced by the
        # database because a double-submit is genuinely concurrent: FastAPI
        # runs sync handlers in a threadpool even at --workers 1, and Postgres
        # deployments run many workers. Declared for both dialects since
        # db.resolve_engine_config supports both.
        # PARTIAL on purpose: an actioned row leaves the index, so a customer
        # whose request was declined months ago can ask again.
        Index(
            "uq_department_interest_open",
            "business_id",
            "department_key",
            unique=True,
            sqlite_where=text("actioned_at IS NULL"),
            postgresql_where=text("actioned_at IS NULL"),
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    business_id: int = Field(foreign_key="business.id", index=True)
    department_key: str  # departments.REGISTRY key; validated on write by expansion.record_interest
    created_at: datetime = Field(default_factory=datetime.utcnow)
    actioned_at: Optional[datetime] = None  # set when ops has handled it (Phase 4)


class OwnerNotification(SQLModel, table=True):
    """A durable record of an owner-facing alert. Every alert is also sent as
    an SMS (see notifications.py) — this table is what the dashboard's
    Notifications page reads, and the only place a FAILED send is visible at
    all (`delivered=False`); today a failed owner text is swallowed by a bare
    `except` and is invisible everywhere.

    Deliberately separate from the `event` table: nothing in the live
    SMS/voice path publishes through eventbus.py today, and wiring the bus
    into that path is a far larger change than a notifications list needs.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    business_id: int = Field(foreign_key="business.id", index=True)
    kind: str  # notifications.KIND_* — WHAT happened: job_booked | escalation | call_dropped
    # notifications.SOURCE_* — WHERE it came from: sms_booking | voice_booking |
    # alert_owner | call_dropped. Operational, not customer-facing: lets us tell
    # an SMS booking from a voice one (both job_booked) without parsing `message`.
    source: str = ""
    message: str  # the exact text the owner was sent, so the log never drifts from the SMS
    delivered: bool = True  # False when the SMS send failed or no escalation phone is set
    created_at: datetime = Field(default_factory=datetime.utcnow)
    read_at: Optional[datetime] = None  # no producer until the Notifications page ships


class Event(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    business_id: int = Field(foreign_key="business.id", index=True)
    type: str = Field(index=True)
    payload_json: str = "{}"
    customer_id: Optional[int] = Field(default=None, foreign_key="customer.id")
    employee_id: Optional[int] = Field(default=None, foreign_key="employee.id")
    dedup_key: Optional[str] = Field(default=None, unique=True, index=True)
    occurred_at: datetime = Field(default_factory=datetime.utcnow)


class SchedulerHeartbeat(SQLModel, table=True):
    """One row, always id=1, overwritten every tick. Exists so /health can
    answer "is the scheduler actually running" from data instead of a guess —
    before this there was no record anywhere of whether recovery_tick.run()
    had ever completed, only what it printed to stdout, which nobody was
    watching. recovery_tick.py updates this in a finally block, so a tick
    that raises still leaves a readable, honest record of its own failure
    rather than silently going stale."""

    id: Optional[int] = Field(default=1, primary_key=True)
    last_tick_at: datetime = Field(default_factory=datetime.utcnow)
    ok: bool = True
    error: Optional[str] = None
