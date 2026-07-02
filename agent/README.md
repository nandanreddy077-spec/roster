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

### Security note (deferred — do before going public)
`/voice/chat/completions` currently has **no authentication**. Anyone who discovers
the ngrok/deployed URL can drive it directly, and every request triggers a paid
Claude call (and can trigger real `transfer_call` telephony actions). Before
exposing this endpoint publicly, add a shared-secret bearer token check (e.g.
validate an `Authorization: Bearer <secret>` header that Vapi is configured to
send as a Custom LLM header) so only Vapi can call it.

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
