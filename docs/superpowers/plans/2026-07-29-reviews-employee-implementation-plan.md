# Reviews — Employee #2, Customer Service Department: Implementation Plan

**Status:** APPROVED (2026-07-30), with one addition — negative-sentiment
handling for review replies (§2a, §4, §6, §7 updated below). Implementation
proceeds PR-by-PR, same TDD discipline as the Frontdesk sprints (failing
tests first, smallest correct fix, full suite green, one commit per PR,
stop for review after each). This document is the shape the implementation
takes — it will be annotated as each PR lands, not re-litigated per PR.

**Planned PR sequence** (each independently testable, each a complete
behavior change, no PR leaves two send-paths active at once):
1. Move the review-request send off the synchronous "Mark done" path onto
   the tick-based, delayed mechanism (§3's first bullet). No reply handling
   yet.
2. The one follow-up (§3, §2 state transitions), added to the same tick.
3. `ReviewReply` table + reply routing + reply classification, **including
   negative-sentiment detection and owner escalation** (§2a — new).
4. Analytics/metrics (§6) + founder-console visibility.
5. Cutover: flip `employees.py`'s `"reviews"` status once validated.

---

## 0. Inspection — what already exists, what's placeholder, what's missing

**Already real, not placeholder:**
- `Job.completed_at` — set by the founder's "Mark done" action
  (`app.py`'s `complete_job`). This IS the "job completed" signal — no new
  detection mechanism is needed; it already exists and is already reliable.
- `Job.review_requested_at` — set the one place a review text is actually
  sent today. Already the metric backing (`metrics.REVIEW_REQUESTS_SENT`,
  `EMPLOYEE_RECORDS["reviews"]`'s one `RecordSource`).
- `Business.review_link` — the owner's Google/Yelp URL, founder-settable via
  `/clients/{id}/review-link`.
- `employees.py`'s `EmployeeDefinition("reviews", "customer_service",
  "internal", "Reviews", mission="Is Reviews requesting feedback?")` — the
  registry entry exists; status is `internal` (founder can deploy it
  manually), not yet `live`.
- `runner.py`'s `dispatch_job_completed`/`JOB_COMPLETED_ROLES`/`is_active` —
  built specifically for "the first job-completion employee," per its own
  docstring. Not yet used by anything (`JOB_COMPLETED_ROLES` is empty).

**Placeholder / minimal, needs to become real:**
- The entire review-request send is currently ~10 lines inlined directly in
  `app.py`'s `complete_job` route: synchronous, instant (no wait), one SMS,
  no follow-up, no reply handling, no per-business review-cadence
  configuration. This is a stub proving the wiring works, not the employee.

**Missing entirely:**
- Any delay/wait logic (today's send fires the instant "Mark done" is
  clicked — the opposite of "wait an appropriate amount of time").
- Any follow-up mechanism.
- Any reply-handling for a customer who texts back to a review request
  (falls through to plain Frontdesk today, which has no idea a review ask
  is outstanding).
- Any "stop if they already reviewed" logic (there's nothing to check
  against, by design — see §Honesty note below).
- Any dedicated analytics beyond the single `REVIEW_REQUESTS_SENT` count.
- A cadence/config knob (how long to wait before the first ask, how long
  before the one follow-up) — today's send has no delay at all, so there's
  no config for one either.

**Honesty note, upfront, because it shapes the whole design:** metrics.py
already states the governing rule explicitly — *"Reviews RECEIVED is
deliberately absent: unknowable without a Google/Yelp integration, and
inventing it would be the dashboard's first fabricated number."* That rule
does not change here. Reviews cannot verify a real Google review happened;
there is no Google/Yelp API integration in scope (and building one would be
new infrastructure, explicitly out of bounds). "If the customer already
reviewed, stop" and "successful reviews" analytics can only ever mean
**self-reported** — the customer's own reply saying so — never a verified
count. The plan below is explicit about this distinction everywhere it
matters, so nothing built here quietly becomes a fabricated metric later.

---

## 1. Responsibilities (restated precisely, against what's buildable)

1. Detect a completed job — **already solved** (`Job.completed_at`).
2. Wait an appropriate amount of time before the first ask — **new**, a
   per-business delay (config, not a fixed constant — see §4).
3. Send a friendly review request — **exists, needs to move** off the
   synchronous request path onto a delayed one.
4. If the customer says (in a reply) they already left a review, stop —
   **new**, self-reported only (see honesty note).
5. If no reply within a window, send exactly one follow-up — **new**.
6. Never spam — enforced by construction: at most two outbound touches per
   job, ever (initial + one follow-up), each gated by a "not yet sent" field
   so a re-run can never double-send, and stops immediately on any reply
   that isn't clearly "still nothing, keep waiting."
7. Record every interaction — **new**, a dedicated table (mirrors
   `ReferralLead`), not just the two `Job` timestamp columns, since a
   timestamp alone doesn't capture *what the customer said back*.
8. Analytics for requests / responses / successful reviews / follow-ups —
   **new** metric keys, each backed by a real, drill-down-able record per
   the drill-down invariant — see §5.

---

## 2. State transitions

State lives on `Job` (the two existing/new timestamp columns) plus one new
table for the reply record. No new status enum needed — the timestamps
*are* the state machine, the same pattern `Job.completed_at`/
`review_requested_at`/`owner_alerted_at` already use elsewhere (never a
separate `status` string to keep in sync with a set of nullable
timestamps that already encode the same thing unambiguously).

```
Job completed (completed_at set)
        |
        v
  [waiting: too soon to ask yet]
        |  (review_delay_days elapsed, review_requested_at still null)
        v
  REQUESTED (review_requested_at set) ---- customer replies ----> RESPONDED
        |                                                        (ReviewReply row exists)
        |  (follow_up_delay_days elapsed since review_requested_at,
        |   review_followup_sent_at still null, no ReviewReply row yet)
        v
  FOLLOWED UP (review_followup_sent_at set)
        |
        v
  DONE — no further action regardless of what happens after (never a
         third touch; a late reply after this point is still recorded,
         just doesn't trigger anything further)
```

Every transition is a plain `Job.<field> is not None` check — no ambiguity,
no separate table to keep in sync with `Job`, matching the existing
`review_requested_at`/`owner_alerted_at` idiom exactly.

---

## 2a. Negative-sentiment handling (added 2026-07-30, founder-approved)

**The problem this closes:** a customer who replies to a review ask with
real dissatisfaction ("the tech was late and rude," "this didn't even fix
the problem") must never be nudged toward the public review link — doing
so is how a business accidentally solicits its own bad public review.
Sending an unhappy customer a review link is worse than sending nothing.

**Classification.** `outcome` on `ReviewReply` gains a fourth value:
`"left_review" | "declined" | "negative" | "unclear"`. Classification is
Claude's job via the SAME small reply-handling call already planned in §5
(`RECORD_REVIEW_REPLY_TOOL`) — one more enum value, not a second model
call or a second tool. The prompt instructs: read the reply for genuine
dissatisfaction (a complaint about the work, the technician, or the
outcome — not just "no thanks" to the review ask itself, which is
`"declined"`, a neutral, non-alarming outcome) and classify accordingly.

**What happens on `"negative"`, concretely:**
- The reply back to the customer acknowledges the concern and says the
  owner will follow up directly — never the review-request link, never a
  cheerful "thanks anyway."
- The owner is alerted immediately — reusing `notifications.
  notify_owner_of_escalation` + `record_owner_notification` exactly as
  Frontdesk's `alert_owner` tool already does, with `KIND_ESCALATION` (the
  existing kind — this genuinely is an escalation, the same concept
  Frontdesk already alerts on) and a **new** `SOURCE_NEGATIVE_REVIEW_REPLY`
  constant in `notifications.py` (the operational axis that already
  exists specifically so different escalation origins can be told apart
  without parsing the message body — `SOURCE_ALERT_OWNER` is Frontdesk's
  live-call escalation; this is Reviews', not the same origin so not the
  same source).
- The sequence stops the same way `"left_review"` stops it — no follow-up
  is ever sent once `ReviewReply` exists for a job, regardless of outcome.
  "Never spam" applies to an unhappy customer at least as much as a happy
  one.
- Recorded honestly: `ReviewReply.outcome == "negative"` is itself a real,
  drill-down-able record (§6 gains a metric key for it — see below), never
  silently folded into "declined" where the founder would lose the signal
  that something specifically went wrong on that job.

**New metric, added to §6's table:** `REVIEW_NEGATIVE_REPLIES` (or the
founder's preferred label — "Customers who flagged a problem") backed by
`reviewreply where outcome == 'negative'`. This is a founder-facing
early-warning signal, not a vanity metric — it belongs on the dashboard
precisely because it's actionable, unlike a metric that only counts
happy-path activity.

**No architecture change:** this reuses the exact same reply-handling call,
the exact same tool-based classification pattern, and the exact same
owner-notification functions already planned for the rest of Reviews (and
already used by Frontdesk) — one more enum value and one more `SOURCE_*`
constant, not a new mechanism.

---

## 3. Trigger events — which existing mechanism fires each step, and why

Two different existing mechanisms already cover the two different shapes
of "when does something happen" here — no new one is needed:

- **Steps 2–3 and 5 (wait, ask, follow up) are deferred and time-based.**
  `runner.py`'s `dispatch_job_completed`/`JOB_COMPLETED_ROLES` fires
  **synchronously, at the instant `complete_job` runs** — the wrong shape
  for "wait N days," since nothing should sleep inside an HTTP request
  handler. The already-established mechanism for exactly this shape is
  `recovery_tick.py`'s cron pattern: a `tick(session)` function, run once a
  day, that queries for jobs whose wait threshold has already elapsed
  (`referral_service.send_due_referral_asks` is the closer precedent —
  single/dual-touch, not Recovery's full N-day sequence engine). Reviews
  should add its own `send_due_review_requests(session)` /
  `send_due_review_followups(session)` to this same tick, registered in
  `recovery_tick.py` next to the existing two calls. **This is a deliberate
  choice between two existing extension points, not a new one** — worth
  stating plainly since `runner.py`'s docstring explicitly invites "the
  first job-completion employee" to register there, and Reviews is
  arguably that employee by name, but its *cadence* doesn't fit that
  hook's *synchronous* shape. `JOB_COMPLETED_ROLES` stays empty; that's
  fine and correct, not a leftover.
- **Reply handling is inbound-webhook-triggered**, exactly like Referral's.
  `app.py`'s `_process_inbound_sms` already chains
  `find_active_recovery_job` → `find_active_referral_ask` → plain
  Frontdesk. Reviews adds one more link in that same chain:
  `find_active_review_ask` → `handle_review_reply`, inserted after
  Referral (order doesn't matter much between Referral and Reviews since
  their active-window checks are already mutually exclusive by
  construction — a given inbound thread can only match one outstanding
  ask at a time in practice, but Referral first preserves the existing
  order and touches nothing about it).

No EventBus involvement either way: `runner.py`'s own docstring explains
why (`eventbus.bus`'s singleton binds an engine at import time; every
existing job-completion-adjacent service already takes an explicit
`session` instead, matching `recovery_service`/`referral_service`).
Reviews follows the same convention for the same reason — not a new
decision, the established one.

---

## 4. Data required

**New `Job` columns** (same migration pattern as every prior addition —
`db._migrate_add_columns`, nullable, no backfill needed):
- `review_followup_sent_at: Optional[datetime] = None`

(`review_requested_at` already exists — reused as-is, not renamed.)

**New config, on `Business`** (mirrors `referral_engine.REFERRAL_DELAY_DAYS`
/ `REFERRAL_REPLY_WINDOW_DAYS` being module constants, not per-business
columns — the same question applies here: should the wait/follow-up
windows be a shared constant or a per-business setting?):
- Recommend **module constants** in a new `review_engine.py`
  (`REVIEW_DELAY_DAYS`, `REVIEW_FOLLOWUP_DELAY_DAYS`), matching Referral's
  precedent exactly, rather than new `Business` columns. Nothing in the
  request asks for per-business tuning, and Referral (the closest sibling
  feature) doesn't have it either — adding it would be scope the task
  didn't ask for. If a real customer later needs a different cadence,
  that's a one-line constant change or a follow-up column, not a blocker
  now.

**New table** — `ReviewReply`, mirroring `ReferralLead` field-for-field in
spirit:
```python
class ReviewReply(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    business_id: int = Field(foreign_key="business.id")
    source_job_id: int = Field(foreign_key="job.id")
    customer_phone: str
    # Self-reported only — never a verified Google/Yelp review. See the
    # honesty note in §0. Claude reads the reply and classifies it; "left_review"
    # is the customer's own claim, not a fact this system can check.
    # "negative" (§2a) is a genuine complaint about the work/technician/outcome
    # — distinct from "declined" (a neutral no to the review ask itself).
    outcome: str  # "left_review" | "declined" | "negative" | "unclear"
    raw_reply_text: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
```
This is the record `find_active_review_ask` checks for "already resolved"
(exactly like `find_active_referral_ask` checks for an existing
`ReferralLead`), and the record the analytics below drill into.

**Reused as-is, no changes:** `Customer` (identity — already resolved via
existing `Job.customer_id`/`customer_phone`/`callback_number`, no new
lookup needed), `Message` (not used for the review SMS thread today and
doesn't need to be — the review ask/reply are logged via `ReviewReply` +
the existing SMS webhook's own message logging, the same way Referral's
reply doesn't get double-logged into a parallel table either), `channels.py`
(`get_channel()` — identical send path every other SMS-sending module uses).

---

## 5. Existing components to reuse (explicit map, nothing new invented)

| Need | Reused from | Not built new |
|---|---|---|
| "Job completed" signal | `Job.completed_at` (already exists) | no new event/flag |
| Deferred, wait-then-send cadence | `recovery_tick.py` cron pattern, `referral_service.send_due_referral_asks` shape | no task queue, no scheduler |
| Reply routing | `app.py`'s existing `_process_inbound_sms` chain (`find_active_recovery_job` → `find_active_referral_ask` → ...) | no new webhook, no new route |
| Reply interpretation | `engine.AgentEngine.respond()` with a small dedicated tool + prompt, exactly `referral_engine.RECORD_REFERRAL_TOOL`/`build_referral_reply_prompt`'s shape | no new agent/model plumbing |
| Idempotent, safe-to-rerun sends | `review_requested_at`/`review_followup_sent_at is None` gates, same idiom as `referral_sent_at` | no new locking primitive (Referral's simpler check-then-set is the right precedent here, not Recovery's atomic-claim — Reviews is 2 touches, not an N-day sequence under real concurrency pressure) |
| Outbound send | `channels.get_channel()` | no new channel |
| Deployment/active check | `runner.is_active(session, business, "reviews")` (already exists, generic) | no new "is this employee on" check |
| Durable interaction record | new `ReviewReply` table, same shape as `ReferralLead` | no reuse of `ReferralLead` itself — different domain, own table, same *pattern* |
| Analytics / drill-down | `metrics.py`'s `METRIC_RECORDS`/`EMPLOYEE_RECORDS["reviews"]` — add new keys the same way `REFERRALS_RECEIVED` was added for `ReferralLead` | no new dashboard mechanism, no new registry shape |
| Founder deployment | `employees.py`'s existing `"reviews"` entry — flip `status` from `internal` to `live` once validated, exactly the "internal is not a resting state" rule the file already documents | no new registry entry needed, it exists |

**Net new files:** `review_engine.py` (pure constants/template/tool schema/
prompt-builder — mirrors `referral_engine.py` exactly), `review_service.py`
(the DB-touching functions — mirrors `referral_service.py` exactly). Net
new DB objects: one column (`Job.review_followup_sent_at`), one table
(`ReviewReply`). Everything else is composition of what's already there.

**Architecture verdict: no changes needed.** Every piece above is an
existing pattern applied to a new domain, not a new pattern. The one real
design decision — tick-based cadence vs. `runner.py`'s synchronous hook —
is a choice between two things that already exist, resolved in §3, not a
third thing invented.

---

## 6. Analytics — the four requested, each honestly grounded

Following `METRIC_RECORDS`'s existing declare-the-real-rows discipline:

| Metric key | Backing rows | Notes |
|---|---|---|
| `REVIEW_REQUESTS_SENT` (exists) | `job where review_requested_at is not null` | unchanged |
| `REVIEW_FOLLOWUPS_SENT` (new) | `job where review_followup_sent_at is not null` | same shape as the metric above, one column over |
| `REVIEW_REPLIES_RECEIVED` (new) | `reviewreply` (any outcome) | mirrors `REFERRALS_RECEIVED`'s `referrallead`-backed pattern exactly |
| `REVIEWS_SELF_REPORTED` (new — deliberately NOT named "reviews received" or "successful reviews") | `reviewreply where outcome == 'left_review'` | Naming matters here: calling this "successful reviews" on a dashboard would silently misrepresent an unverified customer claim as a confirmed outcome. Recommend the founder-facing label make the self-reported nature explicit (e.g. "Customers who said they left a review") rather than presenting it as equivalent to a real Google review count. |
| `REVIEW_NEGATIVE_REPLIES` (new, §2a) | `reviewreply where outcome == 'negative'` | An early-warning signal, not a vanity metric — a founder should see this and act on it, not just tally it. |

All four are real, queryable, drill-down-able rows — no invented numbers,
consistent with the drill-down invariant this whole platform already holds
every other metric to.

---

## 7. Tests (planned, not written)

**`review_service.send_due_review_requests`:**
- a completed job past `REVIEW_DELAY_DAYS`, no request sent yet, with a
  `review_link` and `callback_number` set → sends, sets
  `review_requested_at`.
- a completed job NOT yet past the delay → not sent.
- a completed job with `review_requested_at` already set → not re-sent
  (idempotent re-run).
- a business with no `review_link` set → not sent (mirrors Referral's
  `client.referral_incentive` gate).
- a job with no `callback_number` → not sent (nothing to text).
- calling the function twice in a row sends exactly once (safe-to-rerun,
  matching the recovery_tick.py docstring's own guarantee).

**`review_service.send_due_review_followups`:**
- a requested-but-unanswered job past `REVIEW_FOLLOWUP_DELAY_DAYS`, no
  `ReviewReply` row → sends the one follow-up, sets
  `review_followup_sent_at`.
- a requested job that already HAS a `ReviewReply` (any outcome) → no
  follow-up sent, even if the delay has elapsed — this is the "never spam"
  guarantee's core test.
- a job whose follow-up was already sent → never sent twice.
- a job not yet past the follow-up delay → not sent yet.

**`review_service.find_active_review_ask` / `handle_review_reply`:**
- a reply within the active window, no prior `ReviewReply` → routes to
  Reviews, not plain Frontdesk.
- a reply after the active window has closed → does NOT route to Reviews
  (falls through, same time-bounded logic as `find_active_referral_ask`,
  with the same reasoning: an ask with no ongoing "status" needs a bound or
  an unrelated message months later would still match).
- customer says something equivalent to "already left one!" → creates a
  `ReviewReply(outcome="left_review")`; a subsequent tick run does NOT send
  a follow-up for this job (cross-checks against the "never spam" test
  above from the other direction).
- customer says something that doesn't look like a review confirmation
  (asks an unrelated question, declines, or something ambiguous) →
  `ReviewReply` still recorded (raw text never lost, mirrors
  `handle_referral_reply`'s own guarantee), `outcome` reflects "declined"
  or "unclear" as appropriate, a real reply back to the customer either
  way (never silence unless the trial cap is exhausted, matching
  `handle_referral_reply`/`handle_recovery_reply`'s existing trial-cap
  fallback behavior).
- customer expresses genuine dissatisfaction (§2a) → `ReviewReply(outcome=
  "negative")` created; `notify_owner_of_escalation` + `record_owner_
  notification(kind=KIND_ESCALATION, source=SOURCE_NEGATIVE_REVIEW_REPLY)`
  both called exactly once; the reply sent back to the customer does NOT
  contain the review link; a subsequent tick sends no follow-up for this
  job (same "already resolved" check as `left_review`).
- customer merely declines the review ask itself ("no thanks," "not right
  now") → classified `"declined"`, NOT `"negative"` — must not conflate a
  neutral no with a complaint; no owner alert fires for a plain decline.
- a genuinely negative reply must never include the review link anywhere
  in the text sent back — a direct regression test on the reply body, not
  just the recorded `outcome`.
- a SECOND reply on an already-resolved ask (e.g. they already said "done"
  and text again later) → no duplicate `ReviewReply`, or handled the same
  ambiguous-but-harmless way Referral/Recovery already handle a stray late
  reply (needs one explicit test either way, not left implicit).

**Metrics / drill-down (mirrors the existing `test_metrics.py` pattern for
every other metric):**
- `REVIEW_FOLLOWUPS_SENT` count matches real `review_followup_sent_at`
  rows.
- `REVIEW_REPLIES_RECEIVED` count matches real `ReviewReply` rows,
  regardless of outcome.
- `REVIEWS_SELF_REPORTED` count matches only `outcome == "left_review"`
  rows — a mixed set of left_review/declined/unclear replies must not
  over- or under-count.
- business-isolation test for every new query (the platform-wide security
  boundary — same test shape every other metric/service function already
  has).

**End-to-end regression** (mirrors the departments-migration's own
"deploy → 303 → follow → staffed" e2e pattern): mark a job done → advance
time past `REVIEW_DELAY_DAYS` → run the tick → assert the SMS sent and
`review_requested_at` set → simulate an inbound "left_review" reply →
assert `ReviewReply` created and a subsequent tick sends no follow-up.
And the inverse: no reply → advance past `REVIEW_FOLLOWUP_DELAY_DAYS` → tick
→ assert exactly one follow-up sent, never a second one on a further tick.

---

## Summary

Nothing here requires a new subsystem, a new department, a new dispatch
mechanism, or a schema pattern that doesn't already exist elsewhere in this
codebase. Reviews is Referral's shape (simple, dual-touch, tick-driven,
reply-aware) applied to a different trigger (`completed_at` instead of
"job completed + incentive set") and a different table
(`ReviewReply` instead of `ReferralLead`). The one thing this plan
deliberately does NOT build — a verified "review received" count — is a
Google/Yelp integration, explicitly out of scope, and the plan is explicit
everywhere that "successful reviews" can only ever mean self-reported until
that integration exists.
