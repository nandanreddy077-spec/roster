# Roster Gen-2 Workforce — Design

_Written 2026-08-10. The design for the next generation of Roster AI employees,
grounded in the data the platform actually has today._

**Status:** design approved (founder, 2026-08-10). Only the Membership Agent is
built in the session that produced this document. Nothing else here is
implemented — an entry in this file is a commitment to a shape, not a claim
that it exists. Same rule as `agent/employees.py`'s registry header.

**Grounding:**
- Product filter — `ROADMAP.md`: build only if it improves Trust, Revenue, or
  Employee experience. Competitor parity is never a reason.
- Standing directive — optimize solely for "the owner believes they hired an
  employee, not software."
- Dashboard invariants — `ARCHITECTURE.md`, in particular #10 (no fabricated
  metrics) and #6/#7 (every metric drills into real rows).
- The employee registry and its `live`/`internal`/`planned` discipline —
  `agent/employees.py`.

---

## 1. The current workforce — unchanged

These five are shipped and are not modified by this design. Gen 2 adds
employees around them; it does not rewrite them.

| Employee | Department | Status | Owns |
|---|---|---|---|
| Frontdesk | Customer Service | live | Inbound communication → a booked job |
| Reviews | Customer Service | live | Post-job reputation → more reviews |
| Lead Qualifier | Sales | live | Every new job → an enriched job record |
| Quote Chaser | Sales | live | Unfinished estimates → recovered revenue |
| Dispatcher | Operations | live | Prioritization → a dispatch-ready job |
| Retention Manager | Customer Success | live | Renewals/rebooking → returning customers |

_(Six rows: `roles.py` ships Reviews and Retention Manager as one engine but
they are separate registry employees and separate customer outcomes.)_

---

## 2. Three findings that shape gen 2

Everything below follows from these. They are facts about the codebase, not
opinions about the market.

### 2.1 Lead Qualifier already finds money nobody picks up

`JobQualification.membership_candidate` and `financing_candidate` are computed
on every single job ([`lead_qualifier_engine.py:113`](../../../agent/lead_qualifier_engine.py)),
stored, counted on the dashboard — and **consumed by zero employees**. The only
readers are `metrics.py`, which displays a count.

The workforce identifies a customer who should be on a maintenance plan, and
then does nothing about it. That is the single largest gap in the current
roster, and it costs nothing in new data to close.

**Consequence:** the first two gen-2 employees are the ones that consume these
two flags. They are not new capabilities — they are the missing second half of
a capability that already ships.

### 2.2 Every current employee is reactive

Frontdesk waits for a call. Lead Qualifier waits for a job row. Dispatcher
waits for a qualification. Quote Chaser waits for a completed estimate. Reviews
waits for a completed job.

A real office also has seats that **originate** revenue from the existing
customer base, and one seat that is **accountable for the whole**. Roster has
neither. Gen 2 adds both.

### 2.3 Data is the ceiling, not intelligence

Roster owns the conversation layer (voice + SMS) and its own job records. It
has **no invoices, no payments, no technician records, no equipment/warranty
records, no price book, no ad spend, and no real calendar** —
`calendar_provider.py` is an explicit stub that invents plausible slots.

Of the 23 problem areas considered, seven cannot be staffed at all without a
system-of-record integration. Designing them now would produce demos. They are
Tier 2 below, each with the specific condition that unlocks it.

**This is the honest gate on the whole 2032 vision, and it is a sequencing
fact, not a limitation to be embarrassed about:** Roster earns the right to the
back office by owning the front office first, then pulling the integration
through a paying customer who asks for it (`ROADMAP.md` Phase D).

### 2.4 A fourth finding, discovered while designing: the outbound channel is contended

A completed job already triggers three independent outbound texts:

| Day | Employee | Constant |
|---|---|---|
| 1 | Reviews — review ask | `REVIEW_DELAY_DAYS = 1` |
| 4 | Referral — referral ask | `REFERRAL_DELAY_DAYS = 4` |
| ~5 | Reviews — one follow-up | `REVIEW_FOLLOWUP_DELAY_DAYS = 4` (after the ask) |

**No employee knows what another already sent.** Each tick queries jobs
independently. Adding a fourth post-completion employee without addressing this
turns a customer's week into a stream of texts from four different "people" at
the same business — which destroys exactly the illusion the product exists to
create.

Gen 2 handles this in two steps: a documented, deliberate delay ordering now
(cheap, sufficient for four employees), and the Chief of Staff owning it
structurally later (necessary beyond four).

---

## 3. The tier split

**Tier 1 — buildable on data Roster has today.** No integration, no new
external dependency. Some need one small config field or one schema column,
which is called out explicitly per employee.

**Tier 2 — requires a system of record.** Not designed in detail here, on
purpose: specifying inputs that don't exist is how a roadmap turns into
fiction. Each is listed with its unlock condition so the decision is
mechanical when a customer asks.

---

## 4. Tier 1 employees

### 4.1 Membership Agent ← built this session

**Name:** Membership Agent (registry key `membership_agent`, department Sales)

**Mission:** Turn one-time repair customers into plan members.

**Human employee replaced:** The comfort advisor / senior CSR whose job
description includes "offer the maintenance plan on every call" and who, in a
5–30 person shop, does it perhaps one time in twenty because the phone is
ringing.

**Trigger:** A completed job whose `JobQualification.membership_candidate` is
true, after `MEMBERSHIP_OFFER_DELAY_DAYS` have passed since completion. Tick-
based, once per scheduler run, gated on the `Employee` row.

**Inputs:**
- `Job` — `completed_at`, `service_type`, `customer_name`, `callback_number`
- `JobQualification.membership_candidate` — the signal Lead Qualifier already
  produces and nothing consumes
- `Customer.plan_notes` — absence is what makes someone eligible; presence
  means they're already a member
- `Business.membership_plan` — new config field: the plan's name and price in
  the owner's own words. Unset means this employee sends nothing.

**Outputs:**
- A `MembershipOffer` row per job (the durable record: `sent_at`,
  `followup_sent_at`, `outcome`, `raw_reply_text`)
- One outbound offer SMS, and at most one follow-up
- On acceptance: `Customer.plan_notes` set, and the owner notified through the
  same `OwnerNotification` machinery every other employee uses
- On a pricing question or complaint: an owner escalation, never an improvised
  answer

**Responsibilities:**
- Find completed jobs Lead Qualifier flagged as membership candidates
- Wait until after the Reviews and Referral asks have cleared, so the customer
  hears from one business, not four employees
- Send one plain-language offer in the owner's plan wording
- Send at most one polite follow-up. There is never a third touch — a
  structural cap, not a runtime judgment
- Classify the reply: interested / not interested / has a question / stop
- Enrol on a clear yes, and hand the customer to Retention Manager by writing
  `plan_notes`
- Escalate price negotiation, discount requests, and complaints to the owner
- Never offer to a customer who already has a plan
- Never send outside safe local hours
- Never send twice for the same job

**ROI:** Membership base is the single largest driver of a home-service
company's enterprise value — a shop with 800 plan members sells at a multiple a
shop with none does not get. It is also the cleanest recurring-revenue story
Roster can tell: _"your plan is $19/month, Roster enrolled 14 members last
month — that's $266/month you didn't have, and it compounds."_

Illustrative, **not a promise**: a shop completing 100 repairs/month, ~60%
membership-eligible, at a 10% offer-conversion rate, on a $19/month plan, adds
roughly $114 MRR in month one and compounds from there. The conversion rate is
a hypothesis until a real customer runs it — see Risks.

**MVP (this session's build):**
- `MembershipOffer` satellite table keyed on `source_job_id` (idempotency)
- Deterministic templated offer + one follow-up, using the owner's own plan
  wording — the message copy is never model-generated
- LLM used for exactly one thing: classifying the customer's free-text reply
- Enrollment writes `plan_notes` and notifies the owner
- Registry status `internal` — deployable by the founder per customer, not yet
  a standing dashboard offer, because no real customer has run it

**Future evolution:**
1. **Offer on the live call.** The highest-converting moment is while the tech
   is still in the driveway and the customer is happy. Frontdesk gains a
   membership tool; the SMS path becomes the fallback, not the primary.
2. **Tiered plans.** Business configures multiple tiers; the agent picks by job
   value and the customer's history.
3. **Closed loop with Retention Manager.** Enrollment already writes
   `plan_notes`; the renewal face (`RecoveryCampaign.face == "membership"`,
   which exists today) then owns them for life. Sale → service → renewal, with
   no human in the loop.

**Success metrics:**
- `membership_offers_sent` — offers actually delivered
- `memberships_enrolled` — accepted offers
- Enrollment rate = enrolled / sent
- Recurring revenue added (owner-visible, in dollars, never as an AI metric)
- **Guardrail metric:** unsubscribe/STOP rate on membership offers. If this
  rises, the employee is hurting the relationship and must be throttled.

---

### 4.2 Chief of Staff

**Name:** Chief of Staff (`chief_of_staff`, department Leadership)

**Mission:** The owner reads one message a day and knows exactly what needs
them.

**Human employee replaced:** The office manager — specifically the part of the
role that is "know everything that happened, decide what the owner personally
has to touch, and tell them." In shops too small for an office manager, this is
the owner's own 6am hour.

**Trigger:** A daily tick, once per deployed business, in the morning.

**Inputs:** Every other employee's output. This is the first employee whose
inputs are entirely other employees' outputs — `Job`, `JobQualification`,
`DispatchPlan`, `RecoveryJob`, `ReviewReply`, `MembershipOffer`,
`OwnerNotification`. All of it already exists and is already business-scoped.

**Outputs:**
- One daily owner briefing (SMS, with the dashboard as the drill-down)
- An exception list: escalations unactioned, jobs needing dispatch review,
  customers awaiting a human
- **The outbound contention decision** — which employee is allowed to text a
  given customer today (finding 2.4)

**Responsibilities:**
- Summarize yesterday in dollars and jobs, never in AI metrics
- Surface only exceptions that genuinely need the owner
- Enforce one outbound message per customer per window, across all employees
- Stay silent when there is nothing worth saying — a briefing that fires
  daily regardless trains the owner to ignore it

**ROI:** This is the **retention** employee. It is the daily proof the
workforce is working, and it is the thing that makes opening the dashboard
optional. It is also the only defensible answer to "why do I keep paying for
this?" on a quiet month. Secondarily, it prevents the customer-experience
damage that four uncoordinated employees would otherwise cause.

**MVP:** A daily digest assembled from the existing `BriefingWorkspace` view
model, delivered as SMS, plus one shared "has any employee texted this customer
in the last N days" check that every outbound employee calls before sending.

**Future evolution:** The owner replies to the briefing and it answers.
Then it proposes: _"Thursday is only half booked — want me to run a
reactivation batch on 40 dormant customers?"_ That is the point at which Roster
stops being a set of employees and becomes a manager the owner delegates to.

**Success metrics:** Briefing reply rate; exceptions actioned within a day;
contention collisions prevented; retention of businesses receiving the briefing
vs. not.

---

### 4.3 Reactivation Agent

**Name:** Reactivation Agent (`reactivation`, department Marketing)

**Mission:** Wake up the customers who stopped calling.

**Human employee replaced:** The marketing coordinator who is supposed to work
the dormant list every quarter, and the owner who knows the list is worth money
but has never had a spare afternoon.

**Trigger:** A tick over customers whose most recent completed job is older
than a dormancy threshold, who have no active recovery sequence.

**Inputs:** `Customer`, that customer's `Job` history, `Business.trade` and
services. All existing.

**Outputs:** Auto-enrollment into `RecoveryCampaign(face="reactivation")` — a
face, a six-touch sequence, and approved templates that **already exist** in
`recovery_engine.py`. Then booked jobs through the existing recovery reply
path.

**Responsibilities:** Identify genuinely dormant customers; exclude anyone in
another active sequence; enrol at a sustainable rate rather than blasting the
whole list; stop on a decline; hand a booking to the normal booking path.

**ROI:** The cheapest revenue in the business — no ad spend, no lead cost, and
the customer already knows the company. 500 dormant customers at a 4% booking
rate is 20 jobs; at a $400 average ticket, $8,000 from a list the owner already
owned.

**MVP:** Mirror `enroll_completed_estimates` exactly, against a dormancy
window instead of a completed estimate. This is the **smallest** Tier-1
employee by new code — the campaign machinery, templates, reply handling, and
booking are all built. It is mostly wiring.

**Future evolution:** Seasonal timing (AC tune-ups pitched in April, not
November); targeting by equipment age once equipment records exist; suppression
of customers the owner marks as bad fits.

**Success metrics:** Customers reached; customers returned; revenue from
reactivated jobs; **guardrail:** unsubscribe rate, which is the honest cost of
mining an old list.

---

### 4.4 Financing Agent

**Name:** Financing Agent (`financing`, department Finance)

**Mission:** Nobody walks away from a $9,000 replacement because they didn't
know they could pay monthly.

**Human employee replaced:** The comfort advisor who has to remember to present
financing, and who avoids it because it feels like asking for the sale twice.

**Trigger:** `JobQualification.financing_candidate` is true on a high-ticket
estimate — the second discarded flag from finding 2.1.

**Inputs:** `JobQualification.financing_candidate`, the `Job`, and
`Business.financing_link` — new config, a partner application URL. Unset means
this employee sends nothing.

**Outputs:** An SMS presenting monthly-payment framing with the application
link; a `FinancingOffer` record; owner notified when a customer engages.

**Responsibilities:** Present financing on qualifying estimates without being
asked; frame in monthly terms, never quote an APR or a term Roster cannot
verify; escalate every specific approval or eligibility question to a human;
coordinate with Quote Chaser so a financed lead is chased differently.

**ROI:** Presenting financing measurably lifts close rates on high-ticket
replacements and raises average ticket. A single additional $9,000 close per
month dwarfs the entire Roster subscription.

**Honest limit — and the reason this ranks below Membership:** Roster can send
the link. It **cannot see whether the customer applied or was approved**
without a partner API. So "applications completed" and "financed revenue" are
not measurable at MVP and must not appear as metrics —
`ARCHITECTURE.md` invariant 10 forbids inventing them. The MVP can only report
offers sent and replies received.

**MVP:** Templated offer on `financing_candidate` jobs, gated on
`financing_link` being set, one touch, replies escalated.

**Future evolution:** A real Wisetack/GreenSky integration turns approval into
a first-class signal — at which point Quote Chaser can lead with _"you're
approved for $9,000, want to book the install?"_, which is a categorically
stronger message than any follow-up available today.

**Success metrics:** Offers sent; replies indicating interest; close rate on
`financing_candidate` jobs with an offer vs. without. Explicitly **not**
approval or funded-volume metrics until a partner API exists.

---

### 4.5 Confirmation Agent — Tier 1, blocked on one field

**Name:** Confirmation Agent (`confirmation`, department Operations)

**Mission:** Every booked job gets confirmed, and every hole in tomorrow gets
found today.

**Human employee replaced:** The CSR's day-before confirmation call round —
in a 30-person shop, an hour or more of somebody's afternoon, every afternoon.

**Trigger:** A booked job whose scheduled date is approaching.

**Inputs:** `Job`, `DispatchPlan.scheduling_window`, `Customer`.

**Outputs:** A confirmation text; reschedule handled conversationally;
cancellation detected early; the owner alerted to a hole in tomorrow's
schedule while there is still time to fill it.

**THE BLOCKER — stated plainly:** `Job` has **no confirmed appointment
datetime**. It has `preferred_window`, which is deliberately documented as "a
stated preference only, never a confirmed appointment," and `DispatchPlan`
offers only a coarse window (`immediate`/`today`/`tomorrow`/`flexible`). You
cannot confirm an appointment that was never actually scheduled.

This is a **schema gap, not an integration gap** — one nullable
`scheduled_for` column plus the booking path setting it. That keeps it Tier 1,
but it is genuinely blocked until real scheduling exists, and real scheduling
is on the deferred list until a customer asks (`ROADMAP.md` Phase D). Ranked
last in Tier 1 for exactly this reason.

**ROI:** A no-show is a wasted truck-day — $500–$1,500 of capacity that cannot
be recovered. Catching two per month is $1,000–$3,000/month, and it is the
easiest ROI for an owner to feel, because they already know precisely how much
a wasted morning costs.

**MVP:** Confirm against the coarse dispatch window ("we have you down for
tomorrow morning — still good?"), which works without the new column and
proves demand for real scheduling.

**Future evolution:** Real slot booking; auto-refill of a cancelled slot from a
standby list of flexible customers — the point at which this employee starts
*generating* revenue rather than defending it.

**Success metrics:** Confirmation rate; no-show rate before vs. after;
cancellations caught more than 24h out; slots refilled.

---

## 5. Tier 2 — requires a system of record

Not designed in detail, deliberately. Each entry names the employee, the human
it replaces, and **the specific condition that unlocks it**. When a paying
customer names their tool, the decision becomes mechanical.

| Employee | Replaces | Blocked on | Unlock condition |
|---|---|---|---|
| **Collections** | AR clerk | Invoice + payment records | QuickBooks / ServiceTitan / Jobber invoice read access |
| **Route Optimizer** | Dispatcher (routing half) | Technician records, GPS, real schedule | A real dispatch board with assigned techs |
| **Parts & Vendor Coordinator** | Parts clerk | Inventory, PO, vendor catalog | Supplier integration + inventory records |
| **Warranty Clerk** | Warranty administrator | Equipment records, install dates, serials | Equipment records per customer |
| **Price-Book Analyst** | Ops manager / consultant | Price book, job costing, margins | Price book + labor cost data |
| **Marketing Attribution Analyst** | Marketing coordinator | Ad spend, call tracking numbers | Ad platform + call tracking integration |

**The strategic read:** every one of these is a real seat in a 30-person shop,
and every one is a legitimate future Roster employee. They are not being
rejected — they are being **sequenced**. Roster's entry into each one is the
same integration the ROADMAP already says to build only when a customer names
it. That is the same order ServiceTitan grew in, and the reason the ordering
matters is that an employee built on data you don't have is a demo, and a demo
does not survive contact with a real shop.

---

## 6. Deliberately cut

**Call Scoring / CSR Coach.** In a 5-truck shop there are no CSRs to coach — it
replaces nobody and saves no labor. Reframing it as QA on Roster's own AI makes
it *internal tooling*, which is worth building for Roster and is not an
employee a customer hires. Shipping it as an employee would be a gimmick.
`ROADMAP.md` already defers it to Phase E; this design agrees.

**Technician Coaching.** Same reasoning, plus it needs technician performance
data that does not exist.

**A "Support" employee separate from Frontdesk.** Frontdesk already owns
inbound communication. A second inbound employee splits one outcome across two
seats and creates a routing problem where none exists.

---

## 7. The complete workforce — a 30-person HVAC company

### 7.1 The office that exists today

A 30-person HVAC company is roughly 18–20 field (techs, installers, helpers)
and 10–12 office. The office seats:

| Seat | Count | Fully loaded |
|---|---|---|
| CSR / call taker | 3 | $150k |
| Dispatcher | 1.5 | $90k |
| Office manager | 1 | $70k |
| AR / billing clerk | 1 | $55k |
| Install coordinator | 1 | $60k |
| Sales support / quote follow-up | 1 | $55k |
| Warranty & parts clerk | 1 | $55k |
| Marketing coordinator | 0.5 | $30k |
| Service manager | 1 | $85k |
| **Total** | **~11** | **~$650k/yr** |

That $650k is the honest TAM per customer at this size — and it is why this is
a large company rather than a feature. Roster is not competing for a software
budget; it is competing for a payroll line.

### 7.2 The office when Roster runs it

```
                        ┌──────────────────┐
        Owner  ◄────────┤  CHIEF OF STAFF  │  one briefing a day
                        └────────┬─────────┘
                                 │ reads every employee's output,
                                 │ owns outbound contention
     ┌───────────────┬───────────┼───────────┬────────────────┐
     ▼               ▼           ▼           ▼                ▼
 CUSTOMER         SALES     OPERATIONS    FINANCE      CUSTOMER SUCCESS
 SERVICE                                                 / MARKETING
     │               │           │           │                │
 Frontdesk    Lead Qualifier  Dispatcher  Collections†   Retention Mgr
 Reviews      Quote Chaser    Confirmation Financing     Reactivation
              Membership      Route Opt.†
                              Parts†
                              Warranty†

              † Tier 2 — unlocked by a system-of-record integration
```

**The flow of one job through the whole workforce:**

1. **Frontdesk** answers at 9:40pm, qualifies, quotes, books → a job exists
2. **Lead Qualifier** enriches it — repair, normal priority, membership
   candidate
3. **Dispatcher** schedules it — normal, tomorrow
4. **Confirmation** confirms the day before; the slot holds
5. Job completed
6. **Reviews** asks on day 1 → a Google review
7. **Membership Agent** offers on day 7 → the customer enrolls → `plan_notes`
   is written
8. **Retention Manager** now owns that customer's renewal, forever
9. **Chief of Staff** tells the owner one thing the next morning: _"14 jobs
   booked, $9,400. Two need you: a price complaint and an emergency with no
   address."_

Nine employees touched one customer. The owner was interrupted once, for the
two things that actually needed a human.

### 7.3 What is left for humans

Two seats survive, and both for good reasons:

- **Install coordinator** — permits, equipment delivery, physical-world
  scheduling with suppliers and inspectors. Roster does not exist in the
  physical world.
- **Service manager** — coaching, hiring, and firing human technicians. Humans
  managing humans.

Roster absorbs roughly **9 of 11 office seats**, ~$540k of annual payroll.

### 7.4 Why this is a large company

The pricing mechanic is the whole argument: **each employee is a salary line,
not a feature flag.** Roster lands with Frontdesk at a small monthly number and
expands across the org chart with no new sales motion — the same motion
ServiceTitan runs with modules, except the unit an owner is buying is a person,
which is a unit every owner already knows how to reason about.

At ~15% of replaced labor, a 30-person shop is roughly $80–100k of annual
contract value, entered at a few hundred dollars a month. Ten thousand shops at
that ACV is a $1B revenue company, and there are far more than ten thousand
shops.

**The moat is the handoffs.** Any competitor can build an AI receptionist —
several have. What is hard to copy is that Membership Agent knows the customer
has no plan because Lead Qualifier said so, enrolls them, and hands them to
Retention Manager for life, with shared memory across all three. A point
solution has no second employee to hand to. Every employee added makes every
other employee more valuable, and that compounding is the defensible asset.

---

## 8. Build order

1. **Membership Agent** ← this session. Consumes an existing discarded signal;
   shortest path from data Roster computes to dollars an owner feels.
2. **Reactivation Agent** — smallest new code; the campaign machinery exists.
3. **Chief of Staff** — becomes necessary once four employees text the same
   customer.
4. **Financing Agent** — needs a partner link and honest metric limits.
5. **Confirmation Agent** — needs `Job.scheduled_for` first.
6. **Tier 2** — only when a paying customer names their system of record.

## 9. Open risks

1. **Membership conversion rate is unproven.** No real customer has run a
   membership offer through Roster. The ROI model in §4.1 is arithmetic, not
   evidence. First real deployment either validates it or kills the ranking.
2. **Outbound contention is only partially solved** by delay ordering. Four
   post-completion employees is the practical ceiling before the Chief of
   Staff's traffic-cop role becomes mandatory.
3. **`send_hours_ok` is a fixed UTC window**, not per-business timezones —
   safe but conservative, and wrong for Alaska/Hawaii. Documented in
   `channels.py`; upgrade is a `Business.timezone` field.
4. **Tier 2 is a bet on integrations Roster does not control.** If the major
   FSM platforms close their APIs to competitors, the back office stays
   unreachable and the workforce caps at ~5 office seats rather than 9.
