# Milestone B — No Silent Failures

_Status: **PROPOSAL — audit + design, no code.**_
_Author: Principal Engineer session, 2026-08-11._
_Verified against the working tree at `feat/booking-lifecycle-milestone-a` (Milestone A merged in)._

> The central finding: **this architecture already exists and works — in exactly
> one place.** `xai_voice_adapter.run_call` implements the product rule
> precisely, in three hand-written `except` branches. Milestone B is not
> inventing a framework. It is extracting the one that is already proven and
> making it the default. §6.

---

## 1. Scope correction — the audit surface is smaller than the brief

The brief asks for "every internal employee." Verified against
`employees.py`, that is:

| Employee | Registry status | Can it run in production today? |
|---|---|---|
| Frontdesk | `live` | Yes |
| Lead Qualifier | `live` | Yes |
| Dispatcher | `live` | Yes |
| Quote Chaser | `live` | Yes |
| Retention Manager | `live` | Yes |
| Reviews | `live` | Yes |
| Membership Agent | `internal` | Yes — `deploy_role` only refuses `planned` |
| **Referral** | `planned` | **No.** `deploy_role` raises, so `is_active` is always False |
| 11 others | `planned` | No engine, no route, no UI |

So: **7 employees can run, 1 has a complete engine that cannot be deployed,
11 are names in a list.** Milestone B should not build anything for the 11,
and Referral's fixes should ride along rather than lead.

---

## 2. Audit findings

### 2.1 The matrix

Nine questions × the employees that can run. **N** = no (good), **Y** = yes
(a silent-failure hole).

| | Frontdesk | Lead Qual | Dispatcher | Quote Chaser | Retention | Reviews | Membership |
|---|---|---|---|---|---|---|---|
| Can silently **stop**? | **Y** | N | N | N | N | N | N |
| Can silently **skip work**? | N | N | **Y** | N | N | **Y** | **Y** |
| Can silently **lose customer intent**? | **Y** | N | N | **Y** | **Y** | **Y** | **Y** |
| Can silently **lose owner visibility**? | **Y** | N | N | **Y** | **Y** | **Y** | **Y** |
| Can a **dependant** not know it failed? | N | N | **Y** | N | N | N | **Y** |
| Can the **customer receive silence**? | **Y** | n/a | n/a | **Y** | **Y** | **Y** | **Y** |
| Can the owner **believe work happened** when it didn't? | **Y** | N | **Y** | **Y** | **Y** | **Y** | **Y** |

Lead Qualifier is the only employee with no holes — because it never
contacts anyone and never depends on anything. That is not a virtue to copy;
it is the absence of surface area.

### 2.2 Finding A — a live customer turn can end in silence *(highest severity)*

All five reply handlers return `None` when the trial cap is exhausted, and
`inbound_sms` turns `None` into **empty TwiML** — the customer receives
nothing at all:

| Handler | Line |
|---|---|
| `service.handle_customer_message` | `service.py:96` |
| `recovery_service.handle_recovery_reply` | `recovery_service.py:383` |
| `review_service.handle_review_reply` | `review_service.py:190` |
| `referral_service.handle_referral_reply` | `referral_service.py:123` |
| `membership_service.handle_membership_reply` | `membership_service.py:356` |

Worst case, and it is not hypothetical: a Quote Chaser lead replies *"yes, I
want to book"* and gets silence. `trial_cap.py`'s own docstring already
names the blast radius — *"it silently disabled five of the six live
employees."*

**Fixing this is free.** Inbound replies leave as TwiML on the webhook
response — no outbound API call, no Twilio credentials, no per-message cost.
The current silence buys nothing.

### 2.3 Finding B — an LLM failure during a live turn is an unhandled 500 *(new, not in the previous audit)*

`agent.respond` is called in six production paths and **wrapped in none of
them**. There is no exception handler on the FastAPI app (`app.py` has no
`add_exception_handler`). So an Anthropic timeout, rate limit, or 5xx during
a live turn produces:

1. An unhandled exception → HTTP 500 to Twilio.
2. Twilio retries. `WebhookDelivery` was claimed with `response_text = None`,
   so the turn is legitimately reprocessed (`app.py:1417`).
3. If the model is still unavailable, it fails again.
4. **The customer gets nothing, and no one is told anything.**

This is a strictly worse version of Finding A: it can happen to a *paying*
customer with no cap involved.

### 2.4 Finding C — "hired but unable to work" is not expressible

Four employees do nothing, forever, with no log and no alert, while the
dashboard shows their department **staffed**:

| Employee | Missing precondition | Line |
|---|---|---|
| Reviews | `review_link` unset | `review_service.py:58` |
| Membership | `membership_plan` unset | `membership_service.py:138` |
| Referral | `referral_incentive` unset | `referral_service.py:48` |
| Dispatcher | Lead Qualifier not hired | `dispatcher_service.py:45` |

Every one is a bare `continue`. `EmployeeWorkspace` has exactly five fields
(`workspace.py:117`) and `status` derives from *deployment* state, never
health — so there is no way for the product to say "hired, blocked, here's
why."

This is the finding that most directly violates the product mission: the
owner believes they hired someone who is doing nothing.

### 2.5 Finding D — a failed owner alert is invisible

`notify_owner_*` returns `False` on failure and `record_owner_notification`
persists `delivered=False`. **Nothing reads that field.** Verified: no
production `.py` and no `dashboard_v2/*.html` references it. A failed alert
renders identically to a delivered one on the notifications page.

The codebase already documents this exactly — `db_models.py:567`: *"the only
place a FAILED send is visible at all (`delivered=False`); today a failed
owner text is swallowed by a bare `except` and is invisible everywhere."*

Two live consequences: a business with no `escalation_phone` set gets
**zero** owner alerts, ever, with no signal anywhere; and any Twilio
delivery failure (including silent A2P carrier filtering, which is the
current production state) is undetectable.

### 2.6 Finding E — terminal states are recorded, never announced

A Quote Chaser lead that runs the full 28 days with no reply becomes
`no_response` (`recovery_service.py:241`) and stops. **Zero notifications.**
The owner never learns the estimate they were chasing died — the one moment
a single phone call might still save it. Same for `declined`.

This is the revenue-side instance of the same class.

### 2.7 Finding F — voice is not trial-cap gated *(new)*

`xai_voice_adapter` never calls `can_respond` — verified, no reference to
`trial_cap` anywhere in it. So a capped business's texts go silent while its
phone line keeps answering and keeps spending. Not silence, but an incoherent
product state, and it means the cap does not actually cap the most expensive
channel.

### 2.8 Finding G — a mid-tick employee failure reaches only the log

`review_service`, `referral_service`, `membership_service` and
`recovery_service` each catch per-job send failures, log at ERROR, and
continue. Correct for resilience — but the owner is never told, and there is
no counter anywhere of "how many sends failed this tick." A systematically
broken send (expired credentials, unregistered A2P campaign) looks exactly
like a quiet day.

---

## 3. Corrections to my previous audit

Per your instruction to say so if re-reading changed a finding:

1. **"Referral captures a lead and tells nobody… never on the owner's
   dashboard"** — **partially wrong.** Referral leads *do* surface: as a
   dashboard metric (`metrics.py:261`) and on the founder console
   (`client_detail.html:253`). What is genuinely missing is any **push** —
   no SMS, no `OwnerNotification`. The claim "0 notification calls in
   `referral_service.py`" stands; "never on the dashboard" did not.

2. **"`requires_dispatch_review` → a flag nobody reads"** — **wrong as first
   stated.** It is read by `metrics.py:517` and surfaced to the owner as
   *"Flagged for your review"* (`workspace.py:76`). The accurate finding is
   narrower: it is **passive** — visible if the owner goes looking, with no
   push even for the case that matters (an emergency whose owner alert was
   never confirmed delivered).

3. **"Dispatcher's output is terminal — nothing consumes `DispatchPlan`"** —
   holds for *employees*, but `metrics.py` reads it. The phrasing in
   `docs/how-roster-works.md` should say "no employee consumes it."

Everything else from the previous audit re-verified as stated.

---

## 4. Root causes

Eight findings, **four** causes.

**RC1 — There is no "cannot work" state.** Deployment is binary: an
`Employee` row exists or it doesn't. Nothing models *hired but blocked*. Each
employee therefore open-codes its own precondition as a bare `continue`,
inconsistently, and a new employee inherits nothing.
→ Causes **C**, and the passive half of the Dispatcher dependency.

**RC2 — Silence is a legal return value on a live turn.** The reply
contract is `Optional[str]`, and `None` means "send empty TwiML." Nothing
forbids it, so every new branch may quietly produce it. The unwrapped
`agent.respond` is the same cause reached by a different route: an
exception is another way to return nothing.
→ Causes **A**, **B**.

**RC3 — Notification is best-effort with no floor.** Delivery success is
recorded and never read; failure to notify is itself unnotified. The system
can lose its own alarm channel and not know.
→ Causes **D**, **G**.

**RC4 — Reaching a terminal state carries no obligation.** Nothing in the
codebase says that a flow which has finished — successfully or not — must
inform anyone. Each employee decides ad hoc, and mostly decides not to.
→ Causes **E**, and the notification half of Referral.

---

## 5. Where I'd challenge the rule as written

### Challenge 1 — "tell the owner" without a frequency bound becomes silence

A blocked employee is blocked on *every* tick. Under the rule as stated,
Reviews with no `review_link` texts the owner hourly, forever. Within a day
the owner mutes Roster's number — and then every alert, including a genuine
emergency escalation, is functionally silent. **Alert fatigue is silent
failure with extra steps.**

The rule needs: **tell the owner once per distinct cause, and keep it
visible until resolved.** That is *state*, not a stream — which is precisely
what RC1's missing "blocked" state provides. The two requirements collapse
into one mechanism.

### Challenge 2 — "when appropriate" is the loophole that produced these bugs

Rule 3 says tell the customer *"when appropriate."* Every silent path in
Finding A was written by someone who decided it wasn't appropriate. A rule
with a judgement call in it cannot be tested, and an untestable rule decays.

Sharper, and testable: **any customer who sent a message on a live channel
always receives a reply — no exceptions.** Proactive/batch work carries no
such obligation. That bright line is enforceable by a type and a test; "when
appropriate" is enforceable by nothing.

### Challenge 3 — a fourth outcome is needed, or "deferred" becomes silence

The rule allows exactly three outcomes. Real employees have a legitimate
fourth: **deferred** — "not now, next tick." Outside send hours
(17:00–23:59 UTC only, ~7h/day), waiting on a qualification, claim lost to a
concurrent tick. All correct behaviour.

But an *indefinite* defer is exactly a silent failure: if `send_hours_ok`
were broken, every send would defer forever and nothing would ever say so.
So the model needs Deferred **with an age bound** — a defer older than N
hours is automatically promoted to Blocked and reported. Without that, the
new framework has the old bug inside it.

### Challenge 4 — the biggest risk is the framework itself

I want this on the record before we build. A framework nobody adopts is
worse than eight patches: it adds a layer *and* leaves the bugs. If we build
this, adoption must be **structural** — a test that fails when an employee
doesn't go through it — not a convention in a docstring. This codebase
already does exactly that in three places (`test_tick_deployment_gate`'s
`TICK_FUNCTIONS`, the booking single-writer scan, the nav-label test), so
the idiom is established.

---

## 6. Proposed architecture — the Employee Outcome Contract

**The architecture already exists.** `xai_voice_adapter.run_call` implements
the product rule exactly: on timeout, on budget exhaustion, and on a mid-call
crash, it texts the owner *the caller's number and an honest reason*, and
records an `OwnerNotification` (lines 628–708). Its own comment states the
principle: *"a mid-call crash must never vanish silently: record it, and text
the owner the caller's number so the human relationship survives the software
failure."*

That is the whole design. It is written three times by hand, in one file, and
nowhere else. **Milestone B generalizes it.**

### 6.1 The contract

Every employee action returns an `Outcome`, and there are exactly four
variants — the product rule's three, plus the bounded defer from Challenge 3:

```
Completed(summary)          work happened
Blocked(cause, detail)      cannot work; owner told ONCE per cause, state visible
Deferred(cause, since)      not now; auto-promoted to Blocked past a threshold
Failed(error, customer_msg) tried and broke; owner told, customer answered
```

Returning nothing is not representable. That is the point.

### 6.2 Three mechanisms, one per root cause

**M1 · Declared preconditions → `Blocked` (kills RC1).**
Each employee declares what it needs (`review_link`, `membership_plan`, a
deployed `lead_qualifier`, an `inbound_number`, an `escalation_phone`). A
shared checker runs them before the employee does. A failure produces
`Blocked` — which logs at WARNING, records **one** owner notification per
distinct cause (satisfying Challenge 1), and sets a visible blocked state
on the employee's dashboard card.

Kills: Reviews / Membership / Referral / Dispatcher silent skips, plus every
future employee's version of the same bug, because declaring preconditions
is how you get deployed at all.

**M2 · A live turn cannot return nothing (kills RC2).**
Live handlers return a `TurnOutcome` carrying a **required** customer-facing
message. The cap path returns an honest fallback instead of `None` — free,
since it rides the TwiML response. `agent.respond` is wrapped at one shared
boundary; an LLM failure yields `Failed`, which answers the customer *and*
alerts the owner rather than 500-ing.

Kills: all five cap-silence paths and the unhandled-LLM-failure path.

**M3 · Notification has a floor (kills RC3 + RC4).**
`delivered=False` becomes visible — on the owner's notifications page and as
a `/health` counter. Terminal states (`no_response`, `declined`, booking
`cancelled`) report through the same reporter, so RC4 is a call site rather
than a new mechanism.

### 6.3 Why this is the smallest change that works

- **One new concept** (`Outcome`) and **one new component** (the reporter
  that decides who gets told). Everything else is call sites.
- Preconditions are **data, not code** — a new employee declares them and
  inherits the entire behaviour, which is the stated goal.
- It reuses what exists: `OwnerNotification` (the record), `notifications.py`
  (the send), `Event` (the audit trail, which Milestone A just gave its first
  reader), `runner.py` (the deployment gate). No new table, no new channel,
  no queue.
- The alternative — eight patches — fixes today's eight and inherits nothing.

---

## 7. Migration plan

Every stage independently deployable and revertable. No stage changes two
things at once.

**B0 · Make the contract exist, adopt nothing.** Add `Outcome` and the
reporter. Zero call sites. Zero behaviour change. Ships green.

**B1 · The live-turn floor (M2).** Highest severity, smallest blast radius,
and free. Convert the five cap paths to an honest fallback; wrap
`agent.respond` at the shared boundary. **This is the one stage that changes
what a customer receives**, so it ships alone.

**B2 · Declared preconditions (M1), reporting only.** Employees declare
preconditions; failures log and notify once. **Dashboard unchanged** — no
visible state yet, so a mis-declared precondition cannot make a healthy
employee look broken to a customer.

**B3 · The blocked state becomes visible.** `EmployeeWorkspace` gains a
sixth field. Its docstring says *"reopen this design before adding a sixth
field"* — this is that conversation, and it needs founder sign-off
explicitly, not implicitly.

**B4 · Notification floor + terminal announcements (M3).** Surface
`delivered=False`; announce `no_response`.

**B5 · Structural adoption test.** A test that every registered live
employee declares preconditions and routes through the reporter. Ships last,
because it is what makes B1–B4 permanent.

---

## 8. Expected code changes

| File | Change | Size |
|---|---|---|
| `employee_outcome.py` *(new)* | `Outcome` variants + the reporter | ~120 lines |
| `preconditions.py` *(new)* or in the above | Declarations per employee + checker | ~80 lines |
| `service.py` | Cap path returns a message; wrap `agent.respond` | small |
| `recovery_service.py` | Cap path; `no_response` announcement | small |
| `review_service.py` / `referral_service.py` / `membership_service.py` | Cap path; precondition declaration | small each |
| `dispatcher_service.py` | Declare the Lead Qualifier dependency | small |
| `app.py` | One shared live-turn boundary | small |
| `workspace.py` + `employee.html` | The blocked state (B3 only) | small |
| `notifications.html`, `app.py` `/health` | Surface `delivered=False` | small |
| `db_models.py` | Possibly one `blocked_since`-style column | small |
| Tests | Per stage, plus the B5 structural test | substantial |

**No schema rewrite. No new table. No queue. No change to the tick, the
deployment gate, or `ARCHITECTURE.md`'s dashboard invariants** — except B3,
which touches invariant-governed territory and needs its own approval.

---

## 9. Risks

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R1 | **The framework is built and not adopted** — worse than 8 patches | **Critical** | B5's structural test; adoption is the definition of done, not the intent |
| R2 | Alert fatigue turns the fix into the failure (Challenge 1) | **High** | Blocked is *state*, notified once per distinct cause; never per tick |
| R3 | A wrong precondition marks a healthy employee blocked and stops real work | **High** | B2 reports without gating; the dashboard state waits for B3 after evidence |
| R4 | B1 changes what customers receive, on the A2P path | Medium | Ships alone; note `TWILIO_MESSAGING_SERVICE_SID` is still unset in production, so carriers may filter these replies — the *fix* could itself be silent |
| R5 | Existing tests encode the current silence as correct | Medium | Expected: several `trial_cap` tests assert `reply is None`. Those tests recorded a bug; changing them is the work, not a shortcut |
| R6 | `EmployeeWorkspace`'s five-field rule is a real frozen decision | Medium | B3 is a separate approval, not folded into B2 |
| R7 | Wrapping `agent.respond` masks real errors | Medium | `Failed` must still log at ERROR with the traceback and reach Sentry — catching is not swallowing |
| R8 | Voice's un-capped path (Finding F) is a money bug wearing a silence costume | Low for B | Out of scope; flag separately rather than widening |

---

## 10. Open questions

1. **Is Finding F (voice not cap-gated) in or out?** It is a *spending*
   bug, not a silence bug. I recommend **out** of Milestone B, tracked
   separately — but it is the same `can_respond` call site B1 touches, so
   there's an argument for doing it while we're there.
2. **B3 needs explicit sign-off** — `EmployeeWorkspace`'s docstring freezes
   it at five fields. Do you want the blocked state on the customer
   dashboard at all, or is the founder console enough for the first ten
   customers?
3. **What is the Deferred age threshold?** I'd propose 24h — longer than any
   legitimate quiet-hours wait (max ~17h), short enough to catch a broken
   gate within a day.
4. **Does `escalation_phone` being unset count as Blocked for every
   employee?** It is the channel every owner alert uses. I lean yes — a
   business with no owner number is a business Roster cannot report to at
   all, which is the deepest silent failure in the product.
