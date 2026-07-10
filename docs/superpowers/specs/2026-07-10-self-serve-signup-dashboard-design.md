# Self-Serve Signup, Frontdesk-First Activation, Customer Dashboard

**Date:** 2026-07-10
**Surfaces:** new `agent/auth.py`; new templates (`signup.html`, `login.html`,
`onboarding_source.html`, `onboarding_frontdesk.html`, `dashboard.html`,
`catalog.html`); retires `agent/templates/hire.html`, `hire_done.html`, and the
`/hire` routes in `agent/app.py`. Founder-facing admin dashboard (Basic-Auth,
`clients.html`, `client_detail.html`) is untouched.

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

## Resolution: Frontdesk-first, catalog-as-expansion

Signup leads with **one agent, Frontdesk** — no catalog, no choice-paralysis,
matching the existing "hire one role at a time" positioning and avoiding the
flat-menu failure mode already solved once in the original `/hire` wizard.
The trade-filtered agent catalog (Chaser, Rebooker, Renewals, Reviews) only
appears *after* Frontdesk is live, surfaced from inside the dashboard as an
"Add another agent" expansion — proven value before asking for the next
commitment, and outward-facing framing stays wedge-first, not platform-first.

## Flow

```
/signup (email + password)
   → /onboarding/source     ("where'd you hear about us?" + trade: HVAC/plumbing/electrical/...)
   → /onboarding/frontdesk  (shared business profile: hours, services, tone
                             + Frontdesk's own 1-2 judgment-call questions)
   → activation             (provision Twilio number if missing, mark Frontdesk
                             live, initialize trial spend cap)
   → /dashboard             (outcomes summary: calls answered, jobs booked)
       → "Add another agent" opens the trade-filtered catalog; each agent picked
         gets its own 1-2 short questions, then goes live the same way.
```

Returning visits: `/login` (email + password) → `/dashboard`.

## Data model

Extend `Client` (agent/db_models.py) with:
- `email` (unique, indexed), `password_hash`
- `source` (free text: how they heard about Roster)
- `trade` (HVAC / plumbing / electrical / roofing / landscaping / pest control / other)
- `trial_spend_cents` (default 0), `trial_cap_cents` (default cap, e.g. 2000 = $20)

No new schema needed for agent state — Frontdesk/Chaser/Rebooker/Renewals/Reviews
already have live/config representation on `Client` and the recovery models.

## Components

- **`agent/auth.py`** (new): signup/login routes, password hashing (bcrypt via
  `passlib`), session cookies via Starlette `SessionMiddleware`. Fully separate
  from the founder's Basic-Auth-protected admin routes — no shared auth path.
- **New templates**: `signup.html`, `login.html`, `onboarding_source.html`,
  `onboarding_frontdesk.html`, `dashboard.html` (customer-facing outcomes view),
  `catalog.html` (the "add another agent" surface, rendered inside the dashboard).
- **Retired**: `hire.html`, `hire_done.html`, their routes in `app.py`. The
  landing page's `/hire` CTAs are repointed to `/signup`.

## Activation & trial spend cap

On activation: provision a Twilio number if the client doesn't have one, mark
Frontdesk live, set `trial_spend_cents = 0`. Every LLM/Twilio call in
`engine.py` and `recovery_engine.py` increments `trial_spend_cents` for that
client before running the call; if `trial_spend_cents >= trial_cap_cents`,
the agent pauses (no LLM/Twilio call made) and the dashboard shows a "trial
limit reached" banner. The customer's incoming call/text is not dropped with
an error — the agent simply doesn't respond, and the founder gets a signal to
raise the cap or move the client to a paid plan.

## Dashboard (outcomes-first)

Primary content: jobs booked, calls answered (from existing `Job`/`Message`
tables filtered by `client_id`); revenue-recovered figure once available.
Secondary: a simple "Frontdesk: Live" status card and the "Add another agent"
entry point into the catalog. No raw conversation transcripts by default —
outcomes, not activity logs, per the founder's explicit choice.

## Error handling

- Duplicate email at signup → inline field error.
- Wrong email/password at login → generic "invalid email or password" (no
  user enumeration).
- Spend cap exceeded mid-session → silent agent pause + dashboard banner,
  never a customer-facing error.

## Testing

- Password hash/verify round-trip.
- Signup → activation → dashboard end-to-end (Frontdesk only, no add-ons).
- Add-another-agent flow from the dashboard (second agent activates
  independently of the first).
- Spend-cap enforcement: agent does not fire once cap is reached.
- Dashboard query scoping: a logged-in client can only ever see their own
  `client_id`'s data.

## Explicitly out of scope (this spec)

- Upgrading past the trial cap / billing integration.
- Founder review/approval gate before an agent goes live (deliberately
  decided against — self-serve activation is immediate; see "Why" above for
  the trust/scale tradeoff this accepts).
- Auto-research pre-fill (scraping the shop's site/GBP) — still the
  separately-flagged next build from the original hire-flow work, not part
  of this signup redesign.
