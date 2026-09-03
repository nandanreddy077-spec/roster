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
| **P0-2** | **Orphan number on a crash between purchase and DB commit.** `buy_twilio_number()` returns → process dies → `twilio_number_sid` never persisted. Roster now pays for a number no row points at, and nothing detects it. | `app.py:921-926`, `activation.py` | The P0-1 claim makes this *reconcilable* (a stuck `provisioning_started_at` with no SID is the signal). Add a documented manual reconcile procedure (§7). Not auto-reconcile — disproportionate for 10-20 customers. |
| **P0-3** | **Customer data is not durable.** SQLite lives on the Railway volume; the volume dies with the service on a bad migration or a platform incident, and `backup.py` writes its snapshots *to the same volume*. Bookings, conversations, and Twilio identifiers would be unrecoverable. | `db.py`, `backup.py`, `railway.toml` | Harden + verify `migrate_to_postgres.py`, document the cutover. **Human action: provision Railway Postgres, set `DATABASE_URL`.** Code prepared here; cutover is a human step. |

### P1 — before self-serve opens / before customer count grows

| ID | Finding | Where | Fix |
|---|---|---|---|
| **P1-1** | **No backend provisioning precondition.** `provision_number` would buy for any business id. Founder-only today (HTTP-Basic), so no customer can reach it — but the self-serve wizard will call the same path. **FIXED (commit `<p1-1>`):** `Business.payment_method_verified_at` + `provisioning_unlocked_at`; `provisioning.provisioning_allowed()`; `provision_number` refuses without one; `POST /clients/{id}/unlock-provisioning` is the founder override; console button added. `activate_frontdesk`'s own buy is unchanged (no live caller — gated as part of the self-serve wizard split). | done | |
| **P1-2** | **One business's failure aborts the whole scheduler tick.** `runner.dispatch_tick` loops businesses with no per-business `try`; `enroll_completed_estimates` loops jobs with no per-job `try`. A single bad row skips every later business *and* every later tick phase (Dispatcher, Quote Chaser, Reviews, Referral, Membership). | `runner.py:148`, `recovery_service.py:106`, `recovery_tick.py:64` | Per-iteration `try/except` that logs and continues; make `recovery_tick.run` resilient between phases. |
| **P1-3** | **`FOUNDER_ALERT_PHONE` unset.** A paying customer hits the $20 trial cap → their AI employees go silent → `trial_cap._notify_founder_cap_reached` no-ops. The owner is told; the founder is not. | env | **Human action: set `FOUNDER_ALERT_PHONE`.** |
| **P1-4** | **No error monitoring.** `SENTRY_DSN` unset → `configure_sentry` no-ops. A provisioning failure, a tick crash, an unhandled 500 is visible only by grepping Railway logs. | env | Code is ready (`sentry_config.py`, `sentry-sdk` installed). **Human action: create a Sentry project, set `SENTRY_DSN`.** Verify background-job + provisioning failures are captured. |
| **P1-5** | **SMS "configured" is not distinguished from "deliverable".** `TWILIO_MESSAGING_SERVICE_SID` is set, so the app behaves as if SMS works, but the campaign is rejected and carriers filter the traffic. Nothing surfaces this; owner alerts and the missed-call text-back silently don't arrive. | `channels.py`, `notifications.py` | Model per-business SMS delivery state (or one global flag now); gate proactive sends and surface "SMS pending carrier approval" in the founder console + owner dashboard. Ties into the per-customer A2P work. |
| **P1-6** | **No rate limiting on `/login`.** Brute force against a customer password is unthrottled. Low impact today (≈0 password accounts — owners use access links), real once self-serve creates password accounts. | `portal.py` | Lightweight per-IP + per-account throttle. `/request-access` and `/signup` too when self-serve opens. |
| **P1-7** | **Backups share the DB's failure domain.** Covered by P0-3's Postgres move (Railway Postgres has managed daily backups + PITR). Until then, document that a volume loss is unrecoverable. | `backup.py` | Postgres migration; interim note in §6. |

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

_(filled as the pass proceeds)_

## 4. Rollback procedure

_(filled as the pass proceeds)_

## 5. Database migration procedure (SQLite → Postgres)

_(hardened + documented in Step 2 of this pass — see `runbook.md` "Migrating to Postgres" for the current version)_

## 6. Backup & restore

_(filled as the pass proceeds)_

## 7. Twilio provisioning lifecycle & recovery

_(filled as the pass proceeds — depends on the P0-1 claim landing first)_

## 8. Monitoring

_(filled as the pass proceeds)_

## 9. Common failures & incident response

_(filled as the pass proceeds)_

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
