# Self-Serve Signup, Frontdesk-First Activation, Customer Dashboard

**Date:** 2026-07-10 (revised twice same day after founder review)
**Surfaces:** new `agent/auth.py`; new templates (`signup.html`, `login.html`,
`onboarding_business.html`, `onboarding_receptionist.html`, `activation_live.html`,
`dashboard.html`, `roster.html`); retires `agent/templates/hire.html`,
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

## The customer-facing employee model (key decision)

Customers think in problems, not in agent names. The internal agent
architecture (Frontdesk, Chaser, Rebooker, Renewals, Reviews) stays exactly as
it is in the engine, but the customer never sees those names. They see roles
mapped to the problem being solved:

| Customer problem | Customer sees (role) | Internal agent(s) |
|---|---|---|
| Missing calls | **AI Receptionist** *(trade-adapted, see below)* | Frontdesk |
| Losing quotes | **Quote Chaser** | Chaser |
| Customers never come back | **Retention Manager** | Rebooker + Renewals + Reviews |

**Retention Manager is one employee with adaptive behavior, not a bundle the
customer is forced to buy in full.** It performs whichever retention work
actually applies to that business, inferred from real data already on the
client (existing service/job history → rebooking nudges; existing
membership/contract records → renewal reminders; any completed job → a review
ask). A plumber with no membership plans simply never gets renewal messages
because there's nothing to renew — not because a toggle was switched off. No
extra onboarding question is needed to configure this; it's a natural
consequence of what data exists for that client. Reviews is not a separate
hire or a separate line in "Your Roster" — it's folded into whichever
employee is live, same as today's "feature every agent gets" decision.

**Trade-adaptive naming for AI Receptionist** — a small fixed lookup, not a
per-trade essay:

| Trade | Customer-facing name |
|---|---|
| HVAC | CSR |
| Plumbing | Receptionist |
| Electrical | Office Manager |
| Roofing | Office Coordinator |
| *(default, any other trade)* | Receptionist |

"Frontdesk" (and the other internal names) remain the internal code names
used in the engine, admin dashboard, and codebase — never shown to the
customer.

## Resolution: sequential hiring, minimal questions, "Your Roster"

Signup leads with one hire — the AI Receptionist — no catalog, no
choice-paralysis. Onboarding asks only what a real employer would tell a new
hire on day one. Attribution ("where'd you hear about us") is asked after the
person already trusts the product, not as a gate before value. Expansion is
strictly sequential and framed as hiring, never as an app-store catalog:

- **"Your Roster"** (not "Your Team," not "catalog") shows: currently-live
  employees, then exactly one **"Hire next"** card (hireable now), then any
  further roles as **"Coming later"** (visible for narrative/brand reasons,
  greyed out, no [Hire] button) until the current "next" hire is made. This
  reinforces the existing "hire one role at a time" positioning at the UI
  level, not just in copy.
- Hire order: AI Receptionist → Quote Chaser → Retention Manager.

## Flow

```
/signup (email + password)
   → /onboarding/business       (business name, hours, "what services do you
                                 offer?" with example placeholder — tone is
                                 NOT asked; defaults to "professional and
                                 friendly," editable later from the dashboard)
   → /onboarding/receptionist   (0-1 receptionist-specific question, only if
                                 genuinely needed)
   → activation                 (provision Twilio number if missing, mark
                                 Receptionist/Frontdesk live, initialize trial
                                 spend cap)
   → "Your employee is live" reveal (activation_live.html):
        <Role name> is live. Your new number: (555) 123-4567.
        We're now answering calls for <business name>.
        What happens now:
          ✓ We'll answer every incoming call.
          ✓ We'll book jobs.
          ✓ We'll text confirmations.
          ✓ You can edit anything later.
        Primary CTA: "📞 Call your <role name>" (dials the client's own new
        number — the first wow moment happens in 30 seconds, not by waiting
        for a real customer to call in).
   → /dashboard
        - Zero-data state: "<Role name>: Live — waiting for your first call."
          with the same "Call your <role name>" CTA as the primary action.
          No 0/0/$0 metrics grid.
        - Once Job/Message rows exist for this client: switches to outcomes
          (calls answered, jobs booked, revenue recovered).
        - One-time dismissible banner, shown a few days after activation, not
          at signup: "Quick question — where'd you hear about Roster?"
        - "Your Roster" section: live employees + "Hire next" card (e.g.
          Quote Chaser: "Follows up every estimate automatically." [Hire] —
          no invented statistics) + "Coming later" card(s), greyed, no button.
```

Returning visits: `/login` (email + password) → `/dashboard`.

## Data model

Extend `Client` (agent/db_models.py) with:
- `email` (unique, indexed), `password_hash`
- `business_hours`, `services_offered` (free text, prompted as "what services
  do you offer?"), `tone` (defaults to "professional and friendly," not asked
  at signup, editable later)
- `source` (nullable — filled in later from the dashboard banner, not at signup)
- `trade` (drives both the Receptionist display-name lookup and, later, which
  auto-research heuristics apply)
- `trial_spend_cents` (default 0), `trial_cap_cents` (default hard cap, e.g.
  2000 = $20), `trial_soft_buffer_cents` (default 200 = $2 grace on top of the
  hard cap)

No new schema needed for agent state or for the Retention Manager grouping —
Frontdesk/Chaser/Rebooker/Renewals/Reviews already have live/config
representation on `Client` and the recovery models; "Retention Manager" is a
display-layer grouping over Rebooker+Renewals+Reviews, not a new engine
concept.

## Components

- **`agent/auth.py`** (new): signup/login routes, password hashing (bcrypt via
  `passlib`), session cookies via Starlette `SessionMiddleware`. Fully separate
  from the founder's Basic-Auth-protected admin routes — no shared auth path.
- **Display-name mapping** (new, small module or dict in `agent/app.py`):
  `trade → receptionist display name` lookup (table above), plus the fixed
  internal→customer role-name map (Frontdesk→AI Receptionist/CSR/etc.,
  Chaser→Quote Chaser, {Rebooker,Renewals,Reviews}→Retention Manager). Used
  everywhere the customer-facing UI renders a role name.
- **New templates**: `signup.html`, `login.html`, `onboarding_business.html`,
  `onboarding_receptionist.html`, `activation_live.html` (the "you're live"
  reveal with the What Happens Now checklist and primary call CTA),
  `dashboard.html` (customer-facing, zero-data and has-data states),
  `roster.html` (the "Your Roster" section: live + hire-next + coming-later).
- **Retired**: `hire.html`, `hire_done.html`, their routes in `app.py`. The
  landing page's `/hire` CTAs are repointed to `/signup`.

## Activation & trial spend cap

On activation: provision a Twilio number if the client doesn't have one, mark
the Receptionist (Frontdesk) live, set `trial_spend_cents = 0`. Every
LLM/Twilio call in `engine.py` and `recovery_engine.py` increments
`trial_spend_cents` for that client before running the call.

- Crossing `trial_cap_cents` (hard cap): notify the founder immediately (so
  they can raise the cap or move the client to paid) and show a dashboard
  banner to the customer. The agent **keeps answering** through the soft
  buffer — a caller mid-conversation is never silently dropped.
- Crossing `trial_cap_cents + trial_soft_buffer_cents`: agent pauses. This
  only happens after the founder has already been notified once, so it's a
  backstop, not the primary control.

## Dashboard states

- **Zero-data (first login, no Job/Message rows yet):** "<Role name>: Live —
  waiting for your first call," with "Call your <role name>" as the primary
  CTA. No 0/0/$0 metrics grid — an empty numbers table reads as broken.
- **Has-data:** outcomes summary (calls answered, jobs booked, revenue
  recovered), pulled from existing `Job`/`Message` tables filtered by
  `client_id`.
- **Your Roster:** live employees (status: Live), one "Hire next" card
  (outcome-description copy, no invented statistics, [Hire] button), any
  further roles shown "Coming later" (greyed, no button) until the current
  next hire is made. Hire order fixed: AI Receptionist → Quote Chaser →
  Retention Manager.
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
  (Receptionist only, no further hires).
- Dashboard transitions from zero-state to outcomes-state once a Job/Message
  row exists for the client.
- Trade → display-name lookup returns the correct role name for each mapped
  trade and the default for unmapped trades.
- "Hire next" → "Coming later" sequencing: only one role is ever hireable at
  a time, and the next role advances only after the current one is hired.
- Retention Manager behavior is adaptive: a client with no contract/membership
  records never triggers renewal-style messaging; a client with no rebooking-
  eligible job history never triggers rebooking-style messaging; review asks
  fire on any completed job regardless of which employee is live.
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
- Per-trade Retention Manager behavior tuning beyond "use whatever data
  exists" (e.g. industry-specific renewal cadences) — the adaptive-by-data
  behavior described above is the full scope here; deeper tuning is a later
  iteration once real clients are on it.
- **Automatic target-discovery for Quote Chaser / Retention Manager.** Today
  those agents only work off a customer list the founder manually uploads
  (`recovery_service.create_campaign`) — there's no engine yet that scans
  existing `Job` records to find cold quotes or dormant customers on its own.
  This spec implements full self-serve activation for the Receptionist only.
  When Quote Chaser becomes the "Hire next" card, clicking [Hire] captures
  its short question(s) and queues the request the same way today's
  `requested_roster` field does — the founder still sets up the actual
  campaign by hand. Building real target-discovery so this goes fully
  self-serve is its own follow-up spec, sized on its own.
