# Changelog

Notable changes to Roster, most recent first. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/). This file starts
2026-07-30; full history before that lives in `git log` — it isn't
reconstructed here.

## Unreleased

- **Founder-provisioned customers can reach their dashboard.** Roster sets
  every business up by hand and never creates a password for the owner, so a
  fully set-up shop previously had no way at all to see its own dashboard.
  New `/access/{token}` route: the founder copies a signed, 14-day link from
  the client page and texts it to the owner. The optional owner email on
  `/clients/new` adds Google sign-in as a second door.
- **Login routes on real deployment state, not `frontdesk_live`.** That
  column is only set by the retired self-serve wizard, so a hand-provisioned
  owner was bounced into onboarding they'd already finished. Now an existing
  `Employee` row decides (`ARCHITECTURE.md` invariant 8).
- **Setup checklist on the founder client page** — six derived facts, in the
  order the work happens, replacing a memory game across five panels. Nothing
  is green unless a real column says so; "Answered a real customer" cannot be
  satisfied by test bookings.
- **`/v2/dashboard/settings` now exists.** It had been a nav item pointing at
  a 404 — a dead control in the customer's top bar
  (`ARCHITECTURE.md` invariant 9). `test_every_nav_item_resolves_to_a_real_page`
  keeps every nav href honest from here.
- **API docs no longer published** — `/docs`, `/redoc` and `/openapi.json`
  are off (2026-08-04 penetration test, recommendation 3). The routes were
  already authenticated; this stops handing out the floor plan.
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
