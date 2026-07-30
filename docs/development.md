# Local development

The whole app lives under [`../agent/`](../agent/) — that's the working
directory for everything below.

## Setup

```bash
cd agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in the values — see "Environment variables" below
```

## Run it

```bash
./run.sh          # creates/updates the venv, seeds a demo client, starts uvicorn --reload
```

Open **http://127.0.0.1:8000/clients** — HTTP Basic auth using the
`ADMIN_PASSWORD` you set in `.env`. You'll see a seeded demo business with a
sample conversation and the job it captured.

Try the conversation loop without Twilio/xAI at all: open a client in the
dashboard and type as the customer in the test-chat box, or run the terminal
simulator:

```bash
.venv/bin/python simulate.py clients/acme_hvac.json
```

Both need `ANTHROPIC_API_KEY` set.

## Environment variables

Every variable is documented inline in
[`../agent/.env.example`](../agent/.env.example) — copy it to `.env` and read
the comments there rather than a second copy of the same explanation here.
Summary:

| Variable | Required? | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Claude calls that power every conversation |
| `ADMIN_PASSWORD` | Yes | Founder `/clients` console (fails closed if unset) |
| `SESSION_SECRET_KEY` | Yes in production | Signs the customer-portal session cookie |
| `ROSTER_ENV` | No (set to `production` in prod) | Enforces fail-closed checks; also gates the in-process background scheduler on |
| `PYTHONUNBUFFERED` | Set to `1` in production | Without it, background `print()` output (the scheduler's own tick logs) can sit in a buffer instead of reaching the log stream |
| `TICK_INTERVAL_SECONDS` | No (default `3600`) | How often the in-process scheduler runs `recovery_tick.run()` |
| `DATABASE_URL` | No | Postgres connection string; unset falls back to SQLite on `ROSTER_DATA_DIR` |
| `ROSTER_DATA_DIR` | No (production sets it to the mounted volume) | Where the SQLite file lives; unset uses local disk |
| `PUBLIC_BASE_URL` | No (has a hardcoded fallback) | The public HTTPS host webhook-dependent URLs are built against |
| `FOUNDER_ALERT_PHONE` | No | Trial spend-cap SMS alert destination |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | No (needed for outbound SMS) | Missed-call text-back, Recovery/Referral/Review sends |
| `XAI_API_KEY` | No (needed for live voice) | Registers numbers + opens the realtime voice WebSocket |
| `XAI_SIP_ALLOWED_ADDRESSES` | No | IP allowlist for xAI SIP registration |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | No | Enables "Continue with Google" on the customer portal; unset just hides the button |
| `OAUTH_REDIRECT_BASE_URL` | No (only if Google OAuth is enabled) | Pins the OAuth redirect URI behind Railway's proxy |

Note: the xAI **signing secret** is *not* an environment variable, even
though it's easy to assume it is alongside `XAI_API_KEY` — it's per-business
(`Business.xai_signing_secret`), entered per client via the founder console
when you register that client's number. See
[`../agent/README.md`](../agent/README.md#ai-receptionist-xai-grok-voice-agent-api-live-voice).

## Project layout

```
agent/
  app.py, portal.py        entry points (see docs/api.md)
  engine.py, service.py    conversation core
  bookings.py, departments.py, employees.py, ...   business logic
  db_models.py, db.py      data layer (SQLModel)
  channels.py, provisioning.py, xai_voice_adapter.py   integrations
  recovery_*.py, referral_*.py, review_*.py   background job pairs
  templates/, static/, landing/   Jinja templates + assets
  tests/                   pytest suite (see docs/testing.md)
docs/                      this folder
archive/                   superseded material, kept for reference
```

See [`architecture.md`](architecture.md) for how these pieces fit together.
