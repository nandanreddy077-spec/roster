# Changelog

Notable changes to Roster, most recent first. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/). This file starts
2026-07-30; full history before that lives in `git log` — it isn't
reconstructed here.

## Unreleased

- Documentation and repository-hygiene pass: root `README.md`, `docs/`
  reference set, `CONTRIBUTING.md`/`SECURITY.md`/`CHANGELOG.md`, archived
  superseded outreach material. No behavior change.

## 2026-07-29 — Reviews employee

- Review-request send moved onto the delayed tick; the "one polite
  follow-up"; review replies, classification, and negative-sentiment owner
  alerts.

## 2026-07-27 to 2026-07-29 — Frontdesk conversation quality

- Three sprints of conversation-quality fixes: date/time handling, per-trade
  triage, preferred windows, reschedule/cancel recognition, service-area and
  membership awareness, objection handling, pricing guidance, multi-problem
  calls, fee disclosure.

## 2026-07-28 to 2026-07-29 — Departments migration

- Phase 0 through Phase 5: department/employee registries, `workspace.py`
  view-model layer, the `/v2/dashboard*` department-first customer
  dashboard, `BriefingWorkspace` and `ExpansionWorkspace`. Closed out with
  `ARCHITECTURE.md` — the frozen invariants for that dashboard.

## Earlier

- Revenue Recovery (quote follow-up + reactivation), named as Chaser/Rebooker,
  plus a third face, Renewals (membership renewals).
- Referrals agent (automatic referral-ask on job completion).
- Live voice via xAI's Grok Voice Agent API, replacing an earlier Vapi
  integration.
- Founding-roster hire flow, self-serve signup/onboarding, founder console.

See `git log` for the complete, unabridged history.
