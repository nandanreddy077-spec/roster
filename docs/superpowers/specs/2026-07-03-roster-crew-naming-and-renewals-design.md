# Roster Crew Expansion — Named Agents, Renewals, Reviews — Design Spec

**Date:** 2026-07-03
**Status:** Design approved, ready for implementation plan

---

## Executive Summary

Three changes, one release:

1. **Name the crew.** Revenue Recovery's two existing faces ("quote" and "reactivation")
   get distinct crew identities — **Chaser** (quote follow-up) and **Rebooker**
   (reactivation) — shown everywhere a human sees them. Purely a display-layer change;
   internal `face` values and all engine logic are untouched.
2. **Renewals — a third face.** A new Recovery face, `"membership"`, chases
   maintenance-plan renewals on each customer's own renewal date instead of days-since-
   campaign-start. Rides Recovery's existing sequence/reply/booking machinery; the only
   new logic is a date-anchored clock.
3. **Reviews — a feature, not an agent.** One button on a job card ("Mark done") fires a
   single review-request text if the client has a review link on file. No sequences, no
   Claude, no new agent identity — deliberately positioned as a feature switch per the
   founder's own market research (review automation is already commoditized by
   incumbents; it shouldn't be sold as a seat).

**Outcome:** the roster grows from 2 named agents to 3 named + 1 feature, with the
promoted pitch trio staying **Frontdesk, Chaser, Rebooker** (the three highest-impact
roles per the founder's research) and Renewals + Reviews filling out a genuine "one-stop
shop" menu without duplicating any engine work.

---

## Problem & Opportunity

- **Positioning evidence:** named AI agents are a proven pattern in this exact market —
  11x (Alice), Artisan (Ava), and Rosie (a direct home-services competitor) all sell
  named agents because it maps to the "hiring" mental model the whole pitch rests on.
  Roster currently has one named agent (Frontdesk) and one unnamed one ("Recovery," which
  is actually two distinct jobs wearing one name).
- **Competitive context:** Avoca ($1B valuation, April 2026) and Netic both chase larger
  operations with contact-center complexity. Roster's edge — fully managed, priced per
  booked job, built for shops too small for Avoca to bother with — is sharpened, not
  duplicated, by a clear named-crew menu.
- **Engineering leverage:** splitting Recovery's two faces into two names costs nothing
  (display-layer only). Renewals reuses ~90% of Recovery's engine (sequence timing,
  reply routing, slot booking, STOP handling) — the only new work is a date-anchored
  clock instead of a campaign-start-anchored one. Reviews is a single button and one
  SMS send. Five named/featured things on the roster for roughly 2–3 days of
  incremental work, not five separate builds.

---

## Design

### 1. Naming layer (Chaser, Rebooker)

- `RecoveryCampaign.face` keeps its existing internal values: `"quote"`,
  `"reactivation"`, and the new `"membership"`. **No database rename, no migration.**
  Renaming a stable internal identifier for cosmetic reasons is exactly the kind of
  churn the codebase avoids elsewhere.
- A new pure mapping in `recovery_engine.py`:
  ```python
  FACE_DISPLAY_NAMES = {"quote": "Chaser", "reactivation": "Rebooker", "membership": "Renewals"}
  ```
- Every place a human sees a face — dashboard agent-roster tiles, campaign list badges,
  the campaign creation form's face dropdown, campaign detail headers, landing page copy
  — reads through this mapping. Nowhere else changes.
- Module and variable names in the codebase (`recovery_service.py`, `RecoveryJob`, etc.)
  are unaffected — "Recovery" remains the correct internal/technical umbrella term for
  the shared engine, same as "Frontdesk" is a product name for what's internally just
  `AgentEngine` wired to inbound SMS.

### 2. Renewals (the `"membership"` face) — the real new agent

**The core problem it solves:** a customer's maintenance plan lapses because nobody
called them before the renewal date. Unlike quote follow-up or reactivation — both
anchored to *when the campaign started* — a renewal has to be anchored to *that specific
customer's own date*. Two customers pasted into the same campaign on the same day can
have renewal dates weeks apart; the message timing has to respect that.

- **New column:** `RecoveryJob.anchor_date: Optional[str]` (ISO `YYYY-MM-DD`). `NULL`
  for quote/reactivation jobs — their behavior is provably unchanged, since every
  existing code path that doesn't check `face == "membership"` never looks at it.
- **New offsets:** a new constant, `MEMBERSHIP_OFFSETS = [-30, -14, -7, 0, 7]`, added
  alongside the existing `SEQUENCE_DAYS = [1, 3, 7, 14, 21, 28]` — **not merged into
  it.** `SEQUENCE_DAYS` is a single flat list shared by both the quote and reactivation
  faces today (they use the same day-offsets, just different message text) and stays
  exactly as-is; membership needs its own list because its offsets are structurally
  different (relative to a per-customer date, and can be negative). Days relative to
  `anchor_date`: a customer renewing July 15 gets messages June 15, July 1, July 8,
  July 15, and July 22, regardless of when the owner pasted the list or when the cron
  happens to run.
- **Clock logic in `tick()`:** branches on `campaign.face == "membership"`. For that
  face, elapsed time is computed as `today - anchor_date` (in days) instead of
  `today - campaign.started_at`, and compared against `MEMBERSHIP_OFFSETS` instead of
  `SEQUENCE_DAYS`. Quote and reactivation jobs take the existing code path completely
  unchanged. The existing "latest unsent due offset wins" catch-up rule applies to
  membership the same way it applies today — a customer added 10 days before their
  renewal starts at the −7 offset, never replays −30/−14 as a burst.
- **`last_sent_day` reuse:** stores the offset as-is, including negative values. This is
  the one place existing code needs an audit: any comparison that assumes
  `last_sent_day` is a non-negative day-count (e.g. the terminal "last sequence day
  passed with no reply → `no_response`" check) must become offset-aware — compare
  against `max(OFFSETS[face])`, not a hardcoded `28`.
- **New templates:** `TEMPLATES["membership"]` — 5 messages for the 5 offsets, using
  `{customer_name}`, `{service_type}`, and a new `{renewal_date}` variable. Day-of and
  +7 copy leans on losing member pricing/priority scheduling — the actual stakes of a
  lapsed plan.
- **`create_campaign()`:** membership customers are pasted as
  `phone,name,service_type,renewal_date` (the amount/days-since column becomes a
  strict `YYYY-MM-DD` date). Validated at form submission with a clear per-row error —
  a malformed date silently creating a job with no anchor would defeat the entire
  point of the feature.
- **Reply handling, booking, STOP:** zero new code. Same `handle_recovery_reply`, same
  router precedence, same slot-offer/confirm/book flow, same deterministic STOP check.
  A confirmed slot books the renewal visit as a normal `Job`, same as today.

### 3. Reviews (feature switch, not a named agent)

Deliberately the smallest possible implementation — this is explicitly *not* sold as a
seat on the roster, so it shouldn't look like one in the code either.

- **`Client.review_link: Optional[str]`** — the business's review URL (Google, etc.),
  set once at onboarding or any time after via a small inline form.
- **`Job.completed_at: Optional[datetime]`** — set the moment a job is marked done.
- **New route** `POST /clients/{client_id}/jobs/{job_id}/complete`: sets
  `completed_at`. If `client.review_link` is set and the job has a callback number,
  sends exactly one SMS via the existing `sms_channel`: *"Thanks for choosing
  {business_name}! If we did right by you, a quick review means a lot: {review_link}."*
  If there's no link, no phone number, or the send fails, the job is still marked
  done — the review text is best-effort and never blocks the core action.
- **New route** `POST /clients/{client_id}/review-link`: sets/updates the client's
  review link, via a one-field form on the client detail page.
- **UI:** a "Mark done" button appears on each captured-job card; once clicked, it's
  replaced with a "Completed" badge (guards against a second, duplicate send). The
  Reviews feature lives in its own small tile in the agent-roster grid showing
  link-set/not-set status and the inline form — visually distinct from the Chaser/
  Rebooker/Renewals tiles, which each show live campaigns.

### 4. Dashboard reorganization

- The agent-roster grid on the client detail page grows from 2 tiles (Frontdesk,
  Recovery) to 5: **Frontdesk**, **Chaser** (campaigns where `face == "quote"`),
  **Rebooker** (`face == "reactivation"`), **Renewals** (`face == "membership"`), and
  **Reviews** (link status + inline form). The grid already uses `auto-fit` from the
  design-system pass, so it reflows cleanly at 5 tiles with no layout changes needed.
- The campaign creation form's face `<select>` shows display names ("Chaser — quote
  follow-up" etc.) mapped from `FACE_DISPLAY_NAMES`; each tile's "+ New campaign" link
  pre-selects its face via a query param so the owner never has to pick it manually.
- Campaign detail page header shows the display name instead of the raw face string.

### 5. Landing page + ROSTER.md updates

- "Meet the roster" keeps its current full treatment (phone mockup, stat-backed copy)
  for the promoted trio — **Frontdesk, Chaser, Rebooker** — per the founder's research
  ranking. Renewals and Reviews move from the old "coming next" placeholder cards into
  a lighter "also on the roster" strip, marked live rather than speculative, making the
  one-stop-shop story concrete without diluting the three-agent pitch.
- `ROSTER.md`'s role-sequence section gets updated to reflect the actual shipped roster
  (this was already flagged as stale after the earlier landing-page rewrite).

---

## Data Model Changes (extend existing tables only — no new tables)

```
RecoveryJob.anchor_date       : Optional[str]        -- ISO date, NULL except membership face
Client.review_link            : Optional[str]        -- NULL until owner sets it
Job.completed_at              : Optional[datetime]   -- NULL until marked done
```

---

## Constraints & Scope

### In Scope
- Membership face fully wired into the existing Recovery engine (templates, clock,
  replies, booking).
- Reviews as a single button + single SMS send, no sequencing.
- Display-name mapping applied across dashboard and landing page.
- ROSTER.md role-sequence section brought up to date.

### Out of Scope (explicitly deferred, matching ROSTER.md's "don't guess integrations" rule)
- Auto-importing membership lists from a billing/CRM system — still manual paste, same
  as quote/reactivation today.
- Multi-link reviews (Yelp + Google + Facebook) — one link per client.
- Any grace-period/auto-cancellation logic beyond the final +7 nudge.
- A generic client-edit page — the review-link form is the one narrowly-scoped
  exception, added only because Reviews needs it.

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Negative `last_sent_day` values break a comparison written assuming non-negative day-counts | Audit every `last_sent_day` comparison in `tick()` and `handle_recovery_reply` during implementation; the terminal "sequence exhausted" check for membership must compare against `max(MEMBERSHIP_OFFSETS)` (7), not the existing hardcoded `28` used for quote/reactivation. |
| Malformed pasted renewal dates silently create untimed jobs | Validate `YYYY-MM-DD` at form submission; reject with a clear per-row error, same rigor as the existing phone/name/service_type fields. |
| Naming layer drifts out of sync (a raw `face` string leaks into UI somewhere) | `FACE_DISPLAY_NAMES` is the single source of truth; a test enumerates every known face value and asserts a display name exists for it. |
| Reviews SMS fires twice for one job | `completed_at` gates both the button (hidden once set) and the send (route checks `completed_at is None` before sending, not just before setting it). |
| Reviews send blocks job completion on SMS failure | Send is wrapped in try/except, same per-operation isolation pattern as `tick()`; `completed_at` is set regardless of send outcome. |

---

## Testing

- Membership offset math: before/on/after renewal, late-added customer (catch-up
  lands on the correct offset, not a burst), no-resend of an already-sent offset.
- Terminal `no_response` fires correctly for all three faces (offset-aware, not
  hardcoded to day 28).
- Template coverage: every face has a template for every one of its offsets/days.
- `FACE_DISPLAY_NAMES` covers every value ever assigned to `face`.
- Membership paste-list parsing: valid date accepted, malformed date rejected with a
  clear error, valid rows unaffected by one bad row.
- Reviews: marking done with a link sends exactly one SMS with the right content;
  marking done with no link sends nothing but still completes; SMS failure still
  marks the job done; the button/badge state is correct before and after.
- Full regression: every existing quote/reactivation test stays green untouched,
  proving the `anchor_date IS NULL` path is byte-identical to today's behavior.

---

**Design approved by:** User
**Ready for:** Implementation planning
