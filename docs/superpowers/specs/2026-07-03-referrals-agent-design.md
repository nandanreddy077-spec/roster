# Referrals (Lead-gen, part 1) — Design Spec

**Date:** 2026-07-03
**Status:** Design approved, ready for implementation plan

---

## Executive Summary

**Referrals** is the first half of the **Lead-gen** agent — role #8 on Roster's own
role sequence ("Angi/Google capture, referral nudges"). It is deliberately scoped to
just the referral-nudge half; **speed-to-lead** (instant response to a brand-new
Google/Angi/web-form lead) is out of scope here and gets its own design once a real
lead source exists to build against, per `ROSTER.md`'s own rule against guessing
integrations.

Unlike Chaser/Rebooker/Renewals, Referrals is not a face of the Recovery engine and
needs no manually-created campaign. Every job an owner marks done is automatically
eligible: after a fixed delay, if the client has set an incentive line, the customer
gets one text asking them to refer a friend. A reply gets run through Claude once to
pull out a referred name/phone (falling back to the raw text if extraction fails),
producing a **referral lead** the owner follows up on by hand — no coupon system, no
booking flow, no multi-touch sequence.

---

## Problem & Opportunity

- Every completed job today is a dead end the moment the Reviews text goes out — the
  business never asks the one question that turns a happy customer into a source of
  new customers.
- Word-of-mouth is already how most home-service businesses get customers; this
  makes it systematic instead of relying on someone remembering to mention the shop.
- Cheap to build: the trigger (`Job.completed_at`), the SMS channel, the reply-routing
  pattern, and the per-job error isolation all already exist from Recovery/Reviews.
  The only genuinely new piece is a single Claude-assisted extraction step.

---

## Design

### Flow

Job marked complete → wait the configured referral delay → if referrals are enabled
(the client has set an incentive) → send the configured referral request → if the
customer replies, extract any referral details into structured fields (falling back
to the raw reply if extraction isn't possible) → create a referral lead for the owner
to follow up on.

### Configurability

The delay and the message wording are **fixed constants**, shared across every
client — matching how Chaser/Rebooker/Renewals' own templates aren't owner-editable
in the UI yet either, even though the underlying data supports it. The **only**
owner-set value is the incentive line itself (e.g. "$25 off your next service"),
because that's the one thing that varies business to business and that nobody else
can decide on the business's behalf.

- `REFERRAL_DELAY_DAYS = 4` — long enough that the job doesn't feel like a stacked
  second ask right on top of the Reviews text, short enough the good experience is
  still fresh.
- `REFERRAL_MESSAGE_TEMPLATE` — one message, using `{customer_name}`, `{service_type}`,
  and `{incentive}`. No escalation sequence; a referral ask that nags over weeks reads
  oddly compared to a quote or renewal reminder, so this is genuinely one touch, not
  6/5 like the other three faces.
- `REFERRAL_REPLY_WINDOW_DAYS = 3` — how long after sending a referral ask an inbound
  reply is still treated as a response to it (see Routing, below).

### Trigger — fully automatic, no campaign

Chaser/Rebooker/Renewals require the owner to paste in a customer list to start a
campaign. Referrals does not: the customer's name and phone are already sitting on
the `Job` row the moment it's marked done. A daily check finds every job where:
- `completed_at` is at least `REFERRAL_DELAY_DAYS` ago,
- `referral_sent_at` is still `NULL` (never asked),
- the client's `referral_incentive` is set (referrals are "on" for this client),

sends the text, and sets `referral_sent_at`. No campaign-creation UI, no pasted list —
the owner's only action is setting the incentive line once per client.

### Reply handling — Claude-assisted extraction, no state machine

A referral reply has no judgment call to make (no intent to classify, no time slot to
negotiate), so there's no multi-state job status like Chaser/Rebooker/Renewals'
`pending → awaiting_slot → booked/declined/no_response`. Instead:

- One Claude call (`AgentEngine.respond()`, reusing the same engine every other agent
  uses) with a tool schema (`RECORD_REFERRAL_TOOL`) asking it to extract a referred
  name and/or phone number from the reply, if present.
- Whatever comes back — structured fields, partial fields, or nothing extractable —
  the **raw reply text is always stored**. A `ReferralLead` row is created every time
  a reply arrives to an active referral ask; extraction quality only affects whether
  `referred_name`/`referred_phone` are populated, never whether the lead gets logged
  at all.
- No terminal status to track beyond "a lead was captured" — this is a single-shot
  ask, not an ongoing thread.

### Routing

`/webhook/sms` gains one more check, inserted **after** the existing active-Recovery
check and **before** the Frontdesk fallback: if the sender has an active (sent, not
yet replied-to) referral ask, route there instead. An active Recovery thread has real
back-and-forth negotiation happening (slot booking) and takes priority; a referral
reply is a one-shot capture with nothing time-sensitive about routing it a beat later.

**"Active" is time-bounded, not open-ended.** Unlike Recovery, which tracks an
explicit job status (`pending`/`awaiting_slot`/etc.), a referral ask has no status
field — so without a bound, a customer texting about something unrelated *months*
later would still match "sent, no lead captured yet" and get misrouted forever. The
lookup (`find_active_referral_ask`) matches a `Job` where `referral_sent_at` is set
**within the last `REFERRAL_REPLY_WINDOW_DAYS` (3 days)**, `client_id`/phone match,
and no `ReferralLead` has been recorded against it yet. Past that window, an inbound
text from that number falls through to Frontdesk as normal — a late reply just
doesn't get parsed for a referral, which is an acceptable trade-off for a one-shot ask.

### Cron

Rides the **existing** daily cron (`recovery_tick.py`) rather than adding a second
job to set up — one operational entry point sends due Recovery sequence messages and
checks for due referral asks in the same run.

### Dashboard

A 6th tile in the agent-roster grid, "Lead-gen," same shape as the Reviews tile: an
inline form to set/update the incentive line, and a list of captured referral leads
(referred name/phone if extracted, raw reply text, which job triggered it) so the
owner can see and act on them without digging through raw message logs.

---

## Data Model Changes (extend existing tables + one new table)

```
Job.referral_sent_at          : Optional[datetime]   -- NULL until the ask is sent
Client.referral_incentive     : Optional[str]         -- NULL = referrals off for this client
```

**New table, `ReferralLead`** (the only new table — everything else reuses `Job`):
```
id                : PK
client_id         : FK -> Client
source_job_id     : FK -> Job            -- which completed job triggered this ask
asker_phone       : str                  -- the existing customer who was asked
referred_name     : Optional[str]        -- extracted by Claude, if present
referred_phone    : Optional[str]        -- extracted by Claude, if present
raw_reply_text    : str                  -- always stored, regardless of extraction
created_at        : datetime
```

---

## Constraints & Scope

### In Scope
- Fully automatic trigger off `Job.completed_at` — no campaign UI.
- One fixed-wording, fixed-delay message per client, gated on the incentive being set.
- Claude-assisted extraction of referred name/phone from a reply, with raw-text fallback.
- A dashboard tile to set the incentive and view captured leads.
- Reuse of the existing cron, SMS channel, and per-job error isolation pattern.

### Out of Scope (explicitly deferred)
- **Speed-to-lead** — the other half of Lead-gen. Needs its own design once a real
  Google LSA/Angi/web-form lead source exists to build the trigger against; building
  a webhook nobody's tested against would be guessing an integration, which
  `ROSTER.md` explicitly rules out.
- **Owner-editable delay/message wording** — fixed constants for now, matching the
  other three faces' current state (data could support overrides later; no UI for it
  yet, here or there).
- **A real coupon/redemption code system** — the incentive is a promise the owner
  honors by hand, same "you are the operation" concierge-phase spirit as everything
  shipped so far. Revisit if a real client asks for automated redemption tracking.
- **Multi-touch referral sequence** — one ask, not an escalating sequence; nagging for
  a referral over weeks doesn't fit the ask the way it fits a quote or renewal.

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| SMS send failure for one job stalls the whole daily batch | Same per-job try/except + rollback + continue pattern already proven in Recovery's `tick()`. |
| Claude fails to extract anything useful from a reply | Raw reply text is always stored regardless of extraction outcome — a lead is never silently dropped just because the format was messy. |
| A referral-ask reply gets misrouted to Frontdesk or an active Recovery thread | Routing check placed correctly in `/webhook/sms`'s priority chain: Recovery first (active negotiation in progress), referral second, Frontdesk fallback last. |
| Referral texts stack awkwardly on top of the Reviews text | Delay (`REFERRAL_DELAY_DAYS`) is deliberately days out, not immediate — the two asks land as separate, well-timed touches instead of a back-to-back blast the moment a job closes. |
| Same job re-triggers a referral ask on every daily run | `referral_sent_at` gates re-sending, same idempotency approach as Reviews' `completed_at` guard. |
| An unrelated text months later gets misrouted to the referral handler because "no lead captured yet" never expires | `find_active_referral_ask` bounds the match to `REFERRAL_REPLY_WINDOW_DAYS` (3 days) after `referral_sent_at`; past that, texts fall through to Frontdesk normally. |

---

## Testing

- Daily check: an eligible job (old enough, incentive set, never asked) gets texted
  exactly once; an ineligible job (too recent / no incentive / already sent) is
  skipped without error.
- Per-job failure isolation: one job's send failure doesn't block a different job's
  send in the same run (mirrors Recovery's existing regression test for this).
- Reply handling: a reply with a clean name+phone produces a `ReferralLead` with both
  fields populated; a reply Claude can't parse still produces a `ReferralLead` with
  `raw_reply_text` set and the structured fields `None`.
- Routing regression: an inbound text from a customer with an active Recovery job
  still routes to Recovery, not to the referral handler, even if that same customer
  also has a pending referral ask.
- Dashboard: setting/clearing the incentive line correctly toggles whether the tile
  reads "on"; captured leads render on the client detail page.

---

**Design approved by:** User
**Ready for:** Implementation planning
