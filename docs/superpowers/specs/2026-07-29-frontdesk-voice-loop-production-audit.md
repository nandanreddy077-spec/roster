# Frontdesk Voice Loop — End-to-End Production Reliability Audit

**Date:** 2026-07-29
**Scope:** the live xAI Realtime voice path only (`xai_voice_adapter.py`,
`app.py`'s `/webhook/xai-incoming-call`, `call_trace.py`, and the shared
modules it touches: `bookings.py`, `memory.py`, `notifications.py`,
`metrics.py`). The SMS Frontdesk path (`service.py`) is referenced only
where the two paths share code or diverge in a way that matters.

**Explicitly not in scope:** no architecture change is proposed anywhere in
this document. Every recommendation is a test, a verification step, or a
small code fix inside the existing design — consistent with
[[engineering-priorities-2026-07-29]] and `ARCHITECTURE.md` (which this
audit does not touch: no new workspace, invariant, layering, registry, or
state-derivation change is proposed).

**How this reads:** one section per pipeline stage, each with the same five
subsections the founder asked for. A final section separates every
recommendation into three buckets: things to verify against real production
traffic, small code fixes, and (explicitly, here: none) architectural
changes.

---

## 1. Inbound phone call

**What currently happens.** A call lands on a number registered with xAI's
Grok Voice Agent API (via a Twilio SIP trunk — see `agent/README.md`). xAI
fires `POST /webhook/xai-incoming-call` with a `realtime.call.incoming`
event. `app.py`'s handler:
1. Parses the body defensively (`parse_xai_incoming_call` returns `None` for
   anything that isn't a well-formed `realtime.call.incoming` with a
   filename-safe `call_id`).
2. Captures the raw headers+body to a fixed pre-auth quarantine file
   (`_inbound.jsonl`) regardless of validity — `CallTrace.capture_unverified`.
3. Looks up the `Business` by the dialed number (`xai_phone_number`); unknown
   number → `204`, dropped.
4. Fails closed if that business has no stored `xai_signing_secret` → `401`.
5. Rejects a stale `webhook-timestamp` (>300s old) → `401` (anti-replay).
6. Verifies the Svix-style signature with that business's own secret → `401`
   on mismatch.
7. Claims the call atomically via a unique `WebhookDelivery.dedup_key =
   "xai-call:{call_id}"` insert — a duplicate delivery loses the `IntegrityError`
   race and is dropped with `200`.
8. Schedules `run_call(...)` as a tracked `asyncio.create_task` (strong
   reference held in `_active_call_tasks` so it can't be GC'd mid-call) and
   returns `200` immediately — the webhook response is never blocked on the
   call itself.

**Never verified in production.** Everything about xAI's actual wire
behavior: whether the signing secret is really `whsec_`-prefixed base64,
whether `webhook-signature` really contains space-separated `v1,<sig>`
entries (both flagged as unconfirmed directly in `xai_voice_adapter.py`'s
module docstring), the real shape of `sip_headers`, and — most basically —
whether a real inbound call reaches this webhook at all given the current
Twilio-SIP-trunk-to-xAI configuration.

**Realistic failure modes.**
- The signing-secret format assumption is wrong → every real call gets a
  `401` and is silently dropped from the caller's perspective (dead air or a
  SIP failure tone) with zero user-facing symptom in our system beyond a
  `signature_failed` trace line nobody is watching (see §12).
- `sip_headers` doesn't carry `To`/`From` under those exact names → parsed as
  `None` → `204`, same silent drop.
- The pre-auth quarantine file (`_inbound.jsonl`) has no size cap or
  rotation — a public, unauthenticated endpoint that anyone can flood with
  garbage POSTs to grow one file without bound (low-severity but real disk-
  fill risk).

**Recommended tests.**
- A production verification, not a unit test: place one real call and diff
  the captured `_inbound.jsonl` payload against what `xai_voice_adapter.py`'s
  docstring currently assumes.
- Unit test (once the real payload is confirmed): pin the confirmed
  `sip_headers` shape as a fixture so a future xAI API change is caught by a
  fixture diff, not a silent production drop.
- Add a startup or periodic check that alerts if `_inbound.jsonl` exceeds a
  size threshold.

---

## 2. Conversation lifecycle

**What currently happens.** `run_call` opens exactly one WebSocket to xAI's
Realtime API for the call's whole duration, sends a `session.update` (system
prompt + tools) and a `response.create`, then loops `async for raw in ws`
until the socket closes, dispatching on `event["type"]`. `response.done` marks
the first-AI-response trace stage and persists the assistant's transcript.
Any event type the loop doesn't recognize is traced once as
`unexpected_event` (per type, so a noisy stream doesn't spam the trace) and
otherwise silently ignored. The loop ends when xAI closes the socket
(`call_completed`).

**Never verified in production.** The complete real event stream for one
call — the `websockets` library's default ping/pong keepalive (20s
interval/timeout) is assumed to be sufficient to detect a dead connection,
but this has never been exercised against xAI's actual connection behavior.
Whether `response.function_call_arguments.done` is really the correct event
name/shape for a tool call (matches OpenAI-Realtime's convention, per the
docstring, but xAI's docs weren't fully explicit at time of writing).

**Realistic failure modes.**
- **No maximum call duration.** Nothing bounds how long a single call (and
  its `asyncio` task) can run. A caller who never hangs up — or a bug in
  xAI's endpoint that never closes the socket — pins that task open
  indefinitely, consuming a WebSocket connection and (per §11) unmetered
  xAI Realtime API cost the whole time.
- **User speech is never captured.** The loop only handles
  `response.function_call_arguments.done` and `response.done` (assistant
  side). Whatever event type carries the *caller's* transcribed speech is
  not handled at all — it silently falls into the "unexpected event, traced
  once" branch. The customer's own words are never persisted to `Message`,
  anywhere. This is the single biggest completeness gap in this audit (see
  also §8, dashboard timeline).
- A tool-call event with a malformed/missing `arguments` field
  (`json.loads(event["arguments"]) if event.get("arguments") else {}`)
  degrades to an empty dict rather than failing loudly — acceptable
  defensively, but there's no trace signal distinguishing "the model sent no
  args" from "the args were unparseable JSON" (`json.loads` would raise on
  the latter, which is currently uncaught inside the loop and would be
  treated as a full call crash by `run_call`'s top-level handler rather than
  a recoverable per-event issue).

**Recommended tests.**
- Once the real "user speech" event type is confirmed against a production
  call, add a fake-WS integration test (same style as
  `test_voice_loop_integration.py`) asserting the caller's transcript is
  persisted as a `Message` with `role="user"`.
- A fake-WS test with a malformed `arguments` string (not valid JSON) proving
  the loop's behavior today (full call crash via the top-level handler) is
  the intended behavior, or driving a fix if it isn't.
- No unit test can verify the real ping/pong keepalive story — this is a
  production-verification item: watch one long-idle real call (or
  deliberately go silent on a test call) and confirm the socket actually
  closes within the expected window rather than hanging forever.

---

## 3. Memory updates

**What currently happens.** Before connecting to xAI, `run_call` calls
`memory.build_customer_context(session, business_id, caller_number)`, which
looks up a `Customer` row by `(business_id, phone)` and up to 3 recent `Job`
rows matching `customer_phone == phone OR callback_number == phone` — so a
repeat caller's context is found by phone-number match even without a
`Customer` row exactly linked to a booking. Free-text fields (name, service
type) are run through `_clean_field` (control-character stripping,
newline collapsing, length cap) specifically to prevent a stored customer
name from acting as a prompt-injection vector inside the live system prompt.
Returned context is folded into `build_session_update`'s `instructions`
before the very first `response.create`.

**Never verified in production.** Whether the injected context actually
changes the live model's behavior the way the SMS path's equivalent context
does — this has only been checked with `build_session_update`'s output
shape, never against a real xAI Realtime session actually *using* the
"Returning customer: ..." text naturally in conversation.

**Realistic failure modes.**
- None found that are unique to voice beyond what §4 (customer
  identification) already covers — `build_customer_context` itself is
  business-scoped and shared with the SMS path, which is well-tested.
- A subtler one: because voice-booked jobs are never linked to a `Customer`
  row (§4), `build_customer_context`'s phone-match fallback is *only* path
  that ever surfaces a returning voice caller's history — if a future change
  ever switches this lookup to `Customer`-first, voice callers with no
  `Customer` row would silently stop being recognized.

**Recommended tests.** None beyond what §4 recommends — this stage is
sound *given* §4's finding is fixed; it degrades quietly, not loudly, if
customer identification stays broken (worth a comment in the code, not a
new test).

---

## 4. Customer identification

**What currently happens.** The SMS path (`service.py`) calls
`repositories.get_or_create_customer(session, client.id, customer_phone,
name)` before booking, so every SMS-booked job is linked via `customer_id`.
**The voice path never calls this function anywhere.** `_persist_job` in
`xai_voice_adapter.py` calls `book_job(session, client, thread, caller_number,
args)` with no `customer_id` argument — `book_job`'s signature accepts one
(`customer_id: Optional[int] = None`), but it's never passed on this path.

**Never verified in production.** N/A — this is not an unconfirmed
assumption, it's a directly-readable code gap: grep confirms zero calls to
`get_or_create_customer` in `xai_voice_adapter.py`.

**Realistic failure modes.**
- Every voice-only customer has no `Customer` row, ever — ***confirmed***,
  not hypothetical. Any current or future feature that joins on `Customer`
  (Retention Manager's "customers who came back," Reviews, any future
  Customer Success outcome) silently undercounts or fully misses every
  customer who has only ever called, never texted.
- `Message` rows created on the voice path (`_run_call_session`'s
  `assistant`-role transcript insert) also never set `customer_id` — same
  gap, one level down.
- Combined with §2's "user speech never captured" finding: a voice-only
  repeat customer has essentially no durable identity or transcript trail
  outside of `Job` rows matched by phone number.

**Recommended tests.**
- A fake-WS integration test (extending `test_voice_loop_integration.py`)
  asserting that after a `log_job` tool call, a `Customer` row exists for
  the caller's number and the resulting `Job.customer_id` points to it — this
  test should currently **fail**, which is the point: it pins the gap before
  it's fixed.
- A regression test that a second call from the same number reuses the
  existing `Customer` row rather than creating a duplicate (mirrors
  `get_or_create_customer`'s existing SMS-side guarantee, exercised through
  the voice path once fixed).

---

## 5. Job creation & 6. Booking

*(Grouped: on this path these are the same code — `bookings.book_job`,
called by `xai_voice_adapter._persist_job` for `log_job`, and inlined
directly for `alert_owner`.)*

**What currently happens.** `log_job` → `_persist_job` → `book_job`, which
upserts: it looks for an *open* (`completed_at IS NULL`), *same-thread*,
*same-service-type* `Job` created within the last 24h, merges new details
into it if found, else inserts a new row. This is retry-safe by
construction (webhook redelivery, LLM re-calling `log_job` mid-conversation
with more details). `alert_owner` (escalation) takes a **different, simpler
path**: it constructs and commits a `Job` directly, with **no upsert, no
dedup window, no idempotency check of any kind**.

**Never verified in production.** Whether xAI's Realtime API can ever
redeliver the same `response.function_call_arguments.done` event within a
single WebSocket stream (the transport-level equivalent of an at-least-once
webhook, but inside one call rather than across separate HTTP requests) —
unconfirmed, and would matter more for `alert_owner` given its total lack of
protection.

**Realistic failure modes.**
- **`alert_owner` has no duplicate protection at all** — confirmed by
  reading the code, not hypothetical. If the model calls `alert_owner` twice
  in one call (plausible: a caller restates urgency, the model second-
  guesses whether the first call "took," or a transport-level redelivery
  occurs), the result is **two** `Job` rows and **two** urgent SMS sends to
  the owner for what may be the same incident. This is a direct customer-
  facing-quality issue (an owner who gets paged twice for one emergency
  starts distrusting the alerts) and is the single most concrete
  duplicate-protection gap in this audit.
- `book_job`'s 24h/same-thread/same-service dedup window is well-tested
  generally (`test_idempotency.py`), but has never specifically been
  exercised through the *voice* call path with a scripted double `log_job`
  event — only through `service.py`'s SMS path and directly.

**Recommended tests.**
- A fake-WS test scripting **two** `ALERT_OWNER_EVENT`s in one call, proving
  today's behavior (two `Job` rows, two owner texts) — again, a test that
  should fail once a fix lands, useful now as a precise specification of the
  gap.
- A fake-WS test scripting two `log_job` events for the *same* service in
  one call, confirming `book_job`'s merge behavior holds through the voice
  path specifically (not just through direct `book_job` unit tests).

---

## 7. Notifications

**What currently happens.** `notify_owner_of_booking` /
`notify_owner_of_escalation` send a best-effort SMS via `channels.py`'s
`TwilioChannel`/`ConsoleChannel`; `record_owner_notification` durably logs
the attempt (with `delivered` reflecting the real send outcome) regardless
of whether the SMS itself succeeded — this durable log is exactly what
Phase 5's Notifications page reads. The booking-notification send is
offloaded via `asyncio.to_thread` so a blocking Twilio HTTP call never stalls
other concurrent calls' audio on the same event loop; the DB write for the
log happens back on the original thread with the already-open `Session`
(correctly, since `Session` isn't thread-safe).

**Never verified in production.** Real end-to-end latency of the
`asyncio.to_thread` handoff under concurrent live calls — this is a
plausible-but-unmeasured contention point if many calls book simultaneously
on one process.

**Realistic failure modes.** None new beyond what §6 already covers
(`alert_owner`'s duplicate-notification risk) — this stage itself is
well-covered (`test_notify_owner_of_escalation_never_raises`,
`test_a_failed_owner_sms_still_records_an_undelivered_notification`, etc.)
and already honors the durability guarantee correctly.

**Recommended tests.** None new — this stage is already the most
thoroughly tested part of the whole loop. Worth re-running
`test_owner_notification_log.py`'s existing suite specifically after any fix
to §6, since a duplicate-protection fix there changes what "created" means
for the escalation path and could interact with this stage's own tests.

---

## 8. Dashboard timeline

**What currently happens.** The customer dashboard's Employee Workspace for
Frontdesk (built in the departments migration) renders activity from
`metrics.employee_activity`, which for `frontdesk` sources three
`RecordSource`s: `_jobs` (booked jobs), `_voice_conversations` (one
"Answered a call" row per distinct `xai-voice:*` thread's first message —
i.e. per call, not per customer), and `_escalations` (`OwnerNotification`
rows of kind `escalation`/`call_dropped`). The founder's own console reads
the same underlying `Job`/`Message`/`OwnerNotification` rows through the
same functions (the shared-helper invariant holds here).

**Never verified in production.** Whether this activity feed reads as
coherent and useful once real call volume exists — right now it's only been
exercised with synthetic single-call test fixtures.

**Realistic failure modes.**
- Directly downstream of §2 and §4: since the caller's actual words are
  never captured and voice `Job`/`Message` rows never carry `customer_id`,
  the timeline can show *that* a call happened and *what* got booked, but
  never *what the caller actually said* — for both the founder's own
  debugging and any future "listen back" or QA feature.
- Each call is its own thread (`xai-voice:{call_id}`), so a customer who
  calls three times shows as three unrelated "Answered a call" activity
  rows with no way to group them by actual customer identity today — a
  direct consequence of §4.
- There is no equivalent of the SMS path's `portal-test` safe test thread
  for voice. A founder cannot place a "test call" against a live number
  without it landing as a real `Job`/`OwnerNotification`/dashboard activity
  row indistinguishable from a genuine customer call.

**Recommended tests.** No new automated test recommended here specifically
— this stage's correctness is entirely a function of §2/§4 being fixed.
Worth a manual production-verification pass once those land: place a real
call, confirm the resulting activity row set reads sensibly in both the
founder console and the customer dashboard.

---

## 9. Error handling

**What currently happens.** `run_call` wraps the entire call session in one
top-level `try/except Exception`: any unhandled exception anywhere in
`_run_call_session` (a bad WS payload, a DB error, a crashed tool handler)
is caught, traced as `call_failed`, and answered with a best-effort
"the call dropped, call them back" SMS to the owner plus a durable
`OwnerNotification` (kind `call_dropped`) — written on a **fresh** session
(deliberately: the crashed call's own session isn't safe to reuse).
`supervise_call_task` additionally logs any exception that escapes the
`asyncio.Task` itself to stderr, so a crash can never vanish as a bare
silently-swallowed task.

**Never verified in production.** Whether this recovery path has ever
actually fired against a real failure — it's thoroughly unit-tested
(`test_run_call_survives_ws_drop_and_alerts_owner`) but only against a
scripted `ExplodingWS`, never a genuine xAI-side failure.

**Realistic failure modes.**
- **All-or-nothing recovery granularity.** The `try/except` wraps the
  *whole* call. A single bad tool-call event (e.g. malformed `arguments`
  JSON, or a `book_job` DB hiccup) kills the *entire remaining conversation*
  rather than just failing that one tool call — the caller is left with
  dead air (the socket is still open from xAI's side until it times out or
  closes) while the owner gets a "call dropped" text as if the whole call
  failed, when in fact the model might have kept going fine if only that one
  tool call had been handled more narrowly.
- No distinction in the owner-facing message between "the call cleanly
  ended" and "the call crashed mid-sentence" beyond the one generic
  "dropped mid-call" phrasing — acceptable for now, but worth naming as a
  UX ceiling rather than assuming it's fully satisfying.

**Recommended tests.**
- A fake-WS test that raises inside `_handle_function_call` specifically
  (not the WS transport) and asserts today's behavior: the whole call ends,
  `call_failed` is traced, the owner is texted. This documents the current
  "no per-tool-call recovery" behavior precisely, as a baseline for deciding
  whether to narrow the catch later.

---

## 10. Duplicate protection

**What currently happens.** Three independent layers exist today: (a) the
webhook layer's `WebhookDelivery` unique-`dedup_key` claim on
`xai-call:{call_id}` (protects against a redelivered `realtime.call.incoming`
spawning a second live session for the same call); (b) `book_job`'s
same-thread/same-service/24h merge window (protects `log_job` re-calls); (c)
nothing at all for `alert_owner` (§6).

**Never verified in production.** Whether xAI's Svix-convention delivery is
genuinely at-least-once for `realtime.call.incoming` the way the code
assumes (the docstring notes this is the Svix convention other providers
use, but xAI's own docs weren't fully explicit on redelivery semantics at
time of writing).

**Realistic failure modes.** Already covered in full in §6 —
`alert_owner`'s complete lack of protection is the standout finding of this
entire audit.

**Recommended tests.** Already listed in §6 (the double-`alert_owner`
fake-WS test) plus one new test specifically at the webhook layer: **no
test today exercises a second `/webhook/xai-incoming-call` POST for the
same `call_id`** and asserts it's dropped via the `duplicate_call_id` trace
stage / the `IntegrityError` path in `app.py`. The code path exists and
looks correct on inspection, but is currently unverified by any test in the
suite — a real, closeable gap independent of any code change.

---

## 11. Webhook retries

**What currently happens.** Covered across §1 (verification/dedup),
§6/§10 (idempotent booking), and §9 (per-call crash recovery). One thing
specific to *retries* as a concept: because a live phone call has no
meaningful "replay" the way an SMS or a job-status webhook does (you can't
resend a phone call), the only retry semantics that matter here are (a) xAI
redelivering the *same* `realtime.call.incoming` notification (handled, per
§10) and (b) the model's own conversational retries within one live call
(e.g. re-calling `log_job`; handled for that tool, not for `alert_owner`).

**Never verified in production.** Whether xAI ever actually redelivers
`realtime.call.incoming` in practice (vs. this being purely a defensive
assumption inherited from the general Svix convention).

**Realistic failure modes.** No new ones beyond §1/§6/§10.

**Recommended tests.** The webhook-layer duplicate-`call_id` test named in
§10 covers this stage completely; no additional tests needed here.

---

## 12. Observability

**What currently happens.** `CallTrace` records every stage and raw WS
event, in memory (assertable by tests) and, once a call is authenticated, to
`{capture_dir}/{call_id}.jsonl` — plus a one-line human-readable summary to
stderr for live tailing. Pre-auth traffic (valid or not) is separately
captured into a single fixed `_inbound.jsonl` quarantine file.

**Never verified in production.** Whether stderr is actually being
captured/retained anywhere useful in the real deployment (Railway) — i.e.
whether "tail the logs" is a realistic on-call workflow today or just a
local-dev affordance.

**Realistic failure modes.**
- **No dashboard or founder-console surface reads `call_captures/*.jsonl`
  at all.** Confirmed by grep — the only way to inspect a call's trace today
  is direct file access on the server. [[voice-loop-verification]]'s own
  memory describes the intended verification workflow as "founder reads
  `data/call_captures/*.jsonl`" manually — there is no built UI for this,
  which is fine for a one-time verification pass but not a sustainable
  on-call workflow once there's real call volume.
- **Unbounded log growth.** Neither `_inbound.jsonl` nor the per-call
  `{call_id}.jsonl` files have any rotation, max-size, or retention policy —
  every real call (and every piece of garbage sent to the public pre-auth
  endpoint) grows disk usage forever.
- No alerting exists on `call_failed`, `signature_failed`, or
  `dropped: no_signing_secret` trace stages beyond a stderr print — a string
  of failed/dropped calls in production would currently be invisible unless
  someone is actively tailing logs at the time.

**Recommended tests.** Observability gaps aren't well suited to unit tests;
recommend instead as production-verification/ops items (see final section).

---

## Summary, separated as requested

### Production verification (do these against real traffic, not code changes)

1. Place one real call and diff the actual `_inbound.jsonl` capture against
   `xai_voice_adapter.py`'s documented assumptions: signing-secret format
   (`whsec_`-prefixed base64?), `webhook-signature` multi-value format, and
   the exact `sip_headers` shape (§1).
2. Confirm the real event type xAI sends for the *caller's* transcribed
   speech, and the exact shape of `response.function_call_arguments.done`
   (§2).
3. Watch one long-idle real or test call to confirm the `websockets`
   library's default ping/pong keepalive actually detects a dead xAI
   connection within the expected window (§2).
4. Confirm whether xAI/Svix ever actually redelivers `realtime.call.incoming`
   in practice, to know whether §10's webhook-dedup is defending against a
   real or purely theoretical event (§1, §10, §11).
5. Once §2/§4's fixes land, place a real call and manually confirm the
   resulting dashboard timeline (founder console + customer dashboard) reads
   coherently (§8).
6. Confirm whether stderr output from the running process is actually
   captured/retrievable in the real deployment, i.e. whether tailing logs is
   a real on-call workflow today (§12).

### Code improvements (small, inside the current design — no architecture change)

1. **Give `alert_owner` the same idempotency guarantee `log_job` already has
   via `book_job`.** This is the single highest-priority fix in this audit —
   a duplicate emergency alert is a direct trust problem for the owner. (§5/§6/§10)
2. **Capture the caller's own speech as `Message` rows**, once the real event
   type is confirmed (§2/§4/§8) — currently the transcript is one-sided.
3. **Call `get_or_create_customer` on the voice path**, mirroring
   `service.py`'s SMS path exactly, so voice-booked `Job`/`Message` rows get
   a real `customer_id` (§4).
4. **Add a maximum call duration** as a simple safety timeout around the
   `async for raw in ws` loop (§2, §11 cost exposure below).
5. **Add a voice-path cost cap analogous to `trial_cap.py`'s SMS cap** —
   today a live call has zero automatic cost cutoff of any kind, unlike SMS
   turns (§2/§11 realistic-cost-risk, surfaced while reading `trial_cap.py`
   and confirming it's never imported by `xai_voice_adapter.py`).
6. **Add rotation/size limits to the call-capture JSONL files** — both the
   fixed pre-auth quarantine file and the per-call files grow forever today
   (§1/§12).
7. **Narrow the per-call exception handling** so one bad tool-call event
   doesn't silently end the entire remaining conversation for the caller
   (§9) — worth a decision, not necessarily a change, once the "single
   fake-WS test documenting today's behavior" from §9 exists.
8. **A safe voice test-call mechanism**, analogous to the SMS dashboard's
   `portal-test` thread, so a founder (or future automated check) can
   exercise a live number without producing a real-looking `Job` /
   `OwnerNotification` / dashboard activity row (§8).
9. **A founder-console view over recent call traces**, even a minimal one,
   so watching for `call_failed`/`signature_failed`/dropped calls doesn't
   require server file access (§12) — this is UI, not core loop logic, and
   deliberately listed last since it's the least urgent of the nine.

### Architectural improvements

None. Every finding above fits inside the existing design — this was a
deliberate constraint on this audit, not an oversight: the loop's overall
shape (one WebSocket per call, tool-mediated bookings/escalations through a
shared idempotent `book_job`, best-effort owner notification with a durable
log, per-call crash isolation via a top-level handler) is sound and does not
need to change. The gaps found are missing pieces and missing tests within
that shape, not evidence the shape itself is wrong.
