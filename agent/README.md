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

Twilio (SMS) and xAI (voice) both need a stable public HTTPS URL — `ngrok` is fine
for local testing but the tunnel dies when the terminal closes, which won't work
for a real client's phone line. This gets Roster onto Railway, always-on, with the
database surviving redeploys.

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
   - `ADMIN_PASSWORD` — the founder-dashboard password. `/clients` (every shop's
     conversations and jobs) sits behind HTTP Basic auth with it; if unset, the
     dashboard fail-closes with a 503 instead of serving publicly. The landing
     page (`/`) and self-serve signup flow (`/signup`) stay public.
   - `SESSION_SECRET_KEY` — **required in production.** Signs the customer-portal
     session cookie (the `/signup → /dashboard` flow). It has an insecure dev
     fallback, so if you don't set it here every redeploy logs customers out and
     the cookie is forgeable. Generate one with
     `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
   - `FOUNDER_ALERT_PHONE` — optional. The number that gets the one-time SMS when
     a trial client crosses their spend cap (needs `TWILIO_*` set to actually
     send). Unset = no SMS, but the founder dashboard still shows a cap-reached
     badge on that client.
   - `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` — once you have a Twilio number (see
     below). Twilio is SMS-only now (Chaser, Rebooker, Renewals, Referrals, Reviews,
     Frontdesk text-back) — it no longer carries live voice.
   - `XAI_API_KEY`, `XAI_SIGNING_SECRET` — see "AI receptionist" below.
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
   `https://<something>.up.railway.app`, or attach a custom domain per below) —
   this is the URL Twilio's SMS webhooks point at, and the URL xAI's
   `realtime.call.incoming` webhook points at, below — replacing every
   `<your-public-url>` / `ngrok` reference in this README.

### Attaching your custom domain

Do this on the **web service** (not the cron service — that one has no public URL).

1. In Railway: web service → **Settings → Networking → Custom Domain**, enter your
   domain (e.g. `app.yourdomain.com` for a subdomain, or the apex `yourdomain.com`).
2. Railway shows you a **DNS target** to point at:
   - **Subdomain** (recommended, e.g. `app.` or `www.`): add a **CNAME** record at
     your registrar with that host, pointing at the `...railway.app` target Railway
     gives you.
   - **Apex/root** (`yourdomain.com` with no subdomain): most registrars can't
     CNAME the apex. Either use a registrar that supports **ALIAS/ANAME** at the
     root pointing at Railway's target, or use your registrar's forwarding to
     redirect the apex to the `www` subdomain and put the CNAME on `www`.
3. Wait for DNS to propagate (usually minutes, up to ~24h). Railway auto-issues the
   HTTPS certificate once it resolves — no cert work on your side.
4. **After the domain is live, update the two things that hardcode a URL:**
   - Twilio number → Messaging webhook → `https://<your-domain>/webhook/sms`
     (see "SMS agents" below).
   - The landing page's contact/CTA copy is domain-agnostic (links are relative
     `/signup`), so nothing to change there — but double-check any absolute URL if
     you add one later.

### Why SQLite, not Postgres

At the scale this is actually built for right now (3–5 pilot clients, a realistic
ceiling in the 50–150-business range once multi-worker concurrency is in place),
SQLite's single-writer model isn't the binding constraint. Revisit this only if
real concurrent-write pressure or managed backups/replicas become an actual need —
Railway hosts Postgres natively, so that migration is straightforward later.

## SMS agents (Twilio)
- Point a Twilio number's **inbound SMS webhook** at `POST /webhook/sms`. Replies go
  back as TwiML — no outbound credentials needed.
- Point the **call status callback** at `POST /webhook/voice-status` for the
  missed-call text-back (needs `TWILIO_*` creds, or prints to console in dev). This
  is Twilio's own call-status detection (a call rang unanswered), independent of
  who — if anyone — is answering it live; it's what triggers Frontdesk's SMS
  fallback and is unrelated to the live-voice provider below.
- Set each client's `inbound_number` to the business line so inbound SMS routes
  correctly. Chaser, Rebooker, Renewals, Referrals, and Reviews all ride on this
  same Twilio SMS channel.

## AI receptionist (xAI Grok Voice Agent API, live voice)

Live voice runs on xAI's Grok Voice Agent API (`xai_voice_adapter.py`), not Vapi —
Vapi has been removed. Architecturally different from the SMS agents: instead of a
per-turn HTTP request, xAI holds one WebSocket session open for the whole call and
we only speak on the wire when a tool (`log_job` / `transfer_call`) fires.

### One-time setup
1. Get an xAI API key (x.ai) and set `XAI_API_KEY` in `.env`.
2. Register each client's phone number with xAI:
   - **Reuse an existing Twilio number** (`origin: "byo_trunk"`) — in Twilio,
     create an **Elastic SIP Trunk** with origination URI
     `sip:{number}@sip.voice.x.ai;transport=tls`, and assign the client's Twilio
     number to that trunk. xAI still manages the routing endpoint even though the
     number itself stays with Twilio.
   - Or take one of xAI's own **Direct SIP numbers** directly, no Twilio number
     needed for voice at all.
3. Registration returns a **signing secret** — set it as `XAI_SIGNING_SECRET`.
4. Set the client's `xai_phone_number` field to the registered number (this is
   separate from `inbound_number`, which stays the Twilio number used for SMS).

### How a call flows
xAI sends a signed `realtime.call.incoming` webhook to
`POST /webhook/xai-incoming-call` with a `call_id`. We verify the signature,
look up the client by `xai_phone_number`, and spawn `xai_voice_adapter.run_call()`
as a background task — it opens `wss://api.x.ai/v1/realtime?call_id={call_id}`,
sends a `session.update` (voice, the same `build_voice_system_prompt` the old Vapi
path used, and the `log_job`/`transfer_call` tools), and streams for the life of
the call. Job/message persistence works the same as before.

### Unverified — needs a real test call
The exact webhook body field names (`call_id`, `to`, `from`) and the
`x-xai-signature` header are read from xAI's docs, not a confirmed live payload.
The first real call may 400/404 on a field-name mismatch — check the raw webhook
body if so and adjust `xai_incoming_call()` / `verify_webhook_signature()`
accordingly. Also unverified: whether `response.done` events actually carry a
`transcript` field in the shape `_extract_transcript()` expects — confirm once a
real call has run, since dashboard message history depends on it.

### Local testing (no deployment yet)
Needs `ANTHROPIC_API_KEY` in `.env` (the agent's reasoning) and `XAI_API_KEY` (the
voice layer). Because xAI reaches your server via a webhook (not a per-turn HTTP
call from a service you control), you need a public URL even for local testing:
```bash
./run.sh                 # starts the app on :8000
ngrok http 8000           # in a second terminal; gives you a public https URL
```
Use the `ngrok` URL + `/webhook/xai-incoming-call` when registering the number
with xAI. Call the number from your phone; confirm the job shows up in
`/clients/<id>` and that a deliberately hard question ("I want to speak to a
manager right now") triggers a live transfer to the `escalation_phone` on file.

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
