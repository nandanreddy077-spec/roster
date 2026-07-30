# Deployment

Full step-by-step Railway setup — persistent volume, cron service, custom
domain, Twilio/xAI webhook wiring — already lives in
[`../agent/README.md`](../agent/README.md#deploying-to-railway) and is kept
there rather than duplicated here, since it's tightly coupled to the exact
env vars and file paths in that same directory.

## Quick summary

- **Host:** Railway, root directory `agent/`. Picks up `agent/railway.toml`
  automatically (build command, `--workers 1` start command).
- **Why one worker:** SQLite is single-writer and `locks.py`'s conversation
  lock is process-local — see [`architecture.md`](architecture.md#why-sqlite-and-one-worker).
- **Two services, one volume:** the web service (`app.py`) and a cron service
  running `recovery_tick.py` on a daily schedule both mount the *same*
  persistent volume at the *same* path, via `ROSTER_DATA_DIR`.
- **Two external webhooks to (re)point after any domain change:** Twilio's
  SMS webhook → `/webhook/sms`, and xAI's incoming-call webhook →
  `/webhook/xai-incoming-call`.

For env var values, see [`development.md`](development.md#environment-variables)
and [`../agent/.env.example`](../agent/.env.example).
