# Security

Roster handles real customer data for paying and trial businesses —
conversation transcripts, phone numbers, and booking/job records — so
security reports are taken seriously even at this pre-revenue stage.

## Reporting a vulnerability

Email **nandanreddy1707@gmail.com** with a description and reproduction
steps. Please don't open a public GitHub issue for anything that could be
actively exploited (auth bypass, data exposure, injection, SSRF, secrets
handling). You should get an acknowledgment within a few days.

## Scope

- The deployed application (`agent/`) and its webhooks (`/webhook/sms`,
  `/webhook/voice-status`, `/webhook/xai-incoming-call`).
- The customer portal (`/signup`, `/login`, `/v2/dashboard*`) and the
  founder console (`/clients*`).

## Notes for anyone reviewing this codebase

- `ADMIN_PASSWORD` and, in production, `SESSION_SECRET_KEY` are required —
  the app fails closed (refuses to serve the relevant surface) rather than
  falling back to an insecure default. Don't relax this.
- `XAI_SIGNING_SECRET` verifies the xAI incoming-call webhook signature; the
  Twilio webhook has its own signature check (`test_twilio_signature.py`).
  Don't add a webhook route without an equivalent check.
- Secrets live in `.env` (git-ignored) locally and in the host's environment
  variables in production — never commit a real key. `agent/.env.example`
  documents every variable without values.
