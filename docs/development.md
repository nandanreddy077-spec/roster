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
| `ROSTER_ENV` | No (set to `production` in prod) | Enforces fail-closed checks |
| `FOUNDER_ALERT_PHONE` | No | Trial spend-cap SMS alert destination |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | No (needed for outbound SMS) | Missed-call text-back, Recovery/Referral/Review sends |
| `XAI_API_KEY` | No (needed for live voice) | Registers numbers + opens the realtime voice WebSocket |
| `XAI_SIP_ALLOWED_ADDRESSES` | No | IP allowlist for xAI SIP registration |
| `XAI_SIGNING_SECRET` | No (needed for live voice) | Verifies the xAI incoming-call webhook signature |

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
