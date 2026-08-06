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
product. It supports persistent volumes (so SQLite doesn't get wiped every deploy),
without needing a Dockerfile.

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
   - `ROSTER_ENV=production` — enables every fail-closed check above, and turns on
     the in-process background scheduler (see step 5).
   - `PYTHONUNBUFFERED=1` — **needed for logs to show up at all.** Python
     block-buffers `print()` output when stdout isn't a terminal, which under
     Railway means background work (the scheduler, any tick output) can run
     correctly while producing no visible log lines for a long time. Without
     this, you will not see the scheduler's own output.
   - `TICK_INTERVAL_SECONDS` — optional, defaults to `3600` (hourly). How often
     the in-process scheduler below fires.
   - `DATABASE_URL` — optional. Unset means SQLite on the mounted volume (see
     step 3) — fine at current scale, see "Why SQLite, not Postgres" below. Set
     to a Postgres connection string to switch.
   - `PUBLIC_BASE_URL` — the public HTTPS URL Twilio/xAI webhooks are configured
     against (e.g. `https://yourdomain.com`). Falls back to a hardcoded default
     if unset — set this explicitly once you have your own domain (see
     "Attaching your custom domain" below), or webhook-dependent URLs can be
     built against the wrong host.
   - `FOUNDER_ALERT_PHONE` — optional. The number that gets the one-time SMS when
     a trial client crosses their spend cap (needs `TWILIO_*` set to actually
     send). Unset = no SMS, but the founder dashboard still shows a cap-reached
     badge on that client.
   - `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` — once you have a Twilio number (see
     below). Twilio is SMS-only now (Chaser, Rebooker, Renewals, Referrals, Reviews,
     Frontdesk text-back) — it no longer carries live voice.
   - `XAI_API_KEY` — see "AI receptionist" below. (The signing secret returned
     when you register a number is **not** an env var — see the note in that
     section.)
   - `XAI_SIP_ALLOWED_ADDRESSES` — optional, see "AI receptionist" below.
   - `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` — optional. Enables "Continue
     with Google" on the customer portal login; unset simply hides that button
     rather than erroring (`google_auth.py`).
   - `OAUTH_REDIRECT_BASE_URL` — only needed if Google OAuth is enabled above.
     Railway sits behind a proxy, so the request scheme can read as `http` even
     though the public URL is `https`; set this (e.g. `https://yourdomain.com`)
     to pin the OAuth redirect URI and avoid a `redirect_uri_mismatch`.
   - `ROSTER_DATA_DIR=/data` — points the SQLite file at the mounted volume instead
     of local disk, so it survives redeploys.
5. **Background work runs in-process — no second service.** Lead Qualifier,
   Dispatcher, Quote Chaser's auto-enrollment, Reviews, and Referral all run via
   `recovery_tick.run()`, called on a schedule by an `asyncio` background task
   the app itself starts on boot (`scheduler.py`), gated to
   `ROSTER_ENV=production` only. This was originally planned as a second
   Railway service running `recovery_tick.py` on a cron schedule, but Railway
   volumes attach to one service only — a second service would have no access
   to the real database. Running it in-process sidesteps that entirely: same
   process, same volume, no separate service to configure. See
   [`../docs/architecture.md`](../docs/architecture.md) for how the tick
   pipeline works.
6. **Copy the web service's public URL** (Railway assigns one automatically,
   `https://<something>.up.railway.app`, or attach a custom domain per below) —
   this is the URL Twilio's SMS webhooks point at, and the URL xAI's
   `realtime.call.incoming` webhook points at, below — replacing every
   `<your-public-url>` / `ngrok` reference in this README.

### Attaching your custom domain

Do this on the web service — there is only one service.

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

### A2P 10DLC (required before any real US customer)

US carriers filter automated ("application-to-person") SMS sent from an
unregistered long code. Filtering is silent: Twilio still accepts the message
and reports success, the handset never rings. Every SMS thing Roster does rides
this path — the missed-call text-back, Quote Chaser, Reviews, Referrals, and
the escalation alert that tells an owner a caller needs them **now**.

Registration is a Twilio Console + legal-entity task, not a code change. It
needs the operating company's legal name, EIN, and address, and it costs money,
so **only the founder can complete it**:

1. Twilio Console → **Messaging → Regulatory Compliance → A2P 10DLC**.
2. Register the **Brand** (legal entity + EIN). Sole proprietor works, with
   lower throughput.
3. Register a **Campaign**. Use case is *Mixed* or *Customer Care*; the sample
   messages must match what Roster actually sends — take real copy from
   `notifications.py` and `recovery_engine.py`, and include the STOP language,
   because a campaign whose samples don't match its traffic gets rejected.
4. Create a **Messaging Service**, add the campaign to it, and add every client
   number to its sender pool. New numbers bought via `/clients/{id}/provision-number`
   must be added to the pool too, or their traffic stays unregistered.
5. Set `TWILIO_MESSAGING_SERVICE_SID` (starts `MG`) in Railway and redeploy —
   variables only take effect on redeploy.

With that variable set, `channels.TwilioChannel` sends via the Messaging
Service and lets it choose the sender; without it, sends fall back to the bare
`from_` number and the app logs a warning at startup. Approval takes days to
weeks, so start it before a customer is waiting, not after.

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
3. Registration returns a **signing secret**. This is **not** an environment
   variable — it's per-business, stored on `Business.xai_signing_secret` and
   set via the founder console's registration form (`app.py`), because each
   client's number gets its own secret. `verify_webhook_signature()` reads it
   per-request from the matched `Business` row, not from `os.environ`.
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
3. Something needs to call `recovery_tick.run()` on a schedule to send due
   messages. **On Railway in production this already happens automatically**
   — see "Deploying to Railway" above, the in-process background scheduler
   covers this. For local testing, or any other host, run it manually or via
   a real crontab:
   ```bash
   0 9 * * * cd /path/to/agent && .venv/bin/python recovery_tick.py >> recovery.log 2>&1
   ```
   Safe to run more than once a day (or more than once an hour) — every step
   is idempotent, so nothing sends or processes twice.
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
