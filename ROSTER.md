# Roster — The Complete Idea

_The canonical statement of what we're building. Open this when the picture feels fuzzy._

---

## The thesis
By ~2030, every business — big or small — will run on AI agents. Most of them,
especially small and mid-size businesses, will **never be able to build or run that
in-house.** Today that gap is filled at two extremes:
- **Enterprise AI vendors** (Sierra, Decagon) — $150K–$600K/yr contracts, months-long
  sales, only for big companies.
- **DIY tools** (Zapier, n8n, raw ChatGPT) — too technical for a plumber or dentist to
  set up and maintain.

Nobody owns the easy, managed middle. **Roster is that middle.**

## What Roster is
Not a single AI tool. Roster is a **managed AI operator**: we build, set up, and run a
business's AI workforce *for* them — the work an AI agency charges for, productized.
Underneath we use Claude + our own engine (and tools like telephony) to run each
client's agents; on the surface the owner just sees "it works." **The product isn't the
AI — it's never having to think about the AI.**

## Who it's for first (the wedge)
Home-service trades — **HVAC and plumbing first**, then electrical, roofing,
landscaping, pest control. Chosen because the pain is sharpest and provable fast:
these businesses miss 27–74% of inbound calls and lose ~$189K/yr each, and a missed
call is a job lost to whoever picked up first.

**First agent — "Frontdesk":** missed call → instant AI text-back → answers the
customer → books the job.

## How it's built (the architecture that makes it scale, not a consultancy)
"Customized per business" comes from **assembling reusable parts**, never bespoke code
per client. Three layers:
1. **Config (data)** — each business's services, prices, hours, FAQ, voice. Feels fully
   custom to the owner; costs near-zero to add. _(Built: `ClientConfig`.)_
2. **Capabilities (modular tools)** — booking, calendar push, payment link, review
   request, lead capture. Build each once, switch on/off per client. _(Built: the
   `log_job` tool; more added on demand.)_
3. **Integrations (adapters)** — connect to whatever they run on (Jobber, Housecall Pro,
   Google Calendar). Build each adapter once, reuse for every client on that tool.

**The one rule:** customization = config + pre-built capabilities + pre-built adapters.
When a client needs something new, build it as a *new reusable part*. The parts library
grows from real demand — never from guessing.

## How it makes money
**Outcome-based pricing** (Sierra's enterprise model, applied downmarket): charge **per
booked job**, not a flat monthly fee or per-minute. Aligns our incentive with theirs,
and it's the one lever the commodity $25–$300/mo AI-receptionist tools can't easily copy.

## The moat
A standalone AI receptionist has no moat — it's already a crowded commodity. The moat is
the **full roster of roles**, added one at a time, each one making Roster harder to rip
out — the same switching-cost mechanic that makes ServiceTitan (~$9–12B) sticky: not one
feature, but being embedded in everything the business runs on.

## Role sequence (each only after the previous is rock-solid; each reuses the same data)
1. **Frontdesk** — calls → bookings. _(Built.)_
2. **Chaser** (quote follow-up) — chases every unsold estimate. _(Built.)_
3. **Rebooker** (reactivation) — wakes up dormant customers. _(Built.)_
4. **Renewals** (membership) — chases plan renewals on each customer's own date, before
   they lapse. _(Built.)_ Chaser/Rebooker/Renewals are three faces of one engine, sold
   as three named agents.
5. **Reviews** — texts a review link the moment a job's marked done. _(Built.)_ A
   feature every agent gets, not a separate seat — deliberately unnamed like the agents
   above, since review automation is already commoditized by incumbents.
6. **Workflow plumbing** — get the booked job into their real calendar/CRM. _Not a new
   role — it's what makes every agent above finish the job._ Built from what real
   clients use, never guessed.
7. **Marketing / content** — once there's a customer base and real before/after numbers.
8. **Lead-gen, reframed for trades** — Angi/Google capture, referral nudges (NOT
   LinkedIn scraping — that's a B2B-SaaS tactic, wrong for trades).
9. **Analytics** — last, because it's the exhaust of everything above.

## Vertical expansion
Home services first, all the way through the roles, before touching a second vertical.
Dental, real estate, etc. come later — same `Client`/engine architecture, but built from
real research in that vertical. Going horizontal before proving it once turns this into
an unfocused consultancy.

## Humans in the loop
Agents *reduce* the people needed dramatically — that's the margin engine and why a small
team can serve hundreds of businesses. But **not zero people**:
- Even Claude Code (the best agent product there is) is human-supervised.
- The agent does the labor; a human owns judgment, edge cases, and the relationship.
- The need for a human operator is **literally Roster's product** — we're the person who
  runs the agents so the owner doesn't have to. Remove that and there's nothing to sell.
- Right now (Phase 1) the human needed most is **you** — you are the operation.

## Build phases (advance only on evidence from the prior phase)
1. **Prove it by hand (concierge).** Run the built roster (Frontdesk, Chaser, Rebooker,
   Renewals, Reviews) manually for 3–5 real HVAC/plumbing businesses using the tools
   already built. Prove they'll pay per booked job and the AI holds real conversations.
   _← we are here — zero paying customers yet; the roster is built ahead of demand
   because each new role rides Recovery's existing engine at near-zero marginal cost,
   not because the phase has advanced._
2. **Productize the wedge.** Manual → repeatable: real calendar/CRM integration, one
   working acquisition channel, locked pricing. ~10–20 paying customers.
3. **Build the moat.** Add roles 6–9 (workflow plumbing, marketing, lead-gen, analytics)
   in order; widen to more trades. Roughly the $1M–$10M ARR band where customers pull
   you into the next role themselves.
4. **Expand beyond the wedge.** New verticals; outside capital/accelerator as *tools you
   have leverage to use*, not deadlines you perform for.

## What we deliberately do NOT do now
- Build all 5–6 roles at once (fragments engineering, muddies the pitch).
- Chase enterprise logos like Sierra/Decagon's customers (wrong segment, wrong sales motion).
- Expand to dental/real estate yet (same trap, one level up).
- Guess integrations (calendar/CRM) — build only from what real customers tell us.
- Build an elaborate autonomous loop before there are real conversations to engineer against.

## Where it stands today
- Brand: **Roster.** Landing page built, naming the full roster (Frontdesk, Chaser,
  Rebooker, Renewals) plus the Reviews feature.
- Agent engine: Claude + `log_job`, now a bounded Think→Act→Observe loop (`MAX_ITERS` cap).
- Multi-tenant dashboard: clients, per-customer threads, captured jobs, a 5-tile agent
  roster per client (Frontdesk, Chaser, Rebooker, Renewals, Reviews).
- Real channel: Twilio SMS webhook (replies via TwiML, no outbound creds needed) +
  missed-call text-back scaffold. Routing verified.
- Frontdesk (inbound), Chaser + Rebooker + Renewals (outbound follow-up, one shared
  engine with three named faces), and Reviews (single-button review-request SMS) are
  all built and tested.
- **Missing:** the live LLM path needs `ANTHROPIC_API_KEY` set to verify end-to-end, and —
  the only thing that actually matters — **one real business saying yes.**

---

**One line:** Roster is the company that sets up and runs AI agents for businesses that
can't do it themselves — starting with "never miss a call" for home services, expanding
one proven role and one proven vertical at a time into a full AI workforce.
