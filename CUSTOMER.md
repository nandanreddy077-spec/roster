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

### Deploy / ops state (operational handoff — verify before relying on it)
- **Hosting:** Railway, service `roster`, root dir `agent/`, domain rosterhires.com.
- **Env vars SET in Railway:** `ANTHROPIC_API_KEY`, `TWILIO_ACCOUNT_SID`,
  `TWILIO_AUTH_TOKEN`, `XAI_API_KEY` (confirmed real), `GOOGLE_CLIENT_ID`,
  `GOOGLE_CLIENT_SECRET`, `OAUTH_REDIRECT_BASE_URL`, `SESSION_SECRET_KEY`.
- **⚠️ MISSING — data-loss risk:** `ROSTER_DATA_DIR=/data` is **not set**, and a
  persistent volume must be attached at `/data` (Railway → service → Settings →
  Volumes). Without both, **every redeploy wipes the SQLite DB** (all signups/
  jobs/conversations). `main` now auto-deploys the Phase-1 migration, so fix this
  *before* real customer data exists.
- **MISSING — minor:** `ADMIN_PASSWORD` (the founder `/clients` view 503s until
  set; public site + signup work regardless).
- **`ROSTER_ENV` not set** → the session fail-closed guard is inert (harmless;
  the app boots on `SESSION_SECRET_KEY`). Set `ROSTER_ENV=production` only after
  confirming `SESSION_SECRET_KEY` is set (it is).
- **Git:** PR #1 (foundation) merged to `main`. PR #2 (Owner SMS + operating
  docs) open, not merged.

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

Template:
```
### <Business name> — <trade>, <#> trucks — <date>
- Signed up self-serve? (Y/N, where they got stuck)
- Call forwarding: how long, what confused them, did it work?
- First live call: answered? booked? what broke?
- Manual steps YOU had to do (candidates to automate later)
- Hesitations / objections (verbatim if possible)
- Feature requests (verbatim)
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
