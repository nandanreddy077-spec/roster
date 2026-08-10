# Membership Agent — Verification Report

_2026-08-10. Branch `feat/membership-agent`. Commits `fe766c8` (design),
`cc7e130` (implementation)._

**Verdict: ready to deploy to a real customer, gated on one manual step
(setting `membership_plan` on the client console) and with the risks listed
below — none of which can produce a duplicate or unsolicited text.**

---

## What was built

The employee that consumes `JobQualification.membership_candidate` — a flag
Lead Qualifier has computed on every job since 2026-07-30 that no employee
read. Seven days after a completed repair, it offers the owner's maintenance
plan to a customer who doesn't have one, classifies the reply, and hands an
acceptance to Retention Manager by writing `Customer.plan_notes`.

| File | Role |
|---|---|
| `agent/membership_engine.py` | Pure: delays, templates, tool schema, prompt |
| `agent/membership_service.py` | Tick + reply handling |
| `agent/db_models.py` | `MembershipOffer` table, `Business.membership_plan` |
| `agent/db.py` | Column + unique-index migrations |
| `agent/recovery_tick.py` | Scheduler wiring, inside the `send_hours_ok` gate |
| `agent/app.py` | SMS reply routing + founder config route |
| `agent/metrics.py`, `workspace.py` | Two metrics with drill-down, customer labels |
| `agent/channels.py` | `STOP_KEYWORDS` moved here (was duplicated risk) |
| `agent/employees.py` | Registry `planned` → `internal` |

**Architecture note:** shaped after Reviews/Referral (two-touch offer +
classified reply), not Quote Chaser. There is no sequence and no slot booking,
so `RecoveryCampaign`/`RecoveryJob` would have been dead weight. What it
borrows from Recovery is claim-before-send.

---

## 1. Unit tests — 34, all passing

`agent/tests/test_membership_service.py`, organised by guarantee:

- **Offers correctly:** sends on a flagged completed job; quotes the owner's
  plan verbatim; records a row marked sent.
- **The four silences:** not a candidate · no `membership_plan` configured ·
  employee never hired · employee fired · before the delay elapses.
- **Never pitches a member:** re-checked at *send* time, not just at
  qualification time; one offer per customer even across several jobs.
- **Idempotency:** 5 tick runs → 1 text, 1 row.
- **Claim discipline:** a failed send releases the claim and the next tick
  retries; a second claim on the same job raises `IntegrityError`; a second
  claim on the same *customer* via a different job raises too; the index is
  scoped per business, so one phone number can be offered by two shops.
- **Follow-up:** exactly one, never a second; suppressed for every decided
  outcome (parametrised over all four); still fires for `unclear`; never fires
  for a claim that never actually sent.
- **Replies:** accepted → plan_notes + owner notified · declined → nobody
  paged · question → escalation, no price quoted back · STOP → unsubscribed
  without a model call · hallucinated intent → `unclear` · past the trial cap
  → reply kept, not classified.
- **Routing:** only while unanswered; not after 40 days; never across
  businesses.
- **Metrics:** offers and acceptances counted separately; an unsent claim is
  never reported as activity.
- **Handoff:** after acceptance, `classify_membership_candidate` returns False.

## 2. Integration tests — 6, all passing

`agent/tests/test_membership_lifecycle.py`, driving the **real** entry points:

- `recovery_tick.run()` actually drives this employee (if it didn't, every
  unit test above would be testing a function nobody calls).
- Nothing sends outside the quiet-hours window, **and no claim is left
  behind**, so a suppressed send stays retryable.
- 5 scheduler runs → exactly 1 offer + 1 nudge, 2 messages total.
- A reply to `/webhook/sms` reaches the membership classifier — Frontdesk's
  agent is replaced with one that raises, so a routing regression fails loudly
  instead of silently degrading to a generic chat reply.
- After answering, the customer talks to Frontdesk again (a declined offer
  must not stop them booking another job by text).
- STOP at the webhook unsubscribes and cancels the pending nudge.

Plus the **tick-deployment guard** (`test_tick_deployment_gate.py`) extended to
cover both new tick functions. Its `TICK_FUNCTIONS` list is checked against
`recovery_tick.run`'s actual source, so this was mandatory, not optional. The
fixture now writes the `JobQualification` **directly** rather than relying on
Lead Qualifier — Lead Qualifier is itself gated, so in the "hired nobody" case
it produces no qualification, and the guard would have called the membership
tick, asserted silence, and passed however ungated it was.

**Full suite: 974 passed, 0 failed** (was 934 before this work).

## 3. End-to-end local run

Real SQLite file, real migrations, real `recovery_tick.run()`, real Claude.
Only the SMS channels are stubbed (they print). 30 assertions, all passing:

```
[PASS] membershipoffer table created
[PASS] unique index on source_job_id exists
[PASS] business.membership_plan column added
[PASS] init_db is idempotent (ran twice, no error)
[PASS] no offer sent outside send hours
[PASS] no claim row left behind (send stays retryable)
[PASS] exactly one plan offer after 5 scheduler runs
[PASS] exactly one follow-up nudge
[PASS] no third outbound message ever
[PASS] the offer quotes the owner's plan verbatim
[PASS] Claude classified it as accepted
[PASS] HANDOFF: Customer.plan_notes written
[PASS] owner text says 'wants to sign up', not 'signed up'
[PASS] agent never quotes a price back at the customer
[PASS] Lead Qualifier no longer nominates this customer
[PASS] no further texts to a customer who already said yes
[PASS] a stale membership_candidate flag on a member is ignored
[PASS] offers_sent == 1 / accepted == 1 / drills into real rows
```

The actual message sent:

> Hi Dana Cruz, thanks again for having us out for the AC repair. One thing
> worth knowing about: Comfort Club — $19/month, two tune-ups a year plus
> priority scheduling and 15% off repairs. Want me to get you set up?

## 4. Real Claude execution — 10 live calls

One inside the end-to-end run, plus a 9-case classification matrix against the
live Anthropic API. **All 9 correct, and no reply ever quoted plan terms:**

| Customer says | Classified | Why it matters |
|---|---|---|
| "yes please sign me up" | accepted | the plain yes |
| "sure, that sounds good, let's do it" | accepted | conversational yes |
| "no thanks, not interested" | declined | the plain no |
| "let me think about it and get back to you" | declined | "maybe later" is not a sale |
| "sounds interesting" | unclear | **polite interest is not agreement** |
| "how much is it again?" | question | pricing → human, never answered |
| "does that cover my water heater too?" | question | coverage → human |
| "is it billed monthly or yearly?" | question | billing → human |
| "stop texting me" | unsubscribed | opt-out in words, not the keyword |

### The real-model run found a design bug

`"sounds interesting"` classified as `unclear`, which is *correct* — that
customer said neither yes nor no. But `unclear` was a **settled** outcome in
the original design, meaning the warmest lead the sequence produces would
never receive the follow-up nudge.

Fixed by splitting one question into two, which is what Reviews already does
and what I cited without actually copying:

- **Gets the nudge?** `pending` or `unclear` — silence and ambiguity both
  still deserve the one follow-up.
- **Routes further replies?** `pending` only — an ambiguous reply has spent
  its one classification, so the customer's next text goes to Frontdesk rather
  than being re-classified and re-billed forever.

Pinned by two new tests. This is the specific value the real-LLM requirement
delivered: no stub would have produced `unclear` for that phrasing.

## 5–7. Scheduler, idempotency, duplicate sends

- **Scheduler:** verified through `recovery_tick.run()` itself, both inside and
  outside the send-hours window. Runs last in the tick, after the review and
  referral asks.
- **Idempotency:** 5 consecutive real scheduler runs → 1 offer, 1 nudge, 1 row.
  Re-running `init_db()` twice is clean.
- **No duplicate outbound:** guaranteed by the database, not by control flow.
  `source_job_id` is unique, so the claim IS the insert — two overlapping ticks
  race and exactly one wins. Recording the send afterwards (what Reviews does)
  can only *notice* a double-text, never prevent one. A failed send deletes the
  claim so the work retries.

---

## Remaining risks

**1. ~~Per-customer dedup is not atomic across processes.~~ CLOSED 2026-08-10**
Was: the per-customer rule was a read-then-insert, so two concurrent tick
processes could each pass the check for two different jobs of the same
customer and send two offers. Now enforced by a second unique index on
`(business_id, customer_phone)`, so the claim is atomic on both axes — per
job and per person. The service still does the cheap read first (it avoids a
pointless `IntegrityError` for a customer with three completed jobs), but
correctness no longer depends on it.

Verified: the index is created on a fresh database *and* added by
`_migrate_add_indexes` to a database that already has the table without it
(the shape a redeploy of the previous commit would have), idempotently, with
existing rows preserved. Two tests pin it — one asserting the database rejects
a second claim for the same person on a different job, one asserting the index
is correctly scoped **per business**, since the same phone number can be a
customer of two different shops and each is entitled to its own offer.

**2. Conversion rate is entirely unproven.** *(the honest one)*
No real customer has run a membership offer. The ROI model in the design doc
is arithmetic, not evidence. This is why the registry says `internal` and not
`live`, and it's the first thing a pilot should measure.

**3. A `question` reply depends on the owner following up.** *(low)*
Questions are escalated and the sequence stops — correct, since only a human
can answer them. But if the owner never calls back, that customer is silently
dropped. There is no "did anyone action this escalation?" tracking anywhere in
Roster, so this isn't new; it's the Chief of Staff's job in the design doc.

**4. Inherited: `send_hours_ok` is a fixed UTC window.** *(low)*
Conservative but safe on the mainland US, wrong for Alaska/Hawaii. Documented
in `channels.py`. Arrives with the branch this work builds on.

**5. Message copy uses the full name** ("Hi Dana Cruz") because `customer_name`
is stored whole. Slightly stiff. Identical to Referral's existing behaviour, so
it was left consistent rather than diverging here — worth fixing across all
outbound employees at once.

## Before this reaches a real customer

1. Set `membership_plan` on the client console (`/clients/{id}`) — **the
   employee sends nothing until this exists**, by design.
2. Deploy `membership_agent` for that business (or staff the Sales department,
   which now includes it).
3. This branch is based on `fix/recovery-quiet-hours-gate`, which is **not yet
   merged or deployed**. It must land first — without it there is no
   `send_hours_ok`, and the offer could go out at 3am.
