# Deployment

Full step-by-step Railway setup — persistent volume, custom domain,
Twilio/xAI webhook wiring — already lives in
[`../agent/README.md`](../agent/README.md#deploying-to-railway) and is kept
there rather than duplicated here, since it's tightly coupled to the exact
env vars and file paths in that same directory.

## Quick summary

- **Host:** Railway, one service, root directory `agent/`. Picks up
  `agent/railway.toml` automatically (build command, `--workers 1` start
  command).
- **Why one worker:** SQLite is single-writer and `locks.py`'s conversation
  lock is process-local — see [`architecture.md`](architecture.md#why-sqlite-and-one-worker).
- **One service, one volume — no separate cron service.** SQLite lives on a
  Railway persistent volume (`ROSTER_DATA_DIR`) attached to this single
  service. A second Railway service (e.g. a dedicated cron worker) can't
  mount that same volume, so it would have no access to the real database —
  this was the original plan and was reverted once that constraint was
  found (2026-07-30). Background work (Lead Qualifier, Dispatcher, Quote
  Chaser's auto-enrollment, Reviews, Referral) instead runs **in-process**:
  `app.py` starts an `asyncio` background task on boot (`scheduler.py`) that
  calls `recovery_tick.run()` on an interval — hourly by default,
  configurable via `TICK_INTERVAL_SECONDS` — inside the same running server,
  so it shares the same process and the same volume as the web app. Gated to
  production only (`ROSTER_ENV=production`); local dev and the test suite
  never start it. See [`architecture.md`](architecture.md) for how the tick
  pipeline itself works.
- **`PYTHONUNBUFFERED=1` must be set.** Python buffers `print()` output by
  default when stdout isn't a terminal, which on Railway means background
  work can run correctly but produce no visible logs for a long time — the
  scheduler's own tick output silently sat in a buffer until this was set.
- **Two external webhooks to (re)point after any domain change:** Twilio's
  SMS webhook → `/webhook/sms`, and xAI's incoming-call webhook →
  `/webhook/xai-incoming-call`.

For env var values, see [`development.md`](development.md#environment-variables)
and [`../agent/.env.example`](../agent/.env.example).
