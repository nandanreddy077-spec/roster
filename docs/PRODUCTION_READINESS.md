# Roster — Production Readiness

_Audit + operating procedures. Started 2026-09-03._

Read this alongside [`runbook.md`](runbook.md) (incident response) and
[`deployment.md`](deployment.md) (hosting). This file is the checklist for
"safe for real paying customers" and the record of what is and isn't done.

---

## 0. Current production state (verified 2026-09-03)

| Thing | State |
|---|---|
| `rosterhires.com` | live, `main` deployed (commit `20c7dd9`) |
| Database | **SQLite on a Railway volume** (`/data`, ~0.1 GB). `DATABASE_URL` not set. |
| Workers | `--workers 1` (required while on SQLite) |
| `ROSTER_ENV` | `production` — fail-closed secrets, Twilio signature enforcement, and the scheduler are all engaged |
| Voice (xAI over Twilio SIP) | working — 2 real calls verified |
| SMS / A2P 10DLC | **central campaign REJECTED.** Per-customer ISV registration is the model. Sends are carrier-filtered. Not usable. |
| `TWILIO_MESSAGING_SERVICE_SID` | set (`MG45f16…b604`) — but points at a messaging service whose campaign is rejected |
| `SENTRY_DSN` | not set — no error monitoring |
| `FOUNDER_ALERT_PHONE` | not set — founder gets no SMS on a trial-cap event |
| Backups | `backup.py` runs each tick, verified, **stored on the same volume as the DB** |
| Test suite | 1226 passing, ruff clean |
| Self-serve signup | closed (`portal.py`); `SELF_SERVE_SIGNUP` flag not built yet |

---

## 1. Audit findings

Scope note: onboarding is founder-driven today (`/clients/*`, HTTP-Basic), 0
paying customers, self-serve not built. That lowers the urgency of anything
that only bites once a customer can reach a route or once retention agents run
at volume — but the brief asks for the gates to exist *before* self-serve
opens, so several "self-serve" items are P1 not P2.

### P0 — fix before the first real paying customer

| ID | Finding | Where | Fix |
|---|---|---|---|
| **P0-1** | **Concurrent double-buy of Twilio numbers.** `provision_number` checks `if client.twilio_number_sid` then calls `buy_twilio_number()`. Two in-flight requests (double-click; FastAPI runs sync handlers in a threadpool even at `--workers 1`) both read `None`, both buy. **FIXED (commit `3122a57`):** `provisioning.claim_provisioning()` — atomic conditional UPDATE before the purchase; failed purchase releases the claim; a 10-min-stale claim with no SID is re-claimable (crash recovery). New column `Business.provisioning_started_at`. | done | |
| **P0-2** | **Orphan number on a crash between purchase and DB commit.** `buy_twilio_number()` returns → process dies → `twilio_number_sid` never persisted. Roster pays for a number no row points at. **MITIGATED (commit `<p0-1>` + docs):** the P0-1 claim makes it reconcilable — a `provisioning_started_at` set with no SID for >10 min is the signal. Documented manual reconcile in §7 (check the Twilio console for an unattached number bought around that timestamp; attach or release it). Not auto-reconcile — disproportionate for 10-20 customers. | done (mitigation) | |
| **P0-3** | **Customer data is not durable.** SQLite on the Railway volume; volume loss = data loss, and `backup.py` writes to the same volume. **CODE READY:** `migrate_to_postgres.py` reviewed (schema-drift-safe by construction — copies model columns only, FK order, resets identity sequences); `tests/test_migrate_to_postgres.py` added (runs against real Postgres via `ROSTER_TEST_DATABASE_URL`, skips otherwise); cutover documented step-by-step in §5. **HUMAN ACTION: provision Railway Postgres, run the cutover.** | code ready · **human action** | |

### P1 — before self-serve opens / before customer count grows

| ID | Finding | Where | Fix |
|---|---|---|---|
| **P1-1** | **No backend provisioning precondition.** `provision_number` would buy for any business id. Founder-only today (HTTP-Basic), so no customer can reach it — but the self-serve wizard will call the same path. **FIXED (commit `<p1-1>`):** `Business.payment_method_verified_at` + `provisioning_unlocked_at`; `provisioning.provisioning_allowed()`; `provision_number` refuses without one; `POST /clients/{id}/unlock-provisioning` is the founder override; console button added. `activate_frontdesk`'s own buy is unchanged (no live caller — gated as part of the self-serve wizard split). | done | |
| **P1-2** | **One business's failure aborts the whole scheduler tick.** `runner.dispatch_tick` looped businesses with no per-business `try`; `enroll_completed_estimates` looped jobs with no per-job `try`; `recovery_tick.run` chained all phases in one `try`. A single bad row skipped every later business *and* every later tick phase. **FIXED (commit `<p1-2>`):** per-iteration `try/except` (log + rollback + continue) in `dispatch_tick` and `enroll_completed_estimates`; `recovery_tick.run` now runs each phase in its own session/try and records which phases failed on the heartbeat (`/health` sees a partial tick), without re-raising. | done | |
| **P1-3** | **`FOUNDER_ALERT_PHONE` unset.** A paying customer hits the $20 trial cap → employees go silent → `trial_cap._notify_founder_cap_reached` no-ops. Owner is told; founder is not. | env | **HUMAN ACTION: `railway variables set FOUNDER_ALERT_PHONE=+1…` then redeploy.** No code change needed — `trial_cap` already reads it. |
| **P1-4** | **No error monitoring.** `SENTRY_DSN` unset → `configure_sentry` no-ops. A provisioning failure, a tick-phase crash (now recorded on the heartbeat by P1-2), an unhandled 500 — visible only by grepping Railway logs. | env | Code ready (`sentry_config.py`, `sentry-sdk` installed, `LoggingIntegration` captures `logger.error`/`logger.exception` from boot). **HUMAN ACTION: create a Sentry project, `railway variables set SENTRY_DSN=…`, redeploy.** |
| **P1-5** | **SMS "configured" is not distinguished from "deliverable".** The app behaved as if SMS worked whenever a messaging service SID was set, but a message from a number whose A2P campaign isn't approved is carrier-filtered silently. **FIXED (commit `<p1-5>`):** `Business.sms_delivery_status` (`not_configured` / `pending_campaign` / `active`); `channels.sms_deliverable()` holds proactive sends **only** in `pending_campaign` (missed-call text-back, Quote Chaser, Reviews, Referral, Membership — checked before each claim so a held send goes out later, not lost); `provision_number` sets `pending_campaign`; `POST /clients/{id}/sms-delivery-status` is the founder control (a future automated A2P-status callback replaces it); console shows the state. Voice and owner alerts are unaffected. **Twilio message status-callback ingestion is P2** — this is the campaign-level gate, not per-message delivery receipts. | done | |
| **P1-6** | **No rate limiting on `/login`** (brute force) **or `/request-access`** (public form spam). **FIXED (commit `<p1-6>`):** `agent/ratelimit.py` — a process-local sliding window (no dependency); `/login` throttles 10/5min per IP and per account (429), `/request-access` 5/hr per IP (silently drops, looks like success). `ponytail:` ceiling noted — move the counter to Postgres/Redis when workers > 1. `/signup` gets the same when self-serve opens. | done | |
| **P1-7** | **Backups share the DB's failure domain.** Folded into P0-3 — the Postgres move gives managed daily backups + PITR. **Documented in §6:** until then a volume loss is unrecoverable; interim mitigation is periodically pulling `/data/backups/<newest>.db` off-platform. | doc + P0-3 | |

### P2 — after initial customer validation

| ID | Finding | Fix |
|---|---|---|
| **P2-1** | `seed.py` has no production guard. Harmless unless a human runs it against prod, but a `ROSTER_ENV == production` refusal is cheap. | one guard |
| **P2-2** | No CSRF tokens on portal forms. `SameSite=Lax` on the session cookie is the modern baseline and covers cross-site POST; explicit tokens are defence-in-depth. | later |
| **P2-3** | Cross-business isolation reads correctly everywhere checked (`_booking_target`, `action_department_interest`, `_is_owner`, `_current_client`, all `/v2/dashboard` routes) but has no dedicated adversarial test file. | `test_cross_business_isolation.py` |
| **P2-4** | `/health` is one endpoint, not split liveness/readiness. Fine for Railway's single `healthcheckPath`; revisit if a load balancer needs separate probes. | later |
| **P2-5** | Restore has never been run end to end. | §6 procedure + one drill |

### Not findings — verified sound

- **Webhook authentication**: `/webhook/sms` and `/webhook/voice-status` enforce the Twilio signature in production (fail closed on a missing token/header); `/webhook/xai-incoming-call` verifies the Svix signature + a 5-minute timestamp window + per-`call_id` dedup.
- **Webhook idempotency**: `WebhookDelivery` claims every delivery by unique `dedup_key`; SMS retries replay cached TwiML, voice-status and xai-call retries are one-time.
- **Auth**: bcrypt (72-byte safe, never raises), `SESSION_SECRET_KEY` fails closed in production, access links are salted + time-limited and can't be replayed as session cookies, founder Basic auth uses `secrets.compare_digest` and 503s if `ADMIN_PASSWORD` is unset.
- **Deployment employee writes**: `deployment.deploy_role` is idempotent by a unique index + `IntegrityError` handling.
- **No** TODO/FIXME/HACK, **no** bare `except:`, **no** hardcoded secrets, **no** hardcoded phone numbers outside `seed.py` (dev-only) and doc comments. FastAPI `docs_url`/`openapi_url` disabled.
- **`ORIGIN_ESCALATION`** filtering is consistent across every count and recall path.
- **Broad `except Exception`** blocks are the documented best-effort-boundary pattern (a failed owner alert must not break a customer's turn) and record the failure rather than masking it — **except** the tick-isolation gap in P1-2.

---

## 2. Environment variables

| Variable | Required | Purpose | Behavior if missing |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | **yes** | the agent engine | no replies at all (warns at boot) |
| `SESSION_SECRET_KEY` | **yes (prod)** | signs portal cookies + access links | **refuses to start** in production |
| `ADMIN_PASSWORD` | **yes** | founder console HTTP Basic | console returns 503 |
| `ROSTER_ENV` | **yes** | `production` gates fail-closed behavior + the scheduler | dev mode: insecure fallbacks, no scheduler |
| `PUBLIC_BASE_URL` | **yes (prod)** | webhook registration + Twilio signature base | webhooks 403 / register against wrong host |
| `DATABASE_URL` | **should** | Postgres | falls back to SQLite on the volume (not durable) |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | **yes for SMS/provisioning** | Twilio API | SMS prints to console; provisioning fails; webhooks can't verify |
| `TWILIO_MESSAGING_SERVICE_SID` | for A2P sending | registered campaign sender pool | sends from a bare long code (filtered) |
| `XAI_API_KEY` | **yes for voice** | Grok Voice + number registration | live calls die at connect |
| `XAI_VOICE` | no | voice name (default `eve`) | default |
| `XAI_SIP_ALLOWED_ADDRESSES` | no | SIP IP allowlist | omitted from registration |
| `SENTRY_DSN` | **should** | error monitoring | no-op, logs one line at boot |
| `FOUNDER_ALERT_PHONE` | **should** | founder SMS on trial-cap events | no founder alert |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | no | owner Google sign-in | button hidden, routes redirect to `/login` |
| `OAUTH_REDIRECT_BASE_URL` | if Google enabled | proxy-safe redirect URI | derived from request (can mismatch behind proxy) |
| `TICK_INTERVAL_SECONDS` | no | scheduler cadence (default 3600) | default |
| `SELF_SERVE_SIGNUP` | no | opens `/signup` (not built yet) | closed |

Currently missing in production: `DATABASE_URL`, `SENTRY_DSN`, `FOUNDER_ALERT_PHONE`.

---

## 3. Deployment procedure

Railway auto-deploys `main` (root directory `agent/`, NIXPACKS,
`pip install -r requirements.txt`, `uvicorn app:app --workers 1`).
`healthcheckPath = /health`; `restartPolicyType = ON_FAILURE`, max 3 retries.

1. Merge the PR to `main` (or push). Railway starts a build.
2. Watch it: `railway deployment list` (or the Railway dashboard). Build +
   deploy is ~2-3 min.
3. Migrations run automatically at import (`init_db()` in `app.py` — the DDL
   `ALTER TABLE … ADD COLUMN` statements in `db._migrate_add_columns` are
   idempotent and serialized by a cross-process lock).
4. Verify (see §16 smoke test): `curl -s https://rosterhires.com/health` →
   `status: ok`; `curl -s -o /dev/null -w '%{http_code}' https://rosterhires.com/`
   → 200.
5. If `/health` returns 503, the database is unreachable — check the Railway
   deploy logs and the volume mount.

**Env var changes need a redeploy to take effect** (`railway variables set …`
then trigger a deploy — `railway up` or a dashboard redeploy).

## 4. Rollback procedure

The application has no destructive migrations — every schema change is an
additive `ADD COLUMN`, so an older image runs against a newer database
(it just ignores the extra columns). Rollback is therefore safe:

1. **Railway dashboard → the service → Deployments →** pick the last known-good
   deployment **→ Redeploy.** This re-runs that image; no data change.
2. Or `git revert <bad commit>` on `main` and let the auto-deploy roll forward
   to the reverted state (preferred — keeps history honest).
3. Data written by the bad version stays (additive schema, no drops). If a bad
   version wrote *wrong* data, that's a data-repair task, not a rollback —
   restore from a backup snapshot (§6) into a scratch DB, diff, fix forward.

There is no blue/green or canary. For 10-20 customers a ~30 s restart window
on redeploy is acceptable; revisit if it isn't.

## 5. Database migration procedure (SQLite → Postgres)

**Not done in production. `DATABASE_URL` is unset → SQLite on the volume.**
This is P0-3 (customer data is not durable). The migration tool
(`migrate_to_postgres.py`) is built and its data-copy + verification is
covered by `tests/test_migrate_to_postgres.py` (which runs against a real
Postgres when `ROSTER_TEST_DATABASE_URL` is set, skips otherwise).

**Cutover (human action, ~1 hour, one brief restart):**

1. **Provision Postgres on Railway** (add a Postgres service to the project),
   copy its connection string.
2. **Take a fresh backup first**, always (§6).
3. **Pull the live SQLite DB:**
   ```bash
   railway ssh -- base64 /data/roster.db > prod.b64
   base64 -d -i prod.b64 -o prod-roster.db
   ```
4. **Migrate into a SCRATCH Postgres first** (never straight at the one Railway
   will use):
   ```bash
   DATABASE_URL=postgresql://…scratch… agent/.venv/bin/python agent/migrate_to_postgres.py prod-roster.db
   ```
   It refuses a non-empty target, copies every table in FK order, resets every
   identity sequence past the copied ids, and verifies row counts + sequences
   before returning. `MigrationError` = do not cut over.
5. **Run the suite against that scratch Postgres:**
   ```bash
   ROSTER_TEST_DATABASE_URL=postgresql://…scratch… agent/.venv/bin/pytest -q
   ```
   All green (including the 5 `test_migrate_to_postgres.py` cases that were
   skipping) = the app works on Postgres.
6. **Migrate into the real Railway Postgres** (repeat step 4 with its URL,
   against a fresh pull).
7. **Set `DATABASE_URL`** in Railway → **redeploy.** The app now uses Postgres;
   `--workers 1` can be raised in `railway.toml` (Postgres handles concurrent
   writers and `locks.py` becomes a real cross-process `pg_advisory_lock`).
8. Verify `/health` → `database.ok: true`; confirm a booking/login works.
9. Keep the SQLite volume for a few days as a fallback, then remove it.

## 6. Backup & restore

**Backups (`backup.py`, runs every scheduler tick):** SQLite `.backup()` API
(not `cp` — WAL-safe), immediately reopened + integrity-checked + row-count
matched against the source. A snapshot that fails verification is **deleted**
("a backup nobody restored is not a backup"). Kept in
`$ROSTER_DATA_DIR/backups/` (i.e. `/data/backups/`), last ~14 retained.
`/health` reports the newest snapshot's age.

**KNOWN GAP (P1-7):** backups are on the same Railway volume as the database.
A volume loss loses both. The Postgres cutover (§5) closes this — Railway
Postgres has managed daily backups + point-in-time recovery. **Until then, a
volume loss is unrecoverable.** Interim mitigation: `railway ssh -- base64
/data/backups/<newest>.db` periodically and keep a copy off-platform.

**Restore (SQLite, current):**
1. Stop taking writes (scale the service to 0, or accept the last few minutes
   are lost).
2. `railway ssh` in; `cp /data/backups/<chosen>.db /data/roster.db`.
3. Restart the service. `init_db()` re-runs the idempotent migrations.
4. Verify `/health` and spot-check recent bookings.

**Restore has never been drilled (P2-5).** Do one dry run against a scratch
copy before relying on it.

**What data can be lost:** anything written since the last snapshot (≤ one
tick interval, default 1 h) plus anything in the WAL not yet checkpointed.
Acceptable for the pilot; the Postgres move with PITR reduces it to seconds.

## 7. Twilio provisioning lifecycle & recovery

**States** (read from columns, not one enum — auditable by construction):

| Signal | Meaning |
|---|---|
| `twilio_number_sid` unset | no number bought |
| `provisioning_started_at` set, `twilio_number_sid` unset | a purchase is **in progress** (or, if > 10 min old, **abandoned** — a process died mid-purchase) |
| `twilio_number_sid` set, `xai_signing_secret` unset | number bought, SMS-ready, voice not registered — retry via `retry-xai-registration` |
| `twilio_number_sid` + `xai_signing_secret` set, `voice_provisioning_error` unset | fully wired |
| `voice_provisioning_error` set | last voice attempt failed (retry safe) |
| `sms_delivery_status` | `not_configured` → `pending_campaign` (on purchase) → `active` (founder marks it when the A2P campaign clears) |

**Preconditions (both enforced in `provision_number`, backend):**
- `provisioning_allowed()` — `payment_method_verified_at` OR
  `provisioning_unlocked_at` (founder override, `POST /clients/{id}/unlock-provisioning`).
- `claim_provisioning()` — atomic claim so two concurrent requests can't both buy.

**Recovery — a customer's provisioning looks stuck:**
1. Check `/clients/{id}` — the console shows the state and any error.
2. **`provisioning_started_at` set, no `twilio_number_sid`, > 10 min old:** a
   process died mid-purchase. The claim is now re-claimable. **Before
   re-clicking "Buy", check for an orphan number:** Twilio console → Phone
   Numbers → Active Numbers → look for a number bought around that timestamp
   that isn't attached to any business. If found: either attach it manually
   (set `inbound_number` + `twilio_number_sid` on the row via `railway ssh`
   `python`) or **release it** in the Twilio console (stops the ~$1/mo charge).
   Then the "Buy" button is safe to use.
3. **`twilio_number_sid` set, voice failing:** click "Retry xAI voice
   registration" (buys nothing). If it 409s ("already registered … secret
   unrecoverable"), that number is bricked for voice — provision a different
   one (see `provisioning.provision_voice`'s docstring).

## 8. Monitoring

- **`/health`** (Railway healthcheck + a human mid-incident): `database`
  (hard, 503), `scheduler` heartbeat / `backups` age / `owner_alerts`
  undelivered count (soft, 200 with detail).
- **Structured JSON logs** on stdout, captured by Railway. `logger.exception`
  / `logger.error` for every handled failure boundary. Search the Railway log
  viewer or `railway logs`.
- **Sentry** — code ready (`sentry_config.py`, `sentry-sdk` installed),
  **`SENTRY_DSN` not set** (P1-4). Set it to capture unhandled exceptions +
  the tick-phase failures now recorded on the heartbeat. `LoggingIntegration`
  attaches after `configure_logging()` so `logger.error` from boot onward is
  captured.
- **Trial-cap events** — the owner is texted; the founder is texted **only if
  `FOUNDER_ALERT_PHONE` is set** (P1-3, currently unset).

## 9. Common failures & incident response

| Symptom | Likely cause | Action |
|---|---|---|
| `/health` 503 | DB unreachable (volume unmounted, disk full, Postgres down) | Railway deploy logs; check the volume; restart |
| `/health` `scheduler.ok: false`, stale heartbeat | tick crashed or hung; `last_tick_error` names the phase (P1-2) | read logs for that phase; a bad row is now skipped automatically, so a *persistent* failure is a code bug |
| Owner reports "no text when a job was booked" | A2P campaign not `active` for that business, OR the owner's number is wrong | check `sms_delivery_status`; check `escalation_phone` is E.164; `/health` `owner_alerts.undelivered` |
| Customer's employees "went silent" | trial cap crossed | `/clients/{id}` shows it; move to paid (`billing-state`) or the cap resets on trial re-entry |
| Voice call reaches silence | xAI registration / trunk broken | `GET /clients/{id}/voice-preflight` checks all 4 links |
| Two Twilio numbers for one business | (pre-P0-1) — shouldn't recur | release the spare in the Twilio console |
| Duplicate booking / duplicate reply | shouldn't happen — `WebhookDelivery` dedup + `book_job` upsert | capture the `MessageSid`/`CallSid`, check `webhookdelivery` |

## 10. Currently production-ready vs external-dependency-pending

**Production-ready now:**
- Voice receptionist (inbound calls, triage, booking capture, hangup)
- The conversation engine and booking flow (verified by tests + the dashboard test-chat)
- Founder-driven onboarding via `/clients/*`
- Webhook authentication + idempotency
- Health checks, structured logging, the verified backup job

**External dependency pending — NOT ready:**
- **Outbound SMS** — blocked on per-customer A2P 10DLC campaign approval (Twilio/TCR, ~3-5 business days per customer once registered). This includes the missed-call text-back, owner alerts by SMS, and every retention agent. Voice does not depend on it.
- **Durable storage** — pending the Postgres cutover (human action).
- **Error monitoring** — pending `SENTRY_DSN` (human action).
