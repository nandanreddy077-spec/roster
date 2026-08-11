# Runbook

Operational procedures for Roster in production. Written for whoever is
on-call — could be the founder, could be you six months from now having
forgotten all of this. Every command here has actually been run against
either production or a real copy of it; nothing is theoretical.

Companion docs: [`deployment.md`](deployment.md) (how the service is hosted),
[`architecture.md`](architecture.md) (how the tick pipeline works). This
doc is about **what to do when something is wrong**, not how the system is
built.

**Status as of 2026-08-11 — nothing below is live in production yet.**
`/health`, structured logging, the backup job, and the trial-cap billing
control were all built and verified this milestone (against real production
data pulled via `railway ssh`, and a real Postgres 16 container) but **not
deployed** — this whole runbook describes what production will do once
Milestone 1's branch is merged and pushed, not what it does today. Don't
`curl /health` against the live domain expecting it to exist until that
ships. Postgres migration has been built and independently verified — see
[Migrating to Postgres](#migrating-to-postgres) — but is a further, separate
cutover decision even after the rest of this deploys.

**Sentry needs a human to actually set it up.** The code
(`sentry_config.py`) is written and tested, but creating a Sentry account,
a project, and getting a real `SENTRY_DSN` is not something that can be
done on your behalf — go create one, then `railway variables set
SENTRY_DSN=...`. Until that's set, `configure_sentry()` silently no-ops
(logs `"sentry not configured (no SENTRY_DSN)"` once at boot) — the app
runs fine without it, you just don't get Sentry issues, only the structured
logs.

---

## First, check `/health`

```bash
curl -s https://<your-railway-domain>/health | python3 -m json.tool
```

```json
{
  "status": "ok",
  "checks": {
    "database": {"ok": true},
    "scheduler": {"ok": true, "last_tick_at": "...", "age_seconds": 312, "last_tick_error": null},
    "backups": {"ok": true, "latest": "roster-20260811T030000Z.db", "age_seconds": 41000}
  }
}
```

Three checks, two different meanings:

- **`database.ok: false`** → the whole app is broken. HTTP status is 503,
  and Railway will restart the container on this — that's correct, it's the
  one failure a restart can actually fix (a dropped connection, a wedged
  pool).
- **`scheduler.ok: false`** or **`backups.ok: false`** → something needs a
  human, but the app is otherwise serving traffic fine. Status stays 200 on
  purpose — restarting the container doesn't fix a stuck scheduler any
  faster than the next tick would, and turning either into a hard failure
  risks a restart loop over what might just be one slow tick. Read the
  `error`/`detail` field and go investigate.

If `/health` doesn't respond at all, that's worse than a 503 — the process
itself is down. Check Railway's deploy logs first.

---

## Reading the logs

Every log line is one JSON object, on stdout — Railway captures it as-is.
Search Railway's log viewer, or pull with the CLI:

```bash
railway logs
```

Every structured field is real JSON, not text buried in a message string —
so you can search on `business_id`, `job_id`, `call_id`, `customer_phone`
directly rather than grepping for a phrase and hoping the format didn't
change. A typical error line:

```json
{"ts": "2026-08-11T06:37:19Z", "level": "ERROR", "logger": "review_service",
 "message": "Reviews failed to send review request",
 "business_id": 7, "job_id": 42,
 "exc_info": "Traceback (most recent call last):\n..."}
```

**If `SENTRY_DSN` is set** (check `railway variables --kv`), every `ERROR`
line above also became a Sentry issue automatically, with the same fields
attached — check Sentry first for anything past a few days back, since
Railway's own log retention is short.

---

## A customer's AI has gone quiet (trial cap)

**Symptom:** the owner says Frontdesk (or Quote Chaser, Reviews, Membership
Agent, Referral) stopped replying to customers.

**Cause:** every business starts on `billing_state = trial`, capped at
`trial_cap_cents` (default $20) + a $2 soft buffer. Past that, `can_respond()`
returns `False` and the employee goes silent. This is the #1 cause of "the
AI stopped working" for a business that's actually using the product a lot.

**Check:**

1. Open the founder console: `https://<domain>/clients/<id>` (HTTP Basic,
   `ADMIN_PASSWORD`).
2. Look for the billing line: `Billing: trial · $22.00 spent` and the ⚠
   banner if the cap's been crossed.
3. The owner should already have gotten an SMS and an entry in their
   dashboard notifications feed (`trial_cap_reached`) — if support hasn't
   heard from them yet, check whether that actually delivered
   (`OwnerNotification.delivered`).

**Fix:** click **"Move to paid (remove cap)"** on the client page. Takes
effect immediately — the very next inbound message gets a reply. No restart,
no waiting for a tick.

**Reversing it:** "Move back to trial" is available but rare — only for a
business that was moved to paid by mistake. It resets the notification claim
too, so if the business ever re-crosses the (still-capped) trial threshold,
the owner is alerted again.

---

## Restoring the database (SQLite, current production)

**When:** the volume is corrupted, a bad migration needs undoing, or someone
fat-fingered a delete in the founder console.

**Where backups live:** `{ROSTER_DATA_DIR}/backups/roster-<timestamp>.db`
(or `$ROSTER_BACKUP_DIR` if set). One snapshot is taken per UTC calendar day,
at the top of the first scheduler tick after midnight UTC — see
[`backup.py`](../agent/backup.py). 30 kept, so roughly a month of history.

**Check what you have, without touching anything:**

```bash
railway ssh -- ls -la /data/backups
```

**The restore itself** (do this from a maintenance window — the app should
not be actively writing during the copy):

```bash
# 1. SSH in and confirm the snapshot you want is intact
railway ssh -- python3 -c "
import sqlite3
c = sqlite3.connect('file:/data/backups/roster-20260811T030000Z.db?mode=ro', uri=True)
print(c.execute('PRAGMA integrity_check').fetchone())
"

# 2. Stop the app from writing (Railway dashboard → pause the service, or
#    scale to 0 instances — do NOT just kill -9 the process mid-write)

# 3. Copy the snapshot over the live database
railway ssh -- cp /data/backups/roster-20260811T030000Z.db /data/roster.db
railway ssh -- rm -f /data/roster.db-wal /data/roster.db-shm

# 4. Resume the service
```

**This has actually been tested.** During Milestone 1, a real production
snapshot was taken, the "database" deleted (including `-wal`/`-shm`), and
restored — 18 tables, 97 rows, byte-identical, verified with
`backup.row_counts()` table-by-table. See `test_backup.py`'s
`test_restore_drill_a_snapshot_survives_losing_the_original` for the
automated version of the same drill, and re-run it by hand before you ever
need this for real:

```bash
cd agent && .venv/bin/python -c "
from backup import take_snapshot, row_counts
from pathlib import Path
snap, counts = take_snapshot(Path('data/roster.db'), Path('/tmp/drill'))
print('snapshot verified:', counts)
"
```

**Known gap:** backups live on the same Railway volume as the database.
They protect against corruption, a bad migration, and a mistaken delete —
**not** against losing the volume itself. Pulling a snapshot off-box
(`railway ssh -- cat /data/backups/<file> | ...`, save it somewhere durable)
is a manual step until there's an automated off-site copy. Do this
periodically, not just when something's already wrong.

---

## Migrating to Postgres

Not yet done in production (see banner at top of this doc). When you're
ready to cut over:

1. **Provision Postgres** on Railway, get the connection string.
2. **Take a fresh backup first**, always — see above.
3. **Pull the live database** the same way the migration was tested:
   ```bash
   railway ssh -- base64 /data/roster.db > prod.b64
   base64 -d -i prod.b64 -o prod-roster.db
   ```
4. **Run the migration tool** against a scratch Postgres instance first,
   never straight at the one Railway will use for production:
   ```bash
   DATABASE_URL=postgresql://... .venv/bin/python agent/migrate_to_postgres.py prod-roster.db
   ```
   It refuses to run against a non-empty database (no partial double-run),
   copies every table, resets every identity sequence past the copied data
   (the classic migration bug — SQLite ids are plain values, Postgres ones
   are sequence-backed, and skipping this means the first insert after
   cutover collides with row 1), and verifies row counts + sequences before
   returning. It raises `MigrationError` and does not silently proceed if
   anything disagrees.
5. **Run the full test suite against it** before touching production:
   ```bash
   ROSTER_TEST_DATABASE_URL=postgresql://... .venv/bin/pytest -q
   ```
   Should be 100% identical to the SQLite run. If anything differs, **stop**
   — do not proceed to cutover.
6. **Shadow-validate the real application**, not just the schema: boot the
   app with `DATABASE_URL` pointing at the migrated database and exercise
   the dashboard, an employee deploy, a real webhook + AI booking, and a
   scheduler tick. Compare structural outcomes (job counts, notification
   kinds, HTTP statuses) against the same sequence on SQLite — not exact AI
   text, which is nondeterministic by nature, but the actions the AI took.
7. **Only then**, set `DATABASE_URL` on the real Railway service and
   redeploy. `locks.py` automatically takes the `pg_advisory_lock` path
   instead of the SQLite `threading.Lock` the moment the dialect changes —
   no code change needed, just a redeploy.
8. **After cutover**, `backup.py`'s daily-snapshot job is a deliberate no-op
   (`"not a SQLite deployment — managed backups apply, skipping"`) —
   confirm Railway's own Postgres backups are actually configured before
   relying on that sentence being true.

All of the above was performed end-to-end against a real Postgres 16
container and a real copy of production during Milestone 1 — full report
in the commit history (`git log --grep=migration`).

---

## A scheduler tick failed

**Symptom:** `/health`'s `scheduler.ok` is `false`, or `last_tick_error` is
set.

The tick (`recovery_tick.run()`) runs hourly, in-process, and handles
qualification, dispatch planning, quote-chase enrollment, and every
proactive send (Recovery, Referral, Reviews, Membership). A single failed
tick does not lose work — everything it does is idempotent (gated by a
timestamp column or a unique index), so the next tick picks up where the
last one left off. But a **repeatedly** failing tick means something
structural is broken (a bad migration, a credential that expired).

**Check:**

```bash
curl -s https://<domain>/health | python3 -c "import json,sys; print(json.load(sys.stdin)['checks']['scheduler'])"
```

`last_tick_error` names the exception. Cross-reference the logs for the
full traceback (search for `"level": "ERROR"` around that timestamp).

**If it's been down more than ~2 hours** (the staleness threshold), that's
worth waking someone up for — it means either the process is stuck, or
every tick since has also failed.

---

## Checking a specific customer's activity

**Founder console** (`/clients/<id>`, HTTP Basic auth): jobs, billing state,
deployed employees, recovery/referral/membership campaigns, provisioning
status.

**Call recordings / transcripts:** every authenticated voice call is
captured to `{ROSTER_DATA_DIR}/call_captures/{call_id}.jsonl` — a complete,
replayable, millisecond-timestamped log of every pipeline stage (webhook →
WebSocket connect → first AI response → tool call → job persisted → owner
notified → call ended). The same events also reach the structured logs in
real time (`logger: "call_trace"`, field `call_id`).

```bash
railway ssh -- cat /data/call_captures/<call_id>.jsonl | python3 -m json.tool
```

**SMS history:** `Message` table, scoped by `business_id` +
`customer_phone`. No console UI for raw message history yet — query
directly if needed (see [Direct database access](#direct-database-access)).

---

## Direct database access

```bash
railway ssh
```

Known quirks (found during this session, not documented anywhere else):
the SSH key-verification step occasionally reports
`"status":"unavailable" ... "the verification service was unreachable"` —
this is transient on Railway's end, not a real rejection; retry. Argument
quoting through `railway ssh -- python3 -c "..."` can mangle special
characters — prefer a script file (`railway ssh -- python3 /tmp/script.py`
after copying it up) for anything nontrivial, or a form with no shell
metacharacters at all.

Once in:

```bash
python3 -c "
import sys; sys.path.insert(0, '/app')
from db import engine
from sqlmodel import Session, select
from db_models import Business
with Session(engine) as s:
    print(s.exec(select(Business)).all())
"
```

**Never write directly against production** outside of the documented
recovery procedures above. If you need to change something, use the founder
console or add a proper migration — a hand-run `UPDATE` leaves no audit
trail and nothing to review.

---

## Deploying

Not covered by an existing CI/CD pipeline as of this doc — pushes to `main`
trigger Railway's own build+deploy from `agent/`. Before pushing:

1. `ruff check . && ruff format --check . && mypy` (or just let CI fail the
   PR — see [`.github/workflows/ci.yml`](../.github/workflows/ci.yml))
2. `cd agent && pytest -q` — full suite, must be green
3. If the change touches the database schema, confirm the migration is
   additive (`_migrate_add_columns` in `db.py`) — there is no down-migration
   path, so a bad schema change is a restore-from-backup situation, not a
   quick fix.

**Rollback:** Railway keeps prior deploys — redeploy the last known-good
commit from the Railway dashboard. If the bad deploy already wrote data in
an incompatible shape, a schema rollback alone won't fix it; restore from
backup instead.

---

## Escalation

There is currently no on-call rotation, paging system, or SLA — this is a
pilot-stage product. The founder is the incident responder. If Sentry is
configured, issues land there; if not, `railway logs` and `/health` are the
only signals. Fixing that gap (real alerting, not just visibility) is
explicitly **out of scope** for Milestone 1 — see the Pilot Readiness
roadmap for what's deferred to later milestones.
