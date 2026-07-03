# Railway Deployment — Design Spec

**Date:** 2026-07-03
**Status:** Design approved, ready for implementation plan

---

## Executive Summary

Roster has only ever run locally (`./run.sh`). This gets it onto a stable, public,
always-on URL so Twilio and Vapi have somewhere real to point their webhooks —
the hard dependency blocking both of those setups. Two Railway services in one
project, sharing one persistent volume: a **web service** (the FastAPI app, always
on) and a **cron service** (`recovery_tick.py`, once daily). Database stays SQLite,
now on a Railway volume instead of local disk, so it survives redeploys.

---

## Problem & Opportunity

- Twilio's inbound-SMS webhook and Vapi's Custom LLM URL both need a stable HTTPS
  endpoint — `ngrok` (today's only option) is fine for testing but the tunnel dies
  when the local terminal closes, which is unacceptable for a real client's phone
  line.
- A missed-call agent that's asleep when the webhook fires defeats the product —
  rules out any hosting tier that spins down on idle.
- Concurrency research done alongside this design surfaced a real, separate issue:
  `AgentEngine` uses the synchronous Anthropic client from inside `async def` routes,
  which blocks the whole event loop per Claude call on a single worker process. This
  is fixed by running multiple Uvicorn workers — a one-line start-command change,
  folded into this same deployment work rather than deferred.

---

## Design

### Hosting: Railway

Chosen over Render (whose free tier sleeps — a real risk for webhook reliability;
their always-on tier costs about the same as Railway anyway) and over Fly.io/a raw
VPS (more ops overhead than a solo operator needs for a plain FastAPI app). Railway
gives git-push deploys, persistent volumes, and a native cron-schedule feature
without a Dockerfile.

### Two services, one shared volume

- **Web service** — root directory `agent/`, runs the FastAPI app via multiple
  Uvicorn workers so concurrent Claude calls don't queue behind each other.
- **Cron service** — same repo/root, start command overridden to
  `python recovery_tick.py`, Railway's cron schedule set to `0 9 * * *` (matches
  the schedule already documented in `recovery_tick.py`'s own docstring).
- **Shared volume**, mounted to both services at the same path — both need to
  read/write the *same* SQLite file, or the cron job's sends and the dashboard's
  view of the data would diverge onto two separate databases.

### Database: SQLite stays, path becomes configurable

`db.py` currently hardcodes the database file to `agent/data/roster.db`. This adds
one env var, `ROSTER_DATA_DIR`, that overrides where that file lives — defaulting
to today's exact local-dev path when unset, so no local behavior changes. In
Railway, this env var points at the mounted volume's path.

No migration to Postgres. At the actual target scale (3–5 pilot clients now, a
realistic ceiling in the 50–150-business range once the multi-worker fix lands),
SQLite's single-writer model isn't the binding constraint — see the concurrency
research above. Revisit only if real concurrent-write pressure or managed
backups/replicas become an actual need, not a hypothetical one.

### Secrets

`ANTHROPIC_API_KEY`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `VAPI_SHARED_SECRET`,
and the new `ROSTER_DATA_DIR` all move into Railway's environment variables
dashboard for the deployed services. `.env` remains local-dev-only, unchanged.

### What Claude can build vs. what requires the operator

This spec's code/config pieces (a `railway.toml`, the `db.py` path change, README
documentation) are things Claude writes directly. Creating the actual Railway
project, connecting the GitHub repo, setting the root directory, adding the volume,
setting environment variables, and configuring the cron service's start command and
schedule all happen in Railway's own dashboard — account/billing actions outside
what an agent can do on the operator's behalf. The README section this produces is
the exact runbook for those steps.

---

## Constraints & Scope

### In Scope
- `agent/railway.toml` defining the web service's build/start command
  (multi-worker Uvicorn, reading Railway's `$PORT`).
- `db.py`'s database path made configurable via `ROSTER_DATA_DIR`, defaulting to
  unchanged local behavior.
- A README section documenting the full Railway setup runbook (dashboard steps +
  env vars + volume + cron schedule).

### Out of Scope
- Migrating to Postgres — not warranted at this scale (see Design).
- Automating the Railway project/service creation itself — no CLI session is
  authenticated to the user's account in this environment; this is a manual,
  one-time runbook instead.
- Twilio and Vapi account setup — blocked on this deploy existing, handled as the
  next piece of work once this is live.

---

## Testing

- `db.py`: a test confirming `ROSTER_DATA_DIR`, when set, changes where the
  database file is created; unset, behavior is byte-identical to today.
- No other application logic changes in this spec — the full existing test suite
  must stay green throughout.

---

**Design approved by:** User
**Ready for:** Implementation planning
