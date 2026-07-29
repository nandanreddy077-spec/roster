# Frontdesk Production Readiness Audit — Conversation Quality & Business Logic

**Date:** 2026-07-29. **Scope:** not infrastructure (that audit and its 8-item
fix sprint are done — see
`docs/superpowers/specs/2026-07-29-frontdesk-voice-loop-production-audit.md`).
This is Frontdesk **as a customer would experience it**: 100 real HVAC,
Plumbing, Electrical, Roofing, Landscaping, Cleaning, Garage Door, Pest
Control, Painting, and Pool Service businesses, live tomorrow.

**Method:** read the actual code a caller's words pass through —
`engine.py`'s two system prompts, `LOG_JOB_TOOL`'s schema, `models.ClientConfig`,
`memory.build_customer_context`, `bookings.book_job`, `db_models.Job`. Every
finding below is grounded in what that code literally does or literally lacks
a field/branch for — not a general "AI could be smarter" complaint.

**Constraint honored throughout:** no new architecture, no new subsystem, no
platform redesign. Where the complete fix genuinely requires a department
that doesn't exist yet (Operations — dispatcher/route_optimizer are still
`planned` in `employees.py`), that's stated explicitly, separate from what
Frontdesk itself can close today with a prompt or schema change.

Ordered by customer impact (not effort). Each item: severity, why it matters
to a home-service business, recommended implementation, effort, and whether
it blocks a credible production launch.

---

## Tier 1 — Would disappoint nearly every caller, every vertical

### 1. No appointment scheduling — the call never ends with a real commitment

**Severity: Critical.**

**Why it matters.** The entire point of a home-service phone call, from the
customer's side, is "when is someone coming." `LOG_JOB_TOOL`'s schema has
`customer_name, service_type, urgency, address, callback_number, notes` —
no `preferred_time` or `preferred_window` field, structured or otherwise, and
the system prompts never instruct the model to ask "what day/time works for
you." A caller today hears "someone will get back to you" (or worse, the
generic fallback text in `engine.py`: *"Thanks! I've got your details and
someone will text you shortly"*) instead of "we can have someone out Thursday
between 2 and 4." Every one of the 10 verticals expects a window, not a
promise to be contacted later — this is the single highest-frequency
disappointment, because it happens on 100% of calls, not an edge case.

**Recommended implementation.** Not a scheduling/dispatch system — that's
Operations department scope, and `dispatcher`/`route_optimizer` don't exist
yet. What Frontdesk can do today, within its current shape: add
`preferred_window: {"type": "string"}` to `LOG_JOB_TOOL`'s schema (free text
— "Thursday afternoon," "first thing tomorrow" — not a real slot), a
matching nullable `Job.preferred_window` column (same migration pattern as
`review_requested_at`/`owner_alerted_at`), and one prompt line instructing
the model to ask for and repeat back a preferred window before ending the
call. This closes the "the call ended with nothing concrete" complaint even
though real slot confirmation still requires the owner to call back and
commit a time — which is honest, not a new promise the AI can't keep.

**Effort:** Small (one schema field, one migration line, one prompt
sentence, no new module). **Blocks production:** Yes, in the sense that
launching without it means every single call ends the way callers find most
frustrating about IVR/lead-capture tools generically — the exact reputation
this product is trying not to have.

---

### 2. One generic prompt for all 10 trades — no industry-specific triage

**Severity: Critical.**

**Why it matters.** `build_system_prompt`/`build_voice_system_prompt` are
identical in shape for every `client.trade` value — the only per-trade
inputs are `services` (a plain list) and the one free-text `pricing_faq`
blob. A real dispatcher asks different triage questions per trade before
booking: HVAC ("is it blowing air at all, what's the thermostat set to"),
Plumbing ("is water actively leaking right now, do you know where the
shutoff is"), Electrical ("any burning smell or sparking, is the breaker
tripped"), Roofing ("is water actively coming in, one section or the whole
roof"), Garage Door ("is it stuck open or stuck closed" — a security
distinction, not just a comfort one), Pest ("what kind, and is anyone
allergic/been stung"), Pool ("green/cloudy water, or is it a safety issue
like a fence gate"). None of this exists. The emergency examples hardcoded
into the voice prompt (*"gas leak, flooding, no heat in freezing weather"*)
are HVAC/plumbing-specific — there is no electrical-hazard example at all
(sparking outlet, burning smell, exposed wire), which is arguably a
genuine safety gap, not just a quality one: an electrical business running
on this exact same prompt has no example nudging the model toward
`alert_owner` for a live electrical hazard.

**Recommended implementation.** No new architecture — a per-trade static
copy table, the same shape `departments.py`'s registry already uses for
customer-facing copy. Add a small `TRADE_TRIAGE_NOTES: Dict[str, str]`
keyed by `client.trade` (a handful of sentences per trade: the 2-3
questions to ask, and that trade's own emergency examples), interpolated
into both system prompts alongside the existing `services`/`pricing_faq`
lines. This is copy, not code architecture — same pattern as
`METRIC_LABELS`/`CUSTOMER_STATE_LABELS` being a plain dict.

**Effort:** Medium (one dict with ~10 entries, each a few sentences;
requires someone who knows each trade to write accurate triage questions —
the writing effort exceeds the engineering effort). **Blocks production:**
Yes for the safety dimension (electrical hazard framing specifically); for
the other 9 trades it's a quality/differentiation gap more than a safety
one, but still directly undermines "feels like a person who knows this
trade," the product's central promise.

---

### 3. No awareness of the current date or time

**Severity: High, but the cheapest fix in this entire audit.**

**Why it matters.** Neither prompt injects today's date or the current
time — confirmed by grep across `engine.py`, `xai_voice_adapter.py`,
`service.py`, `memory.py`: there is no `datetime.now()`/`strftime` call
feeding either system prompt. `hours` is a free-text string
("Mon-Sat 8am-6pm") the model has to reason about with **no actual
knowledge of what time it currently is**. This directly undermines the
scenario the "backup" `answer_mode` exists for in the first place — a
missed call is disproportionately likely to be after-hours or during a busy
stretch, and that's exactly when a receptionist most needs to say "we're
closed until 8, but I've got you down and we'll call first thing" instead
of guessing or ignoring the hours string entirely.

**Recommended implementation.** Add one line to both `build_system_prompt`
and `build_voice_system_prompt`: the current local date/time (business
timezone if stored, else server time), formatted plainly — e.g. "Right now
it's Tuesday, July 29, 9:47 PM." No schema change, no new field, one
`datetime.now()` call at prompt-build time.

**Effort:** Trivial (a few lines in `engine.py`). **Blocks production:**
Yes — this is the highest impact-to-effort ratio item in the whole audit;
there's no good reason to ship without it.

---

### 4. No reschedule/cancel/"look up my existing booking" flow

**Severity: High.**

**Why it matters.** A meaningful share of real inbound calls to any home
service business are NOT new leads — "I need to move Thursday's
appointment," "can you cancel, I found someone else," "is the tech still
coming today." `log_job` only ever creates or (via `book_job`'s dedup
window) merges into an *open, same-service-type, same-thread* job from the
last 24 hours — there is no tool or prompt instruction for "find my
existing booking and change/cancel it." A caller trying to reschedule today
either gets a confused AI improvising, or — worse — a second, near-duplicate
Job row if they describe the issue slightly differently the second time
(different wording defeats `book_job`'s exact-string service-type match).

**Recommended implementation.** A second small tool, same shape as
`LOG_JOB_TOOL`/`TRANSFER_CALL_TOOL`: `update_job` (or extend `log_job`'s
description to explicitly cover "if the caller has an existing appointment
and wants to change or cancel it, say so in `notes` and set urgency
accordingly" as a lighter first step) plus one prompt sentence teaching the
model to ask "is this about an appointment you already have, or something
new" early in the call. Full two-way calendar sync is out of scope (that's
Operations); the achievable near-term fix is recognizing the intent and
routing it honestly, even if "I'll have someone call you to confirm the
new time" is the honest answer rather than a real reschedule.

**Effort:** Small–Medium (one new tool + a prompt branch; the underlying
`Job` model already has everything needed to represent "still open,
address/notes updated"). **Blocks production:** Borderline — not every
call is a reschedule, but shipping with zero handling for a very common
call type is a real, everyday gap, not a rare edge case.

---

## Tier 2 — High impact, trade- or segment-dependent

### 5. No service-area / distance qualification

**Severity: High** for Roofing, Landscaping, Pool Service, Pest Control
(all commonly radius-bound). **Why it matters.** `ClientConfig` has no
service-area/zip-radius field at all — a caller 60 miles outside the
business's actual range gets booked exactly like a next-door neighbor, and
the mismatch surfaces later as an awkward callback canceling a "confirmed"
booking, which reads far worse to the customer than never having promised
it. **Recommended implementation.** One optional free-text
`service_area` field alongside `pricing_faq` (same onboarding-form pattern),
interpolated into the prompt with an instruction to ask for a zip/city
early and flag out-of-area callers honestly rather than booking then
walking it back. **Effort:** Small. **Blocks production:** No, but high
value for the radius-bound trades specifically.

### 6. No membership/recurring-plan/warranty awareness

**Severity: High** for Pest Control, Pool Service, Landscaping, Cleaning
(all commonly subscription/plan-based). **Why it matters.**
`memory.build_customer_context` surfaces name + last 3 jobs only — nothing
distinguishes "on a quarterly plan, this visit is already paid for" from
"first-time caller." A plan customer calling about a covered issue getting
treated (and possibly quoted) like a brand-new lead is a specific,
recognizable "the AI didn't know who I was" complaint, worse than generic
memory gaps because these businesses' whole retention model depends on
customers feeling remembered. **Recommended implementation.** Nothing new
architecturally — `Customer` already exists; add a nullable
`plan_notes`/`membership` free-text field the owner can set per customer
(founder console, not self-serve), surfaced the same way past-jobs already
are in `build_customer_context`. **Effort:** Small–Medium (one column, one
founder-console field, one context-string line). **Blocks production:** No,
but a real, foreseeable complaint for exactly the businesses with
recurring-revenue models — 4 of the 10 verticals here.

### 7. No comeback/warranty-callback signal

**Severity: Medium-High.** **Why it matters.** "You were just here and it's
still broken" needs faster handling and a different tone than a routine
new booking — nothing today distinguishes a callback-on-recent-work from an
unrelated new issue; both get whatever urgency the model infers from
`log_job`'s three-value enum. A comeback treated as routine `same_day`
booking is a reputational risk out of proportion to its frequency.
**Recommended implementation.** `build_customer_context` already has the
last 3 jobs' dates and service types in scope — one prompt instruction:
"if the caller mentions the same problem as a job from the last 14 days,
treat it as a callback, not a new booking, and lean toward higher urgency."
No new data, just an instruction over data already present.
**Effort:** Trivial (prompt only). **Blocks production:** No.

### 8. Pricing is one flat blob, not per-service

**Severity: Medium-High.** **Why it matters.** `pricing_faq` is a single
free-text field covering every service the business offers (the onboarding
placeholder itself mixes a diagnostic fee, a same-day-fix waiver, and a
membership price in one blob). A caller asking specifically "how much for a
water heater replacement" gets whatever's in that one blob, whether or not
it actually answers that question — likely either irrelevant or silence.
This is a real conversion-affecting gap: home-service callers price-shop
aggressively, and "I don't have exact pricing for that" reads very
differently than a confident ballpark the business is actually willing to
give. **Recommended implementation.** Not a structured price list (that's
a bigger, separate feature) — the honest, in-scope improvement is a prompt
instruction to use `pricing_faq` for what it actually covers and say so
plainly when a specific ask isn't in there, rather than reciting the whole
blob regardless of fit. **Effort:** Trivial (prompt wording). A true
per-service price list is a separate, larger feature — noted, not
recommended here. **Blocks production:** No.

---

## Tier 3 — Real, but lower frequency or lower severity

### 9. No explicit rule for "let me talk to a person"

**Severity: Medium.** A caller who distrusts AI and asks directly for a
human should get the owner's number immediately — today the voice prompt
only routes to `alert_owner` when the model judges it "can't confidently
handle" something or the caller is upset, not on an explicit direct
request. **Fix:** one sentence — "if the caller asks to speak to a person,
comply immediately, don't try to keep helping first." **Effort:** Trivial.
**Blocks production:** No.

### 10. No text confirmation after a voice call, and the escalation number is only ever spoken

**Severity: Medium.** A voice caller gets nothing durable after hanging up
— no confirmation text of what was booked, and someone told the owner's
number verbally during a stressful emergency call has to remember or write
it down correctly with nothing else going on. **Fix:** after `log_job`
commits, send a short SMS confirmation to `callback_number` via the
existing SMS channel (`channels.py`) — the same send path already used for
owner texts, just addressed to the caller instead; same treatment for
`alert_owner`'s escalation-phone number. **Effort:** Small (reuses
`channels.get_channel()`, no new channel/abstraction). **Blocks
production:** No, but a cheap, meaningful trust-builder, especially for the
emergency case.

### 11. No non-English fallback

**Severity: Medium-High** for Landscaping, Cleaning, Pool Service, Pest
Control specifically (markets with disproportionately more Spanish-speaking
callers/crews in many US regions). Nothing in either prompt addresses a
caller speaking anything other than English. **Fix:** at minimum, an
instruction to recognize Spanish and either continue in Spanish (models are
generally capable of this already) or gracefully route to
`alert_owner`/callback rather than pushing through a conversation neither
side can complete. **Effort:** Small (prompt only, verify against a real
call). **Blocks production:** No, but a real gap for a meaningful share of
callers in several of these verticals specifically.

### 12. No objection-handling guidance

**Severity: Medium.** "That sounds expensive," "the other guy quoted less,"
"can you do a discount" — extremely common in this industry, and the
prompt's only relevant instruction is "never make up a price," which is
correct but not a full answer to a price objection. **Fix:** a short,
generic script line: acknowledge, don't argue, don't invent a discount,
offer to have the owner discuss pricing directly if the caller pushes.
**Effort:** Trivial. **Blocks production:** No.

### 13. No sales/quote vs. repair/service distinction

**Severity: Medium**, higher for HVAC, Roofing, Garage Door, Pool
(common big-ticket replacement/installation calls, not just repairs). A
"my AC is 15 years old, thinking about replacing it" call needs different
qualifying questions (system age, square footage, single/multi-zone) than
a repair call, and arguably different urgency handling entirely (not
urgent, but high-value) — `log_job`'s schema has no way to represent this
distinction at all today. **Fix:** no new tool needed — add "estimate" as
a valid `urgency`-adjacent signal in `notes`, and one prompt instruction
recognizing quote/estimate requests as a distinct conversation type worth
a few extra qualifying questions before booking. **Effort:** Small.
**Blocks production:** No.

---

## Tier 4 — Real but lower priority

### 14. No explicit multi-issue-call guidance

**Severity: Low-Medium.** The *mechanism* already supports two genuinely
separate issues in one call correctly — `book_job`'s dedup keys on
`(thread, service_type)`, so two different `service_type` values in one
call already create two separate `Job` rows without any change needed.
What's missing is prompt guidance telling the model to *recognize and
split* "my AC isn't cooling and I also have a leak under the sink" into
two `log_job` calls rather than picking one or conflating them. **Fix:**
one prompt sentence. **Effort:** Trivial. **Blocks production:** No.

### 15. No signal for a caller who's called back multiple times recently

**Severity: Low-Medium.** `build_customer_context` shows past *jobs*, not
past *conversation attempts* — a caller phoning a third time in 20 minutes
because they never got a callback reads identically to a first-time caller
today, missing an obvious frustration signal. **Fix:** would need a small
addition to the context-builder (count recent `Message`/call threads for
this phone number in, say, the last hour) — small, but touches shared
context-building code, so slightly more than prompt-only. **Effort:**
Small. **Blocks production:** No.

### 16. No proactive disclosure of a standard service-call/diagnostic fee

**Severity: Low-Medium**, trade-dependent (common in HVAC, Plumbing,
Electrical, Garage Door). Many businesses charge a standard diagnostic or
trip fee, often disclosed upfront by a human dispatcher to set
expectations — `pricing_faq` may contain this, but nothing instructs the
model to surface it proactively rather than only if asked. **Fix:** prompt
instruction to mention a stated service-call fee (if present in
`pricing_faq`) once, naturally, before ending the call — not before every
sentence. **Effort:** Trivial. **Blocks production:** No.

---

## Summary table

| # | Issue | Severity | Effort | Blocks launch |
|---|---|---|---|---|
| 1 | No scheduling / no concrete next step | Critical | Small | Yes |
| 2 | No industry-specific triage (10 trades, 1 prompt) | Critical | Medium | Yes (safety dimension) |
| 3 | No current date/time awareness | High | Trivial | Yes |
| 4 | No reschedule/cancel flow | High | Small–Medium | Borderline |
| 5 | No service-area qualification | High (radius trades) | Small | No |
| 6 | No membership/plan awareness | High (subscription trades) | Small–Medium | No |
| 7 | No comeback/warranty-callback signal | Medium-High | Trivial | No |
| 8 | Pricing is one flat blob | Medium-High | Trivial | No |
| 9 | No "talk to a human" rule | Medium | Trivial | No |
| 10 | No SMS confirmation / spoken-only escalation number | Medium | Small | No |
| 11 | No non-English fallback | Medium-High (some trades) | Small | No |
| 12 | No objection-handling script | Medium | Trivial | No |
| 13 | No sales/quote vs. repair distinction | Medium | Small | No |
| 14 | No multi-issue-call splitting guidance | Low-Medium | Trivial | No |
| 15 | No repeat-caller frustration signal | Low-Medium | Small | No |
| 16 | No proactive service-fee disclosure | Low-Medium | Trivial | No |

**Reading order for a next sprint:** items 1–4 first (they're the ones a
real business owner would notice on day one, across every vertical); items
3, 7, 9, 12, 14, 16 are all "trivial" prompt-only changes that could
reasonably ship together in one pass; 5, 6, 10, 11, 13, 15 are each small,
standalone, and can be sequenced by which verticals are onboarding first.
