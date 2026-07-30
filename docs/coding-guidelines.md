# Coding guidelines

These are conventions already in use across `agent/` — written down so new
code stays consistent, not a proposal to change anything.

## Patterns to follow

- **One idempotent write path per side effect.** `bookings.py` (book a job),
  `deployment.py` (deploy an employee) each expose a single function that's
  safe to call more than once. New side-effecting operations should follow
  the same shape rather than letting callers write directly.
- **`service.py` is the one conversation-turn handler.** Dashboard test-chat,
  the SMS webhook, and the voice adapter all route through it. Don't add a
  second place that processes a turn.
- **`service`/`engine` split for background jobs.** `recovery_service.py` vs
  `recovery_engine.py` (same for referral/review): `service` owns DB access
  and the daily tick; `engine` owns prompt/template construction and has no
  DB dependency. Keep new background jobs split the same way.
- **Registries are code, not data.** `departments.REGISTRY`,
  `employees.REGISTRY`, `metrics.METRIC_RECORDS`/`EMPLOYEE_RECORDS` are the
  single source of configuration — see invariant 2 in
  [`../ARCHITECTURE.md`](../ARCHITECTURE.md). Don't duplicate their content
  into a template or a second table.
- **No fabricated metrics.** If a number can't be sourced from a real row
  today, it's omitted — never a placeholder zero. See invariant 10 in
  `../ARCHITECTURE.md`.
- **Fail closed on missing secrets.** `ADMIN_PASSWORD` and
  `SESSION_SECRET_KEY` (in production) cause the relevant surface to refuse
  to serve rather than fall back to something insecure. Follow this for any
  new secret-gated route.

## Naming

- Modules are `snake_case`, one noun or noun-phrase per file
  (`bookings.py`, `departments.py`), matching the domain concept they own —
  not the technical layer (`services.py`, `models.py` as a dumping ground).
- Tests mirror the module they cover 1:1: `<module>.py` → `test_<module>.py`.
- Environment variables are `SCREAMING_SNAKE_CASE`, documented in
  [`../agent/.env.example`](../agent/.env.example) as the single source —
  don't introduce a new env var without adding it there.

## Before touching `/v2/dashboard*`

Read [`../ARCHITECTURE.md`](../ARCHITECTURE.md) in full first — it's a
PR-review checklist, not background reading, and CLAUDE.md requires it.

## Before any UI/visual change

Read [`../DESIGN.md`](../DESIGN.md) first — fonts, colors, spacing, and
aesthetic direction are defined there, not left to per-PR judgment.
