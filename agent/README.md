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

## Pieces
| File | Role |
|---|---|
| `engine.py` | The agent — Claude + `log_job`, bounded Think→Act→Observe loop |
| `service.py` | Shared turn logic (dashboard + webhook use the same path) |
| `app.py` | FastAPI: dashboard + `/webhook/sms` + `/webhook/voice-status` |
| `db_models.py` | `Client`, `Message` (threaded per customer), `Job` |
| `channels.py` | Outbound SMS (Twilio, or console fallback) |
| `seed.py` | Demo data so the dashboard isn't empty |
