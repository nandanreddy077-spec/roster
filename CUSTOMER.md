# CUSTOMER.md — the living record of what real customers tell us

**Purpose:** every objection, pilot, request, and complaint lands here. Features
get built when *this file* shows repeated demand — not on a guess. If it isn't
written here from a real customer, it isn't a validated need.

**Rule:** before building anything not on the current `ROADMAP.md` phase, check
this file. Repeated pain here > any internal idea.

---

## Current state (2026-07-14)
- **Paying customers:** 0
- **Active pilots:** 0
- **Product:** live at rosterhires.com (signup → onboarding → dashboard). Owner
  SMS in review (PR #2).
- **Revenue:** $0

### Deploy / ops state (updated 2026-07-21 from founder's Railway dashboard —
### previous entries below were stale, dated from before this was fixed)
- **Hosting:** Railway, service `roster`, root dir `agent/`, domain rosterhires.com.
- **Persistent volume attached and confirmed correct** (`roster-volume`,
  mount path `/data`, matching `ROSTER_DATA_DIR=/data` exactly — verified in
  the Railway dashboard, not assumed). The prior data-loss risk (redeploys
  wiping the SQLite DB) is fully resolved.
- **11 env vars set in Railway:** `ADMIN_PASSWORD`, `ANTHROPIC_API_KEY`,
  `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `OAUTH_REDIRECT_BASE_URL`,
  `ROSTER_DATA_DIR`, `ROSTER_ENV`, `SESSION_SECRET_KEY`, `TWILIO_ACCOUNT_SID`,
  `TWILIO_AUTH_TOKEN`, `XAI_API_KEY`. Founder admin (`/clients`) is reachable.
- **Twilio KYC cleared** (2026-07-21) — number provisioning
  (`buy_twilio_number`/`attach_number_to_xai_trunk`/`register_number_with_xai`)
  should now succeed instead of failing at the KYC wall. **Not yet proven**: no
  real inbound call has ever been captured (`data/call_captures/` is empty as
  of this writing) — Open Risk #1 below is still open until one real call is
  placed and logged.
- **Git:** no PR workflow in use this sprint — commits land directly on `main`,
  which Railway auto-deploys. Latest pushed: `674872c` (Roll back speculative
  employees; keep the Runner seam only).

### THE next action (not code)
Run the live Frontdesk test — sign up as a test business, confirm a real number
provisioned, then **call and text it**. Does it *answer*, *book*, and *notify the
owner*? That result closes or escalates Open Risk #1 and is the gate for all M2+
work (see `ROADMAP.md` build freeze).

## Open risks / blockers (ranked — from the internal persona review, to be
## replaced by *real* customer evidence as it comes in)

**#1 — The core voice loop is UNVERIFIED.** No real call has ever been proven to
answer end-to-end. Live voice is the primary wedge (see `SALES.md`), so this is
the single highest risk. **Closed when:** the first onboarding call confirms a
real inbound call is answered by the AI and books a job. Until then, we cannot
honestly sell "answers your calls live." *(This supersedes all feature work —
do not build M2+ as if the core works until this is closed.)*

**#2 — Activation cliff: call forwarding.** A non-technical owner forwarding
their business line is scary and currently unguided. Mitigated for the first 20
by the concierge onboarding call; a guided flow is Phase B / M4.

**#3 — Trust with zero proof.** No case studies, no reviews, brand new. Owner
SMS (M1) + "test it before you trust it" are the first trust artifacts; real
proof comes only from real pilots.

**#4 — The silo.** A booked job that only lives in a dashboard the owner never
opens. Owner SMS (M1) is the interim fix; real CRM/calendar sync is Phase D,
built only when a pilot names their tool.

---

## Onboarding-call log *(fill one per pilot — this is the research the 15-min calls are for)*

Template — extended 2026-07-21 to track the discovery-led onboarding
principle (`SALES.md` "Customer Onboarding Principle"): diagnosis before
deployment only works if the diagnosis and its outcome are actually logged.
```
### <Business name> — <trade>, <#> trucks — <date>
- How they reached us (request-access / cold call / referral) — where they got stuck
- Biggest bottleneck identified (discovery call)
- AI employee recommended — and why (which data/answer pointed to it)
- Call forwarding: how long, what confused them, did it work?
- First live call: answered? booked? what broke?
- Time to deployment (request → live)
- Time to first measurable value (live → first booked/recovered job)
- Manual steps YOU had to do (candidates to automate later)
- Hesitations / objections (verbatim if possible)
- Feature requests (verbatim)
- Would recommend? (Y/N + why)
- Next employee requested (if any) — this is the expansion signal
- Outcome: went live? paid? churned? why?
```

*(No pilots yet.)*

---

## Objections heard *(log verbatim; when one repeats, it becomes a `SALES.md` battlecard line)*
*(none yet)*

## Feature requests *(log verbatim; when the same ask repeats across pilots, it earns a `ROADMAP.md` slot)*
*(none yet)*

## Wins *(what made someone say yes / pay / renew — repeat these)*
*(none yet)*
