# Self-Serve Signup, Frontdesk-First Activation, Customer Dashboard

**Date:** 2026-07-10 (revised same day after founder review)
**Surfaces:** new `agent/auth.py`; new templates (`signup.html`, `login.html`,
`onboarding_business.html`, `onboarding_frontdesk.html`, `activation_live.html`,
`dashboard.html`, `catalog.html`); retires `agent/templates/hire.html`,
`hire_done.html`, and the `/hire` routes in `agent/app.py`. Founder-facing admin
dashboard (Basic-Auth, `clients.html`, `client_detail.html`) is untouched.

## Why

Two problems drove this:

1. **Trust.** A shop owner who signs up and sees nothing (no login, no
   activity, no numbers) assumes it's a scam — self-serve onboarding needs a
   visible payoff to replace the trust a human onboarding call would normally
   provide.
2. **Scale.** Concierge-only onboarding (founder manually sets up every
   client) doesn't scale at Roster's ACV. Self-serve is the economically
   correct direction at this price point (sub-$5K ACV), but it must not
   collapse into the same "config-your-own-AI-settings" pattern incumbents
   (Housecall Pro's CSR AI) already ship for free — that would erase Roster's
   differentiation ("we run it for you," not "here's a tool to configure").

## Resolution: Frontdesk-first, minimal questions, catalog-as-expansion

Signup leads with **one agent, Frontdesk** — no catalog, no choice-paralysis.
Onboarding asks only what a real employer would tell a new hire on day one
(hours, what you do, how to talk to customers) — everything else is deferred
to manual edits later or to the auto-research build (see Roadmap). Attribution
("where'd you hear about us") is asked *after* the person already trusts the
product, not as a gate before value. The trade-filtered agent catalog
(Chaser, Rebooker, Renewals, Reviews) only appears after Frontdesk is live,
framed as hiring more staff, never as enabling features.

## Flow

```
/signup (email + password)
   → /onboarding/business   (business name, hours, one-line "what do you do",
                             tone — 4 fields, ~30 seconds)
   → /onboarding/frontdesk  (0-1 Frontdesk-specific question, only if genuinely needed)
   → activation             (provision Twilio number if missing, mark Frontdesk
                             live, initialize trial spend cap)
   → "Your employee is live" reveal (activation_live.html):
        Frontdesk is live. Your new number: (555) 123-4567.
        We're now answering calls for <business name>.
        Checklist: Answer calls / Book jobs / Send confirmations.
        [Go to dashboard]
   → /dashboard
        - Zero-data state: "Frontdesk: Live — waiting for your first call." + [Test it now]
          (dials the client's own new number)
        - Once Job/Message rows exist for this client: switches to outcomes
          (calls answered, jobs booked, revenue recovered)
        - One-time dismissible banner, shown a few days after activation (not
          at signup): "Quick question — where'd you hear about Roster?"
        - "Your office now has ✓ Frontdesk — want another employee?" opens the
          catalog: each agent card shows a one-line outcome stat and a
          **[Hire]** button (never "add," "install," or "enable").
```

Returning visits: `/login` (email + password) → `/dashboard`.

## Data model

Extend `Client` (agent/db_models.py) with:
- `email` (unique, indexed), `password_hash`
- `business_hours`, `business_summary` (one-line "what you do"), `tone`
  (a short pick-one, e.g. professional / casual / friendly)
- `source` (nullable — filled in later from the dashboard banner, not at signup)
- `trade`
- `trial_spend_cents` (default 0), `trial_cap_cents` (default hard cap, e.g.
  2000 = $20), `trial_soft_buffer_cents` (default 200 = $2 grace on top of the
  hard cap)

No new schema needed for agent state — Frontdesk/Chaser/Rebooker/Renewals/Reviews
already have live/config representation on `Client` and the recovery models.

## Components

- **`agent/auth.py`** (new): signup/login routes, password hashing (bcrypt via
  `passlib`), session cookies via Starlette `SessionMiddleware`. Fully separate
  from the founder's Basic-Auth-protected admin routes — no shared auth path.
- **New templates**: `signup.html`, `login.html`, `onboarding_business.html`,
  `onboarding_frontdesk.html`, `activation_live.html` (the emotional "you're
  live" reveal), `dashboard.html` (customer-facing, with a zero-data state and
  a data state), `catalog.html` (the "hire another employee" surface, rendered
  inside the dashboard).
- **Retired**: `hire.html`, `hire_done.html`, their routes in `app.py`. The
  landing page's `/hire` CTAs are repointed to `/signup`.

## Activation & trial spend cap

On activation: provision a Twilio number if the client doesn't have one, mark
Frontdesk live, set `trial_spend_cents = 0`. Every LLM/Twilio call in
`engine.py` and `recovery_engine.py` increments `trial_spend_cents` for that
client before running the call.

- Crossing `trial_cap_cents` (hard cap): notify the founder immediately (so
  they can raise the cap or move the client to paid) and show a dashboard
  banner to the customer. The agent **keeps answering** through the soft
  buffer — a caller mid-conversation is never silently dropped.
- Crossing `trial_cap_cents + trial_soft_buffer_cents`: agent pauses. This
  only happens after the founder has already been notified once, so it's a
  backstop, not the primary control.

## Dashboard states

- **Zero-data (first login, no Job/Message rows yet):** "Frontdesk: Live —
  waiting for your first call" + a "Test it now" affordance that dials the
  client's own number. No 0/0/$0 metrics grid — an empty numbers table reads
  as broken, not as "nothing happened yet."
- **Has-data:** outcomes summary (calls answered, jobs booked, revenue
  recovered), pulled from existing `Job`/`Message` tables filtered by
  `client_id`.
- **Expansion:** "Your office now has ✓ Frontdesk — want another employee?"
  → catalog cards (Chaser, Rebooker, Renewals, Reviews), each with a one-line
  outcome stat (e.g. "Average shop recovers $2,800/month") and a **[Hire]**
  button. Copy always uses hiring language, never automation/feature language.
- **Attribution banner:** one-time, dismissible, appears a few days post-
  activation, not blocking anything: "Where'd you hear about Roster?"

## Error handling

- Duplicate email at signup → inline field error.
- Wrong email/password at login → generic "invalid email or password" (no
  user enumeration).
- Trial hard-cap crossed → founder notification + dashboard banner, agent
  keeps running through the soft buffer (see above).
- Soft-buffer exhausted → agent pauses; this is the only case where the
  agent stops responding, and only after two prior warnings (cap + buffer).

## Testing

- Password hash/verify round-trip.
- Signup → activation → reveal screen → dashboard zero-state end-to-end
  (Frontdesk only, no add-ons).
- Dashboard transitions from zero-state to outcomes-state once a Job/Message
  row exists for the client.
- Hire-another-employee flow from the dashboard (second agent activates
  independently of the first).
- Trial cap: agent keeps answering through the soft buffer, founder gets
  notified at the hard cap, agent pauses only after the buffer is exhausted.
- Dashboard query scoping: a logged-in client can only ever see their own
  `client_id`'s data.

## Roadmap (not building now, reprioritized)

**Auto-research pre-fill** (scrape the shop's website/Google Business Profile
→ pre-fill services, hours, FAQ → owner reviews and approves rather than
typing it) is the **next spec after this one**, not a someday-item. It's what
eventually collapses onboarding to just business name / website / phone /
trade — directly tied to Roster's differentiation ("it already knows my
business" vs. filling out forms). This spec ships the reduced-manual-question
version now; the follow-up spec replaces the manual business questions once
auto-research is built.

## Explicitly out of scope (this spec)

- Upgrading past the trial cap / billing integration.
- Founder review/approval gate before an agent goes live (deliberately
  decided against — self-serve activation is immediate; see "Why" above for
  the trust/scale tradeoff this accepts).
- Auto-research pre-fill itself (see Roadmap above — flagged as the
  immediate next spec, not built here).
