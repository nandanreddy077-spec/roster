# Roster — AI front desk (agent)

Missed call → instant AI text-back → answers the customer → books the job.
Multi-tenant: each client business gets its own AI that sounds like them.

## Run it
```bash
cp .env.example .env        # then put your ANTHROPIC_API_KEY in .env
./run.sh                    # sets up venv, seeds a demo, starts the server
```
Open **http://127.0.0.1:8000/clients** — you'll see the seeded demo (Lou's Heating
& Cooling) with a sample missed-call conversation and the job it captured.

## Try it live
- In the dashboard, open a client and type as the customer in the chat box — the AI
  replies and logs jobs to the sidebar. (Needs `ANTHROPIC_API_KEY`.)
- Or the terminal simulator: `.venv/bin/python simulate.py clients/acme_hvac.json`

## Deploying to Railway

Twilio and Vapi both need a stable public HTTPS URL — `ngrok` is fine for local
testing but the tunnel dies when the terminal closes, which won't work for a real
client's phone line. This gets Roster onto Railway, always-on, with the database
surviving redeploys.

**Why Railway specifically:** unlike free-tier Render, it doesn't spin the app down
on idle — a missed-call agent that's asleep when the webhook fires defeats the
product. It supports persistent volumes (so SQLite doesn't get wiped every deploy)
and a native cron-schedule feature, without needing a Dockerfile.

### One-time setup (in Railway's dashboard — an agent can't click through this for you)

1. **Create a new Railway project**, connect it to this GitHub repo.
2. **Add the web service:**
   - Set its **Root Directory** to `agent/` — that's where `railway.toml`,
     `requirements.txt`, and `app.py` live.
   - Railway will pick up `agent/railway.toml` automatically (build command,
     multi-worker start command reading Railway's `$PORT`).
3. **Add a persistent volume** to the web service, mounted at e.g. `/data`.
4. **Set environment variables** on the web service:
   - `ANTHROPIC_API_KEY` — same key as local `.env`.
   - `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` — once you have a Twilio number (see
     below).
   - `VAPI_SHARED_SECRET` — same value as local `.env` (see "AI receptionist" below).
   - `ROSTER_DATA_DIR=/data` — points the SQLite file at the mounted volume instead
     of local disk, so it survives redeploys.
5. **Add a second service** (same repo, same root directory `agent/`) for the daily
   Recovery/Referrals tick:
   - Override its **Start Command** to `python recovery_tick.py`.
   - Set a **Cron Schedule** of `0 9 * * *` (matches the schedule already
     documented in `recovery_tick.py`'s own docstring).
   - Mount the **same persistent volume** at the **same path** (`/data`) as the web
     service, and set the same `ROSTER_DATA_DIR=/data` env var — this service must
     read/write the *same* database file the web service uses, not a separate one.
6. **Copy the web service's public URL** (Railway assigns one automatically,
   `https://<something>.up.railway.app`, or attach a custom domain) — this is the
   URL Twilio's webhooks and Vapi's Custom LLM connection both point at below,
   replacing every `<your-public-url>` / `ngrok` reference in this README.

### Why SQLite, not Postgres

At the scale this is actually built for right now (3–5 pilot clients, a realistic
ceiling in the 50–150-business range once multi-worker concurrency is in place),
SQLite's single-writer model isn't the binding constraint. Revisit this only if
real concurrent-write pressure or managed backups/replicas become an actual need —
Railway hosts Postgres natively, so that migration is straightforward later.

## Real phone line (Twilio)
- Point a Twilio number's **inbound SMS webhook** at `POST /webhook/sms`. Replies go
  back as TwiML — no outbound credentials needed.
- Point the **call status callback** at `POST /webhook/voice-status` for the
  missed-call text-back (needs `TWILIO_*` creds, or prints to console in dev).
- Set each client's `inbound_number` to the business line so inbound routes correctly.

## AI receptionist (Vapi, live voice)

The voice receptionist reuses the same engine as the text agent — see
`docs/superpowers/specs/2026-07-01-ai-receptionist-design.md` for the full design.

### One-time setup
1. Create a Vapi account and import each client's Twilio number
   (docs.vapi.ai/phone-numbers/import-twilio).
2. Create a single Vapi assistant (shared across all clients) with:
   - Model provider: Custom LLM
   - Custom LLM URL: `<your-public-url>/voice/chat/completions`
   - A custom header: `Authorization: Bearer <VAPI_SHARED_SECRET value>` — required
     once you've set `VAPI_SHARED_SECRET`; the endpoint accepts any request until
     you do, so this step is what actually locks it down.
   - A `transferCall` tool with an empty `destinations` list — the destination is
     supplied dynamically by the agent per call, not configured here.
3. Point every imported client number at this one assistant.

### Local testing (no deployment yet)
Needs `ANTHROPIC_API_KEY` set in `.env` — same as the SMS agent, `/voice/chat/completions`
calls the live Claude API, so nothing will respond without it.

Vapi needs a public URL to reach your local server:
```bash
./run.sh                 # starts the app on :8000
ngrok http 8000           # in a second terminal; gives you a public https URL
```
Use the `ngrok` URL (plus `/voice/chat/completions`) as the assistant's Custom LLM
URL while testing. Call the imported Twilio number from your phone to test live;
confirm the job shows up in `/clients/<id>` and that a deliberately hard question
("I want to speak to a manager right now") triggers a live transfer to the
`escalation_phone` on file.

### Authentication
`/voice/chat/completions` requires a shared-secret bearer token once
`VAPI_SHARED_SECRET` is set in `.env` — a request with a missing or wrong
`Authorization: Bearer <secret>` header gets `401` immediately, before any Claude
call or DB lookup. **Until `VAPI_SHARED_SECRET` is set, the endpoint accepts any
request** — this is the one operator action that actually closes the gap; do it
before pointing a real client's number at this in production.

### Per-client onboarding
When adding a client for this agent: ask whether the AI should answer every call
or only unanswered ones, set `answer_mode` accordingly (`primary`/`backup`) on the
new-client form, and have them set matching call forwarding on their existing
number (forward-all vs. forward-on-no-answer) to the Twilio number you imported
into Vapi.

## Revenue Recovery (quote follow-up + reactivation)

A standalone agent — independent of Frontdesk — that chases unsold estimates
("quote" campaigns) and lapsed customers ("reactivation" campaigns) via a
6-touch SMS sequence over ~4 weeks, and books the job itself when the customer
says yes.

### Running it
1. Open a client's dashboard page, click **+ New campaign** under "Revenue
   Recovery campaigns".
2. Pick a type (quote follow-up or reactivation), name the campaign, and paste
   customers one per line: `phone,name,service_type,amount_or_days_since`.
3. Set up a daily cron job to send due messages:
   ```bash
   0 9 * * * cd /path/to/agent && .venv/bin/python recovery_tick.py >> recovery.log 2>&1
   ```
   Safe to run more than once a day — each sequence day's message is only ever sent once.
4. When a customer replies, `/webhook/sms` checks for an active Recovery
   conversation before falling through to Frontdesk, so replies get routed
   correctly even on a shared inbound number.

### Scope (Phase 1)
- Only a manual/business-hours time-slot proposal is wired up — no live Google
  Calendar/Jobber/Housecall Pro sync yet (see `calendar_provider.py`). Add one
  when a real client names the system they use.
- No CRM auto-import — campaigns are seeded from a pasted customer list.

## Named crew and the third face (Renewals)

Revenue Recovery's two faces are sold under separate names — **Chaser** (`face ==
"quote"`) and **Rebooker** (`face == "reactivation"`) — via `FACE_DISPLAY_NAMES` in
`recovery_engine.py`. This is a display-layer mapping only; the underlying `face`
column, engine, and code all still say "quote"/"reactivation" internally.

A third face, **Renewals** (`face == "membership"`), chases membership/maintenance-plan
renewals on each customer's own renewal date instead of days since the campaign
started. Paste customers as `phone,name,service_type,renewal_date` (strict
`YYYY-MM-DD` — rejected with a clear error otherwise) and the sequence fires at
30/14/7 days before the renewal, on the day itself, and 7 days after if there's been
no reply. Everything else — replies, slot booking, STOP handling — is identical to
Chaser/Rebooker; only the timing clock differs (see `MEMBERSHIP_OFFSETS` in
`recovery_engine.py`).

## Reviews (feature, not an agent)

Set a client's review link via the "Reviews" tile on their dashboard page. Once set,
clicking **Mark done** on any captured job texts that customer a one-line review
request. No sequence, no Claude — deliberately the smallest possible implementation,
since review requests are already a commodity feature on every competing platform.

## Pieces
| File | Role |
|---|---|
| `engine.py` | The agent — Claude + `log_job`, bounded Think→Act→Observe loop |
| `service.py` | Shared turn logic (dashboard + webhook use the same path) |
| `app.py` | FastAPI: dashboard + `/webhook/sms` + `/webhook/voice-status` |
| `db_models.py` | `Client`, `Message` (threaded per customer), `Job` |
| `channels.py` | Outbound SMS (Twilio, or console fallback) |
| `seed.py` | Demo data so the dashboard isn't empty |
| `recovery_engine.py` | Recovery's templates, tool schemas, and system prompts |
| `recovery_service.py` | Campaign creation, daily tick, reply handling, booking |
| `recovery_tick.py` | Cron entry point — sends due sequence messages |
| `calendar_provider.py` | Pluggable time-slot source (manual fallback today) |
