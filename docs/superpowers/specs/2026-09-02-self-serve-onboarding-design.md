# Self-Serve Onboarding — Reopening `/signup`

_Status: **PROPOSAL — design, no code.**_
_Author: Founder + engineering session, 2026-09-02._
_Verified against the working tree on `main` (the 5→3 dashboard redesign + `optout.py` + booking-honesty fixes are uncommitted in the tree; this design assumes they land first)._

> Self-serve signup was welded shut on 2026-08-06 (`portal.py:167`) because the
> old flow let **anyone on the internet trigger a real, billed Twilio number
> purchase** with zero identity or payment check. This design reopens it without
> reopening that hole. The gate moves *earlier* (a verified payment method, or —
> for the hand-onboarded first cohort — a founder still in the loop), it does not
> become a review queue.

---

## 1. What this supersedes

Reopening self-serve reverses standing decisions. Listing them so the reversal
is deliberate, not silent:

- **`ROADMAP.md`** — _"There is no self-serve onboarding … every customer begins
  at Contact Us and is provisioned by the Roster team … One onboarding flow, no
  internal exception."_ And the 2026-07-21 decision that request-led onboarding
  stays default _"until customer-success metrics … show a repeatable self-serve
  motion works."_
- **The Phase 6/7 cutover** (in-flight) — its stated purpose is to *delete* the
  onboarding wizard. `portal.v2_home`'s docstring: _"after Phase 6 there is no
  onboarding wizard to bounce them to."_ This design keeps and rebuilds it.
- The standing _"founder throughput is the lever, not lead volume"_ verdict.

`ROADMAP.md` gets updated in the Phase 6 PR so the record is consistent. The
2026-07-21 metrics bar (onboarding time, activation rate, first-week retention)
is **not** waived — it becomes the gate for dropping the flag to the public
(§4, "Public open").

---

## 2. Situation

- **0 paying customers** as of 2026-09-02.
- **First ~10 customers: concierge onboarding**, but run *through the customer's
  own account* — the founder logs in as each customer and drives the same wizard
  the public will eventually use, rather than the founder console. This dogfoods
  the flow and means there is no separate founder-console onboarding path to
  maintain.
- **First few customers are free** — no card collected, no charge.
- **Twilio 10DLC: manual per-brand, KYC has been a recurring blocker.** SMS at
  volume is not solved. For a hand-onboarded cohort the founder does the per-brand
  registration manually, same as today.
- Stripe is **not** a blocker for the first cohort — it is built in parallel and
  must be live before the flag opens to the public.

---

## 3. The abuse vector, and the two locks

The thing that closed self-serve:

```
stranger → /signup → /onboarding/* → activate_frontdesk()
                                   → buy_twilio_number()   ← real, billed, no human
```

`activate_frontdesk` (`activation.py`) both collects config **and** buys a
number. The reopen splits those:

- **The wizard never buys a number.** It creates the `Business`, collects config,
  deploys the Frontdesk `Employee` row, and ends on an honest "we're setting up
  your line" state. A `Business` with no `inbound_number` is already a valid,
  gracefully-handled state everywhere (the dashboard shows honest empty states) —
  no new status column needed.
- **Number provisioning gains a hard precondition.** `provisioning.py` /
  `/clients/{id}/provision-number` refuse unless **`payment_method_verified_at`
  is set OR a founder override is present.** For the free first-10 the founder
  sets the override; Stripe fills the `payment_method_verified_at` path later.

Two locks keep the public out during the interim, and **both** must clear before
`/signup` is public:

1. **`SELF_SERVE_SIGNUP` env flag** — off for the world, on for the founder.
   Public `/signup` with the flag off keeps its current behaviour (redirect to
   the waitlist).
2. **The payment precondition** on provisioning — built now, exercised by the
   founder override until Stripe lands.

This preserves the brief's rule ("Phase 0 before `/signup` is public") — the
flag substitutes for the payment gate *only while `/signup` is non-public*.

---

## 4. Phases, re-sequenced

| # | Phase | When | Gate to start |
|---|---|---|---|
| **1a** | **Tomorrow PR** — flag-gated wizard, no number purchase, provisioning precondition + founder override | now | this spec approved |
| **0** | Stripe: Customer + PaymentMethod, `payment_method_verified_at`, email magic-link verification | parallel | 1a merged |
| **2** | Async provisioning + customer status page (reuse `scheduler.py` tick + `voice-preflight`'s 4-link check) | parallel | 0 merged |
| **1b** | Auto-research prefill (scrape + Google Places) — "confirm or edit", not empty fields | after first ~5 onboarded, if the form is the bottleneck | 2 merged |
| **3** | Calendar OAuth non-blocking — confirm signup never requires it, move to post-activation settings | parallel | independent |
| **4** | 10DLC at volume — ISV path confirmation with Twilio (**not code-only**); until resolved, self-serve SMS **queues** rather than sends unregistered | parallel | Twilio conversation |
| **5** | Trial-cap → Stripe: on cap crossing, convert card trial→paid or pause outbound; reuse `notifications.py` | parallel | 0 merged |
| **6** | Feature-flag rollout to a % of public signups; `ROADMAP.md` update; keep `/clients/new` as override | last | 0,2,4,5 done + 2026-07-21 metrics bar met |

**Phase 0 trimmed for v1** (0 customers, hand-onboarded):
- Keep: Stripe Customer + PaymentMethod, `payment_method_verified_at` on
  `Business` (config on the aggregate root, same as `xai_signing_secret` — not a
  satellite), the provisioning precondition + its explicit test, email
  magic-link verification (cheap, reuses `auth.URLSafeTimedSerializer`).
- Cut until the week before public open: **auth holds** (a saved card is gate
  enough at this volume), **per-IP/per-email velocity limiting** (the flag is the
  gate).

---

## 5. The tomorrow PR (Phase 1a) — detail

### 5.1 Routes

- `SELF_SERVE_SIGNUP` env var, default off. `should_allow_signup(environ)` —
  pure, testable, same convention as `should_start_scheduler`.
- `/signup` GET+POST, `/onboarding/business` GET+POST, `/onboarding/receptionist`
  GET+POST: when the flag is **off**, keep the current redirect-to-waitlist.
  When **on**, serve the wizard.
- The wizard's terminal action calls a new `activate_config_only(session,
  business)` — the half of `activate_frontdesk` that sets `frontdesk_live`,
  `activated_at`, and deploys the `frontdesk` `Employee` row. It **does not**
  call `buy_twilio_number` / `provision_voice`.
- `activate_frontdesk` keeps its current behaviour for any existing caller;
  the number-buying half is factored so both paths share it.

### 5.2 Wizard content

Replaces what the founder types into `/clients/new`: `trade`, `hours`,
`pricing_faq`, `service_area`, `tone`, `escalation_phone`, `answer_mode`.

Per-trade defaults so an HVAC signup never sees a blank form — a new
`onboarding_defaults.py` (or a dict beside `engine.TRADE_TRIAGE_NOTES`) seeds
`hours` ("Mon–Fri 8am–5pm"), a first-draft `pricing_faq` skeleton, and
`answer_mode` ("backup"). The wizard shows them pre-filled and editable.

Reuse `/clients/new`'s `Business`-creation logic — extract the shared bit so the
wizard and the console can't drift on what a new `Business` looks like.

### 5.3 Provisioning precondition

```
provision is refused unless:
    business.payment_method_verified_at is not None
    OR business has a founder-override marker
```

The override: simplest is a nullable `Business.provisioning_unlocked_at` set by a
founder-only route (`POST /clients/{id}/unlock-provisioning`) — one console
action, audited by the timestamp, no new UI on the customer side. `provision_number`,
`retry_xai_registration`, and `activate_frontdesk`'s number-buy all check it.

### 5.4 Tests (same PR)

- `test_self_serve_signup_gating`: flag off → `/signup` redirects; flag on →
  wizard renders.
- `test_wizard_creates_pending_business`: a completed wizard lands a `Business`
  with config set, a `frontdesk` `Employee` row, `frontdesk_live=True`, and
  **no `inbound_number`**.
- `test_provisioning_requires_verified_payment`: provisioning is refused for a
  `Business` with neither `payment_method_verified_at` nor the override, **even
  when every other field is valid** — this is the brief's Phase 0 acceptance
  test, built now.
- `test_founder_override_unlocks_provisioning`: with the override set,
  provisioning proceeds.
- `test_per_trade_prefill`: an HVAC wizard GET contains the seeded default hours.

### 5.5 Non-negotiables — unaffected

- Booking honesty: this PR adds no customer-facing appointment language.
  `assert_no_confirmation_claim` untouched.
- Claim-before-send: the wizard's writes are ordinary `Business` upserts; no new
  send path. Magic-link verification (Phase 0) will follow the `/access/{token}`
  signing pattern.
- No silent failures: a wizard that can't create a `Business` returns an error to
  the customer; a provisioning step stuck > N minutes (Phase 2) pages the founder
  via `employee_outcome.py`.
- `/clients/*` founder console: untouched, stays as override/support.

---

## 6. Open questions

1. ~~Does the first cohort bring their own Twilio number, or does Roster buy
   it?~~ **RESOLVED 2026-09-02 (founder): Roster buys it.** The founder clicks
   provision from the console after each onboarding call. So the billed abuse
   surface exists during the interim and the founder-override precondition
   (§5.3) is load-bearing, not just a convenience — it is the *only* gate until
   Stripe lands. No BYO-number path is built.
2. **Magic-link email verification in Phase 1a or Phase 0?** Assumed Phase 0 —
   the founder is driving the first 10 and can vouch for the email. Move to 1a if
   any of the first 10 self-serve without the founder watching.
3. **Google Places API key / budget** for Phase 1b auto-research. Deferred with
   the phase.

---

## 7. Acceptance for Phase 1a

A founder, with `SELF_SERVE_SIGNUP=1`, completes the wizard and lands a
`Business` in the same state a founder-created one would be in **minus the
number** — and no code path can buy that number until the founder unlocks
provisioning or a verified payment method exists.
