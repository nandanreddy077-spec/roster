# The Booking Experience — Product Architecture

_Status: **PROPOSAL — product reasoning, no code.**_
_Author: Principal Engineer / Product session, 2026-08-11._
_Companion to `2026-08-11-booking-management-system-design.md` (the technical
design). This document supersedes that one's assumption that the workflow is
chosen by which software a business runs._

> You asked me to challenge the framing. I'm challenging the central one: **the
> business's software is the wrong axis to design around.** §2 explains why, §3
> gives the axis I think is right, §4 answers your five categories on that
> basis. If §2 is wrong, everything after it is wrong too — start there.

---

## 1. What a customer actually wants (first principles)

A homeowner whose AC died at 2pm is not calling to obtain an appointment. They
are calling to stop being in trouble. They will phone three companies and go
with whoever makes the trouble stop first.

So the question they need answered is **not** *"am I confirmed for Thursday?"*
It is:

> **"Is this handled, or do I need to keep calling other companies?"**

This distinction is the whole design. Roster can answer that question
**honestly and immediately for every business type**, including one running on
a paper diary — because the answer doesn't require knowing whether a technician
is free at 2pm. It requires knowing: *you're in our area, we do this work,
you're in the queue, and here is exactly what happens next and when.*

**Certainty about the process is worth more than certainty about the time**,
and it is available to us today. Most booking-experience design gets this
backwards, chases the timestamp, and either lies or stalls.

The four office-manager principles you listed are right. But two of them
conflict in manual mode — *don't lie* and *don't leave customers waiting* — and
the resolution isn't to pick one. A great office manager says: **"You're in the
book for Thursday morning. I'll call you within the hour to lock the exact
window."** Nothing is confirmed, nothing is a lie, and nobody is waiting
without an end in sight.

---

## 2. Challenge: software is the wrong axis

Your brief asks for a workflow per FSM. I think that produces five variations
of the wrong thing, for three reasons.

### 2.1 An empty slot is not a promise you can keep

ServiceTitan, Housecall Pro and Jobber will all tell you a slot is open. **None
of them tell you the booking is a good idea.** Home-services dispatch is a
routing-and-skills problem wearing a calendar's clothes:

- The tech free at 2pm is 45 minutes away, finishing at 1:50.
- The tech free at 2pm does service, and this is an install.
- The truck doesn't have the part.
- The slot was being held open for a $12k replacement, and you just filled it
  with a $200 tune-up.

A real office manager knows Dave is on the north side today and doesn't book a
south-side call at 2. **The calendar API does not know that.** So "the FSM says
it's free" is *availability*, not *feasibility*, and confirming off it is a
more sophisticated version of the same lie the booking-honesty audit removed —
just with an API call in front of it.

### 2.2 Your central assumption inverts in both directions

> *"If ServiceTitan can verify availability, the owner should not have to
> manually approve."*

Capability is not permission. A shop that connected ServiceTitan three days ago
has not agreed to let an AI commit its dispatch board. Conversely, a shop on a
whiteboard that has trusted Roster for eight months may happily let it own
"Tuesday morning."

So the answer inverts **both** ways:

- **ServiceTitan + no authorization → Roster should NOT auto-confirm.**
- **Paper diary + clear authorization → Roster CAN honestly confirm.**

Which means software cannot be what selects the workflow.

### 2.3 The premise "manual mode cannot verify availability" is false

This is the most consequential challenge in the document.

Manual mode can't check a *calendar*. It can absolutely check **a capacity
model the owner declared once**: *"We run 6 service calls a day, arrival
windows 8–12 and 1–5, Monday to Friday."*

That is a two-minute onboarding question, not an integration. And against it,
Roster can say **"You're booked for Tuesday morning, 8am–12pm"** and be telling
the truth — because the owner pre-authorized exactly that, and Roster counted.

**Verified gap:** `Business` today has free-text `hours` and `service_area`
(`db_models.py:70,74`) and **no structured capacity model whatsoever** — no
jobs-per-day, no arrival windows, no crew count. Three new fields would unlock
honest confirmation for the businesses that have no software at all, which is
most of the ICP.

Home services doesn't sell 2:00pm appointments anyway. It sells **arrival
windows**. Windows are exactly what a declared capacity model can allocate
honestly.

---

## 3. The axis I'd design around

Two things decide the workflow, and neither is the FSM's name:

**Axis 1 — Certainty.** What can Roster truthfully assert right now?
- *None* — no rules declared, nothing connected.
- *Declared* — the owner told us their capacity rules.
- *Observed* — a system reports real availability.

**Axis 2 — Authority.** What has the owner allowed Roster to commit?
- *Capture only* — take the job, promise a callback.
- *Book within policy* — commit anything matching the rules.
- *Book everything* — including exceptions.

**Software sets the ceiling on Axis 1. The owner sets Axis 2.** A business can
sit anywhere on the grid, and it moves as trust grows — which is exactly how
hiring a real office manager feels. Week one they check with you. Month three
they just handle it.

This produces **three workflow shapes, not five**:

| Shape | Certainty | Authority | Roster says |
|---|---|---|---|
| **A · Assisted** | None | Capture only | "You're in the queue. The office will confirm your time and text you back today." |
| **B · Delegated** | Declared | Within policy | "You're booked for Tuesday morning, 8–12." |
| **C · Integrated** | Observed | Within policy | "You're booked for Tuesday 8–12." *(same words, better precision, lands in their system)* |

**B and C give the customer an identical experience.** That is the most
important line in this document. The integration improves *precision and
where the job lands* — it does not improve what the customer hears. So you can
ship B, with no integrations at all, and capture nearly all of the customer-facing
value.

### The exception policy — the same for every shape

Regardless of shape, some bookings always go to a human. This is where owner
approval belongs — **once, as policy, not per booking**:

- Outside the service area *(already implemented — `engine._service_area_note`)*
- Emergencies *(already escalates via `alert_owner`)*
- Estimated value above a threshold
- Outside declared hours
- A customer flagged by the owner
- Anything the AI is unsure of

**Shift the owner's approval from per-booking to per-policy.** You tell an
office manager the rules once and they handle the rest, bringing you only the
odd ones. That single change is what makes Roster feel like staff instead of
software — more than any integration will.

---

## 4. Your five categories, answered

### 4.1 Businesses with no software (paper, whiteboard, Excel, WhatsApp)

**This is the ICP and should be designed for first, not last.** Roster's target
is 5-truck plumbing and HVAC shops; a large share run on a whiteboard and a
phone. They are also the only segment where Roster has no serious competitor,
because there's no API for anyone to integrate with.

**Day 1 (Shape A):**
- **Customer:** *"Got it — AC not cooling at 44 Oak Street, and mornings work
  best for you. You're in the queue and the office will text you to confirm
  your window today."* Then, when the owner acts: *"You're confirmed for
  Thursday 8am–12pm. See you then."*
- **Owner:** one actionable text — reply `Y412` to confirm, `N412` to reject,
  or text a better time.
- **Approval required:** yes, every booking.
- **Confirm immediately:** no.
- **Wait:** yes — but bounded, with the customer told what the bound is.
- **Ends:** when the owner acts and Roster texts the customer back. *(Today it
  ends nowhere — `confirm_job` sends the customer nothing. Verified.)*

**Day 3 onward (Shape B) — the goal state.** Onboarding asks three questions:
how many jobs a day, what arrival windows, which days.
- **Customer:** *"You're booked for Tuesday morning, 8am–12pm. We'll text you
  the evening before to confirm."*
- **Owner:** no per-booking action. A morning digest of the day's bookings, and
  the ability to bump anything.
- **Approval required:** no — pre-authorized by the capacity rules.
- **Confirm immediately:** **yes, honestly.**
- **Ends:** at the booking, in one conversation.

**The honesty test:** is "you're booked Tuesday 8–12" true when the owner has
declared 6 jobs/day and this is #4? Yes — as true as any home-services booking
gets, because the owner authorized exactly this. The risk is a full day the
owner forgot to flag, and the mitigation is the same one a paper diary has: the
owner sees the day and moves someone. Roster then texts the customer. That is
strictly better than today (limbo) and better than per-booking approval (work
every time).

### 4.2 Google Calendar

**I'd deprioritize this, and I think it's a trap.** It looks like the easy
first integration and it's the worst fit of the five.

A Google Calendar shows **one person's schedule**. A 5-truck shop has five
techs; the owner's calendar says nothing about whether *a technician* is free —
it says whether *the owner* is free, which is the wrong question. It gives the
false confidence of Shape C with the actual certainty of Shape A.

It's only honest for a genuine **one-person operation** where the owner *is*
the technician. That's a real segment, just a small one — and for them, the
capacity model of Shape B works just as well with no OAuth, no token refresh,
and no consent screen.

- **Approval required:** yes, unless one-person shop.
- **Confirm immediately:** only for a true solo operator.
- **Recommendation:** treat as Shape B; revisit only if a paying solo operator
  specifically asks.

### 4.3 Housecall Pro and Jobber

**This is where the first real integration belongs** — 1–10 truck shops,
squarely the ICP, with APIs that are obtainable without a partnership program.

- **Customer:** identical to Shape B — *"You're booked for Tuesday morning,
  8am–12pm."*
- **Owner:** the job appears in the system their crew already uses. **This is
  the actual value** — not better customer wording, but no double entry. Under
  Shape B the owner still retypes the job into their own system; here they
  don't.
- **Approval required:** no, within policy.
- **Confirm immediately:** yes — with a caveat below.
- **Ends:** at the booking, with the job written into their system.

**Caveat (§2.1):** even here, confirm against *bookable capacity*, not raw
calendar gaps. Use the platform's own scheduling/availability concept where one
exists, and keep drive-time and skill-matching as exceptions routed to the
owner until proven. **Do not assume an open slot means a feasible job.**

### 4.4 ServiceTitan

**I'd rank this last, and I don't think it's an ICP fit today.**

- ServiceTitan customers are typically 10+ trucks and **already employ a
  dispatcher or CSR**. Roster's pitch — "you're missing calls because nobody
  can answer" — is weakest exactly there.
- API access requires going through a partner program, not just a key. That's a
  business-development timeline, not a sprint.
- It's the segment where Avoca ($125M raised, enterprise scale) is strongest.

Building ServiceTitan first means building for a customer Roster doesn't have,
in the one segment where a much larger competitor is entrenched.

- **Approval required:** no, within policy.
- **Confirm immediately:** yes, with the §2.1 caveat — most strongly here,
  since ServiceTitan shops have the most complex dispatch rules.
- **Recommendation:** build only when a paying customer with ServiceTitan asks
  by name. Not before.

---

## 5. Your seven questions

### 1. What should become the default booking workflow?

**Shape B — Delegated, on a declared capacity model — should be the default for
every business, with Shape A as the automatic fallback until the owner answers
the three capacity questions.**

Not manual-with-approval (creates owner work forever), and not
integration-first (blocks honest confirmation behind an API most of the ICP
doesn't have). Every business can reach Shape B in a two-minute onboarding
conversation, regardless of software.

### 2. Which providers should exist?

Honestly, two of these aren't integrations at all — and that's the point:

| Provider | What it is | Priority |
|---|---|---|
| **Assisted** | No rules declared. Capture + owner confirm. | **Build first** — it's the safe fallback |
| **Capacity** | Owner-declared rules. Honest confirmation, no integration. | **Build first** — this is the default |
| **Housecall Pro** | Real availability + write-back | When a paying customer asks |
| **Jobber** | Real availability + write-back | When a paying customer asks |
| **Google Calendar** | Owner's personal calendar | Only for solo operators who ask |
| **ServiceTitan** | Enterprise dispatch | Last. Wrong ICP today |

### 3. What belongs to Booking Manager?

Everything that must be identical for every business:

- The state machine and every transition *(the only writer of booking state)*
- **The exception policy engine** — service area, value threshold, hours,
  emergency. This is Booking Manager's, **not** the provider's, because the
  rules are the same whether or not a shop has software. Putting it in the
  provider forks it six ways.
- All customer messaging, and the honesty guarantee via `booking_language.py`
- All owner notification and the actionable-SMS reply handling
- The audit trail
- Choosing the shape: *what may I truthfully say, given this business's
  certainty and authority?*

### 4. What belongs to Booking Providers?

Only what genuinely varies:

- `can_confirm_immediately()` — false for Assisted, true for Capacity and the
  integrations
- `check_availability(window)` — declared-quota arithmetic, or a real API call
- `reserve(window)` — decrement the quota, or write to the external system
- `release(booking)` — on cancellation
- `external_ref` — the id in their system, if any

**Note what's absent:** no `confirm_booking`, no `propose_new_time`, no
`cancel_booking`. Those are *state transitions*, and state belongs to Booking
Manager for every provider. This is the same narrowing argued in the technical
design's Challenge 5, now with a product reason behind it: the customer
experience must not vary by provider, so nothing that shapes the customer
experience may live in one.

### 5. What should be built first?

In order, and none of it is an integration:

1. **Close the loop with the customer.** When the owner confirms, text them.
   Today `confirm_job` sends nothing *(verified)*. Smallest change, biggest
   honesty win — it's the difference between a message-taker and an office.
2. **Owner-phone recognition on inbound SMS.** Blocking bug: an owner replying
   to a Roster alert is treated as a customer and gets booked a job *(verified
   — `escalation_phone` is absent from the entire inbound path)*.
3. **Actionable owner SMS** — `Y412` / `N412` / a better time. Shape A becomes
   genuinely usable from a roof.
4. **The capacity model** — three fields, three onboarding questions. This is
   what makes Shape B the default and removes the owner from the loop.
5. **The exception policy** — value threshold and hours, reusing the service-area
   and emergency checks that already exist.

### 6. What should wait for real customers?

- **Every FSM integration**, including Housecall Pro and Jobber. Build the first
  one when a paying pilot names their tool — which is already ROADMAP.md's
  standing rule for integrations.
- **ServiceTitan**, most of all.
- **Google Calendar**, until a solo operator asks.
- **Drive-time and skills-based feasibility.** Real, and correctly handled for
  now by routing exceptions to the owner rather than modelling routing.
- **`NO_SHOW`, `COMPLETED` as booking states** — see the technical design;
  completion already lives on `completed_at` and four employees depend on it.
- **Customer self-reschedule.** Frontdesk already recognizes the intent; letting
  a customer move a job the owner has dispatched a truck for needs thought.

### 7. Where I think you're wrong

1. **"Software determines the workflow."** Certainty × authority determines it.
   Software only caps certainty. *(§2.2 — the assumption inverts in both
   directions.)*
2. **"If ServiceTitan can verify availability, no approval needed."** Verified
   availability isn't verified feasibility, and capability isn't permission.
   *(§2.1)*
3. **"Manual mode cannot verify availability."** It can, against declared
   capacity. This is the unlock that makes honest instant confirmation
   available to businesses with no software at all. *(§2.3)*
4. **"I want the correct workflow for each business type."** Five workflows is
   five customer experiences, five sets of edge cases, five things to keep
   honest. There are three shapes, and two of them are indistinguishable to the
   customer. **Fewer workflows is the better product**, not a compromise.
5. **Implied ordering — integrations as the destination.** The destination is
   *no owner in the loop for routine work*. Integrations are one way to get
   there and not the fastest; declared capacity gets there in a two-minute
   conversation, for the segment Roster actually sells to.

---

## 6. What this looks like in one year

The same customer, the same words, three different businesses:

> *"You're booked for Tuesday morning, 8am–12pm. We'll text you the evening
> before to confirm."*

- **The whiteboard shop:** Roster counted against 6 jobs/day. The owner sees a
  morning digest.
- **The Housecall Pro shop:** Roster checked real availability and wrote the job
  into their system. The owner sees it on their board.
- **The ServiceTitan shop:** same, plus a technician assignment.

**The customer cannot tell them apart, and that is the goal.** The integration
changes where the job lands and how precise the promise is. It does not change
whether Roster is honest, fast, or worth paying for.

The businesses that never buy software still get an AI office. That's the
market nobody else is serving, and it's reachable without a single integration.

---

## 7. Open questions for you

1. **Will owners actually declare capacity?** The entire Shape B recommendation
   rests on a 5-truck owner answering "how many jobs a day, what windows, which
   days." I believe they will — they answer it constantly on the phone — but I
   have not asked one. **This is the assumption to test on the next customer
   call, before any of this is built.**
2. **What's the value threshold for a human?** I'd guess a routine repair is
   safe to auto-book and a replacement isn't. Real number needed from an owner.
3. **Does the day-before confirmation text belong in Shape B?** It's what makes
   the capacity model safe — the last chance to catch an overbooked day — but
   it's another outbound text on a customer already receiving several *(and the
   employees don't coordinate outbound; see `membership_engine.py:24`)*.
4. **Should Shape B be opt-in or opt-out at onboarding?** Opt-out gets more
   businesses to the good state; opt-in is more conservative with someone
   else's crew. I lean opt-in for the first ten customers, opt-out after.
