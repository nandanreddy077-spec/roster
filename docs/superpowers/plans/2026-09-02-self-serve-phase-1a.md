# Self-Serve Phase 1a — Flag-Gated Wizard + Provisioning Gate

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reopen `/signup` + `/onboarding/*` behind an env flag so the founder can drive concierge onboarding through the customer's own account, with a hard precondition that no code path buys a Twilio number until a payment method is verified or the founder explicitly unlocks it.

**Architecture:** Restore the pre-`5659690` wizard routes in `portal.py`, gated on `should_allow_signup()`. Split `activation.activate_frontdesk` into `activate_config_only` (no purchase) — which the wizard calls — and the number-buy half, which stays behind a new `provisioning.provisioning_allowed()` gate that `app.py`'s two provision routes also consult. Two new nullable timestamps on `Business`: `payment_method_verified_at` (Stripe fills this in Phase 0) and `provisioning_unlocked_at` (a founder-only route sets it now).

**Tech Stack:** FastAPI, SQLModel, Jinja2, pytest + `fastapi.testclient`. No new dependencies.

## Global Constraints

- **Booking honesty untouched.** This work adds no customer-facing appointment language; `booking_language.assert_no_confirmation_claim` and `test_booking_honesty.py` stay green with no changes.
- **No silent failures.** A wizard step that can't write returns an error to the customer; the provisioning gate returns a founder-visible `?...error=` redirect, never a 500 and never a quiet no-op.
- **Tests in the same task, not after.** Every task ends with `pytest` green on its new tests plus the full suite (`cd agent && pytest -q`, currently 1226 passing).
- **Founder console (`/clients/*`) is not removed.** Task 3 *adds* one route + one button to it; nothing existing is changed or deleted.
- **`SELF_SERVE_SIGNUP` defaults OFF.** With the var unset, every route in this plan behaves exactly as it does today (redirect to `SIGNUP_CLOSED_REDIRECT`). The existing `test_portal_signup.py` closed-door tests must still pass unchanged.
- **Line length 100, ruff `select = ["E","F","I"]`.** Match the surrounding code's comment density and voice.
- **`datetime.utcnow()`** — the codebase uses it everywhere; match it, do not introduce `datetime.now(UTC)`.

---

### Task 1: Provisioning-gate columns + `provisioning_allowed()`

**Files:**
- Modify: `agent/db_models.py` (the `Business` class, after `membership_plan`)
- Modify: `agent/db.py` (`_migrate_add_columns`, the `statements` tuple)
- Modify: `agent/provisioning.py` (add one function near the top, after `public_base_url`)
- Test: `agent/tests/test_provisioning_gate.py` (new)

**Interfaces:**
- Produces: `Business.payment_method_verified_at: Optional[datetime]`, `Business.provisioning_unlocked_at: Optional[datetime]`
- Produces: `provisioning.provisioning_allowed(business: Business) -> bool` — True iff either timestamp is set.

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_provisioning_gate.py`:

```python
"""Nothing buys a Twilio number for a business that has neither a verified
payment method nor an explicit founder unlock. This is the precondition that
lets /signup be reopened without reopening the abuse vector that closed it
(a stranger making Roster spend money) — see
docs/superpowers/specs/2026-09-02-self-serve-onboarding-design.md §3.
"""

from datetime import datetime

from db_models import Business
from provisioning import provisioning_allowed


def _biz(**kw) -> Business:
    return Business(business_name="Kestrel HVAC", trade="HVAC", **kw)


def test_a_fresh_business_may_not_provision():
    assert provisioning_allowed(_biz()) is False


def test_a_verified_payment_method_allows_provisioning():
    assert provisioning_allowed(_biz(payment_method_verified_at=datetime.utcnow())) is True


def test_a_founder_unlock_allows_provisioning():
    assert provisioning_allowed(_biz(provisioning_unlocked_at=datetime.utcnow())) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_provisioning_gate.py -q`
Expected: FAIL — `ImportError: cannot import name 'provisioning_allowed'` (and `TypeError` on the unknown `Business` kwargs).

- [ ] **Step 3: Add the columns**

In `agent/db_models.py`, in `class Business`, immediately after the `membership_plan: Optional[str] = None` block:

```python
    # Set by Stripe once a card is on file (Phase 0). Until then it is None,
    # and the only thing that lets a self-serve business provision a number is
    # provisioning_unlocked_at below. See
    # docs/superpowers/specs/2026-09-02-self-serve-onboarding-design.md §3.
    payment_method_verified_at: Optional[datetime] = None
    # A founder override: set from the console for the free first-cohort
    # customers, who are hand-onboarded and have no card. provisioning.
    # provisioning_allowed() treats this exactly like a verified payment.
    provisioning_unlocked_at: Optional[datetime] = None
```

In `agent/db.py`, in `_migrate_add_columns`'s `statements` tuple, after the last `ALTER TABLE customer ADD COLUMN opted_out_at DATETIME` line:

```python
        "ALTER TABLE business ADD COLUMN payment_method_verified_at DATETIME",
        "ALTER TABLE business ADD COLUMN provisioning_unlocked_at DATETIME",
```

- [ ] **Step 4: Add `provisioning_allowed`**

In `agent/provisioning.py`, after the `public_base_url()` function:

```python
def provisioning_allowed(business) -> bool:
    """Whether a Twilio number may be bought for this business.

    True once EITHER a payment method is verified (Stripe, Phase 0) OR a
    founder has explicitly unlocked it from the console. Self-serve /signup
    was closed on 2026-08-06 because the wizard bought a number for anyone;
    this is the gate that lets it reopen. Every code path that spends money —
    app.provision_number, app.retry_xai_registration, and
    activation.activate_frontdesk's purchase half — checks this first.

    Duck-typed on the two attributes so it needs no db_models import and is
    trivial to unit-test with a bare object.
    """
    return (
        getattr(business, "payment_method_verified_at", None) is not None
        or getattr(business, "provisioning_unlocked_at", None) is not None
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd agent && pytest tests/test_provisioning_gate.py -q && pytest -q`
Expected: 3 new pass; full suite still green (1229 total).

- [ ] **Step 6: Commit**

```bash
git add agent/db_models.py agent/db.py agent/provisioning.py agent/tests/test_provisioning_gate.py
git commit -m "feat(provisioning): payment/unlock precondition columns + provisioning_allowed()

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Split `activate_frontdesk` into `activate_config_only` + guarded purchase

**Files:**
- Modify: `agent/activation.py` (whole file — restructure)
- Test: `agent/tests/test_activation.py` (add cases; keep existing)

**Interfaces:**
- Consumes: `provisioning.provisioning_allowed` (Task 1)
- Produces: `activation.activate_config_only(session, client) -> None` — sets `frontdesk_live=True`, `activated_at`, deploys the `frontdesk` Employee row, buys NOTHING.
- Produces: `activation.activate_frontdesk(session, client) -> None` — unchanged signature; now `activate_config_only` + a purchase that is skipped unless `provisioning_allowed(client)`.

- [ ] **Step 1: Write the failing tests**

Add to `agent/tests/test_activation.py`:

```python
def test_activate_config_only_never_buys_a_number(session, monkeypatch):
    import activation
    from db_models import Business, Employee
    from sqlmodel import select

    def _boom(*a, **k):
        raise AssertionError("activate_config_only must not buy a number")

    monkeypatch.setattr(activation, "buy_twilio_number", _boom)

    biz = Business(business_name="Kestrel", trade="HVAC")
    session.add(biz)
    session.commit()
    session.refresh(biz)

    activation.activate_config_only(session, biz)

    session.refresh(biz)
    assert biz.frontdesk_live is True
    assert biz.activated_at is not None
    assert biz.inbound_number is None
    emp = session.exec(select(Employee).where(Employee.business_id == biz.id)).first()
    assert emp is not None and emp.role_key == "frontdesk"


def test_activate_frontdesk_skips_the_purchase_without_a_gate(session, monkeypatch):
    import activation
    from db_models import Business

    def _boom(*a, **k):
        raise AssertionError("purchase must be gated")

    monkeypatch.setattr(activation, "buy_twilio_number", _boom)

    biz = Business(business_name="Kestrel", trade="HVAC")  # no payment, no unlock
    session.add(biz)
    session.commit()
    session.refresh(biz)

    activation.activate_frontdesk(session, biz)  # must not raise

    session.refresh(biz)
    assert biz.frontdesk_live is True
    assert biz.inbound_number is None


def test_activate_frontdesk_buys_when_unlocked(session, monkeypatch):
    import activation
    from datetime import datetime
    from db_models import Business

    monkeypatch.setattr(
        activation, "buy_twilio_number", lambda *a, **k: {"phone_number": "+15125550100", "sid": "PN1"}
    )
    monkeypatch.setattr(activation, "provision_voice", lambda *a, **k: None)

    biz = Business(business_name="Kestrel", trade="HVAC", provisioning_unlocked_at=datetime.utcnow())
    session.add(biz)
    session.commit()
    session.refresh(biz)

    activation.activate_frontdesk(session, biz)

    session.refresh(biz)
    assert biz.inbound_number == "+15125550100"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd agent && pytest tests/test_activation.py -q`
Expected: FAIL — `AttributeError: module 'activation' has no attribute 'activate_config_only'`; the gate tests fail because the current code always tries to buy.

- [ ] **Step 3: Rewrite `agent/activation.py`**

```python
"""Activation: from a filled-in onboarding profile to a live Frontdesk.

Two entry points:

  activate_config_only  — set frontdesk_live, deploy the Frontdesk employee.
      Buys nothing. This is what the self-serve wizard calls: a business is
      "live" as a config, and Roster provisions its phone number separately
      once a payment method exists or a founder unlocks it (provisioning.
      provisioning_allowed).

  activate_frontdesk    — activate_config_only PLUS the Twilio number
      purchase, and the purchase is itself gated on provisioning_allowed.
      SMS-only is a fully working Frontdesk, so a skipped or failed purchase
      never blocks activation.
"""

import logging
from datetime import datetime

from db_models import Business
from deployment import deploy_role
from provisioning import buy_twilio_number, provision_voice, provisioning_allowed
from sqlmodel import Session

logger = logging.getLogger(__name__)


def activate_config_only(session: Session, client: Business) -> None:
    """Mark Frontdesk live and deploy its Employee row. No phone number."""
    client.frontdesk_live = True
    client.activated_at = datetime.utcnow()
    session.add(client)
    session.commit()
    # The Employee row IS the deployment record (audit F1): without it the
    # business shows zero departments until the next app restart's backfill.
    # Best-effort — activation must always complete — but loud.
    try:
        deploy_role(session, client.id, "frontdesk")
    except Exception as e:
        logger.error(
            "failed to create frontdesk employee",
            exc_info=e,
            extra={"business_id": client.id},
        )


def _buy_number(session: Session, client: Business) -> None:
    if client.inbound_number:
        return
    if not provisioning_allowed(client):
        # Not an error: the wizard completes, and the founder buys the number
        # from the console once payment is verified / they unlock it.
        logger.info(
            "number purchase skipped — no verified payment or founder unlock",
            extra={"business_id": client.id},
        )
        return
    try:
        purchase = buy_twilio_number()
        client.inbound_number = purchase["phone_number"]
        client.twilio_number_sid = purchase["sid"]
        # Commit the purchase BEFORE voice: the number is billable the moment
        # Twilio returns, so it must be on file even if everything after fails.
        session.add(client)
        session.commit()
        provision_voice(session, client)
    except Exception as e:
        logger.warning(
            "number provisioning failed", exc_info=e, extra={"business_id": client.id}
        )


def activate_frontdesk(session: Session, client: Business) -> None:
    """Config + a gated number purchase. Never raises on a provisioning
    failure — onboarding must always complete."""
    _buy_number(session, client)
    activate_config_only(session, client)
```

- [ ] **Step 4: Run to verify pass**

Run: `cd agent && pytest tests/test_activation.py tests/test_activation_live.py -q && pytest -q`
Expected: all green. If `test_activation_live.py` asserted a purchase on a business with no gate, update that test to set `provisioning_unlocked_at` first — the new behaviour is correct and the test was encoding the old.

- [ ] **Step 5: Commit**

```bash
git add agent/activation.py agent/tests/test_activation.py agent/tests/test_activation_live.py
git commit -m "feat(activation): activate_config_only — wizard path buys no number

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Provisioning gate on the founder routes + `unlock-provisioning`

**Files:**
- Modify: `agent/app.py` — `provision_number` (~896), `retry_xai_registration` (~948); add `unlock_provisioning` route near `set_billing_state` (~1229)
- Modify: `agent/templates/client_detail.html` — one button in the provisioning `<details>` section
- Test: `agent/tests/test_provisioning_gate.py` (extend)

**Interfaces:**
- Consumes: `provisioning.provisioning_allowed` (Task 1)
- Produces: `POST /clients/{client_id}/unlock-provisioning` — sets `provisioning_unlocked_at`, redirects to `/clients/{client_id}`.

- [ ] **Step 1: Write the failing tests**

Add to `agent/tests/test_provisioning_gate.py`:

```python
def test_provision_number_is_refused_without_payment_or_unlock(test_engine, monkeypatch):
    """The brief's Phase 0 acceptance test, built now: provisioning cannot
    fire without a gate, even when every other field is valid."""
    import app as app_module
    import db as db_module
    from conftest import DASH_AUTH, provisioned_business
    from fastapi.testclient import TestClient

    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)

    def _boom(*a, **k):
        raise AssertionError("no number may be bought without a gate")

    monkeypatch.setattr(app_module, "buy_twilio_number", _boom)

    bid = provisioned_business(test_engine)  # full config, no payment, no unlock
    client = TestClient(app_module.app)
    resp = client.post(
        f"/clients/{bid}/provision-number", data={"area_code": "512"},
        headers=DASH_AUTH, follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "provision_error" in resp.headers["location"]


def test_unlock_then_provision_is_allowed(test_engine, monkeypatch):
    import app as app_module
    import db as db_module
    from conftest import DASH_AUTH, provisioned_business
    from db_models import Business
    from fastapi.testclient import TestClient
    from sqlmodel import Session

    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(
        app_module, "buy_twilio_number",
        lambda *a, **k: {"phone_number": "+15125550111", "sid": "PN9"},
    )
    monkeypatch.setattr(app_module, "provision_voice", lambda *a, **k: None)

    bid = provisioned_business(test_engine)
    client = TestClient(app_module.app)
    client.post(f"/clients/{bid}/unlock-provisioning", headers=DASH_AUTH, follow_redirects=False)

    with Session(test_engine) as s:
        assert s.get(Business, bid).provisioning_unlocked_at is not None

    resp = client.post(
        f"/clients/{bid}/provision-number", data={"area_code": "512"},
        headers=DASH_AUTH, follow_redirects=False,
    )
    assert resp.status_code == 303
    with Session(test_engine) as s:
        assert s.get(Business, bid).inbound_number == "+15125550111"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd agent && pytest tests/test_provisioning_gate.py -q`
Expected: FAIL — `provision-number` currently buys unconditionally; `/unlock-provisioning` 404s.

- [ ] **Step 3: Add the gate to `provision_number`**

In `agent/app.py`, import `provisioning_allowed` alongside the other `provisioning` imports (near line 89):

```python
from provisioning import (
    ProvisioningError,
    buy_twilio_number,
    provision_voice,
    provisioning_allowed,
    public_base_url,
    verify_voice_wiring,
)
```

In `provision_number`, right after the existing `if client is not None and client.twilio_number_sid:` early-return block, add:

```python
        if client is not None and not provisioning_allowed(client):
            from urllib.parse import quote

            msg = (
                f"{client.business_name or 'This business'} has no verified payment "
                "method. Click 'Unlock provisioning' first (free first-cohort), or "
                "wait until a card is on file."
            )
            return RedirectResponse(
                f"/clients/{client_id}?provision_error={quote(msg)}", status_code=303
            )
```

In `retry_xai_registration`, after the `if not client.inbound_number or not client.twilio_number_sid:` check, add the same guard (a business mid-retry already bought a number, so this is belt-and-braces — but if `provisioning_unlocked_at` was later cleared it should still refuse a fresh xAI spend):

```python
        if not provisioning_allowed(client):
            from urllib.parse import quote

            return RedirectResponse(
                f"/clients/{client_id}?provision_error="
                + quote("Provisioning is locked for this business."),
                status_code=303,
            )
```

- [ ] **Step 4: Add the `unlock-provisioning` route**

In `agent/app.py`, immediately before `@app.post("/clients/{client_id}/billing-state")`:

```python
@app.post("/clients/{client_id}/unlock-provisioning")
def unlock_provisioning(client_id: int):
    """Founder override: allow a Twilio number to be bought for a business
    that has no card on file. For the free hand-onboarded first cohort — see
    docs/superpowers/specs/2026-09-02-self-serve-onboarding-design.md §3.

    Idempotent: re-clicking keeps the original timestamp, so the audit trail
    ('when did a human decide to spend money on this shop') stays honest.
    """
    with Session(engine) as session:
        client = session.get(Business, client_id)
        if client is None:
            raise HTTPException(status_code=404, detail="No such client")
        if client.provisioning_unlocked_at is None:
            client.provisioning_unlocked_at = datetime.utcnow()
            session.add(client)
            session.commit()
    return RedirectResponse(f"/clients/{client_id}", status_code=303)
```

- [ ] **Step 5: Add the button**

In `agent/templates/client_detail.html`, inside the provisioning `<details>` block (find the `provision-number` form), add above or beside it:

```html
        {% if not client.payment_method_verified_at and not client.provisioning_unlocked_at %}
        <form method="post" action="/clients/{{ client.id }}/unlock-provisioning" class="inline-form">
          <button type="submit" class="btn-secondary">Unlock provisioning (free cohort)</button>
          <span class="hint">No card on file. Buying a number is blocked until you unlock it.</span>
        </form>
        {% elif client.provisioning_unlocked_at %}
        <p class="hint">Provisioning unlocked {{ client.provisioning_unlocked_at.strftime('%Y-%m-%d') }}.</p>
        {% endif %}
```

(Match the surrounding template's class names — inspect the nearby forms; `btn-secondary`/`hint` are illustrative.)

- [ ] **Step 6: Run to verify pass**

Run: `cd agent && pytest tests/test_provisioning_gate.py tests/test_client_setup.py -q && pytest -q`
Expected: green. `test_client_setup.py` and `test_ops_console.py` exercise `/clients/*` — if any asserted a provision without a gate, set `provisioning_unlocked_at` in that fixture.

- [ ] **Step 7: Commit**

```bash
git add agent/app.py agent/templates/client_detail.html agent/tests/test_provisioning_gate.py
git commit -m "feat(provisioning): gate the founder provision routes; add unlock-provisioning

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: `should_allow_signup()` flag + per-trade onboarding defaults

**Files:**
- Create: `agent/onboarding_defaults.py`
- Modify: `agent/portal.py` — add `should_allow_signup` near `_current_client` (~160)
- Test: `agent/tests/test_onboarding_defaults.py` (new)

**Interfaces:**
- Produces: `portal.should_allow_signup(environ) -> bool` — `environ.get("SELF_SERVE_SIGNUP") == "1"`.
- Produces: `onboarding_defaults.defaults_for(trade: str) -> dict` with keys `hours`, `pricing_faq`, `services` (comma string), `answer_mode`. Unknown trade → a safe generic dict, never empty.

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_onboarding_defaults.py`:

```python
"""An HVAC signup should see a pre-filled form, not blank fields — the
difference between a 15-minute form and a 6-item confirm screen (spec §5.2)."""

import portal
from onboarding_defaults import defaults_for


def test_flag_defaults_off():
    assert portal.should_allow_signup({}) is False
    assert portal.should_allow_signup({"SELF_SERVE_SIGNUP": "0"}) is False


def test_flag_on_with_1():
    assert portal.should_allow_signup({"SELF_SERVE_SIGNUP": "1"}) is True


def test_known_trade_prefills_every_field():
    d = defaults_for("HVAC")
    assert d["hours"] and d["pricing_faq"] and d["services"] and d["answer_mode"] == "backup"


def test_unknown_trade_is_safe_not_empty():
    d = defaults_for("underwater basket weaving")
    assert d["hours"] and d["pricing_faq"] and d["answer_mode"] == "backup"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd agent && pytest tests/test_onboarding_defaults.py -q`
Expected: FAIL — module + function missing.

- [ ] **Step 3: Create `agent/onboarding_defaults.py`**

```python
"""Pre-fill values for the self-serve onboarding wizard, keyed on trade.

The wizard shows these as editable defaults so an owner confirms and tweaks
rather than starting from a blank textarea. Plain copy, same shape as
engine.TRADE_TRIAGE_NOTES and roles.RECEPTIONIST_TRADE_NAMES — a dict keyed
on the normalized trade string, a safe generic fallback, no new architecture.

These are STARTING POINTS, never claims: the owner owns the final text, and
Frontdesk answers from whatever they save, not from this file.
"""

_GENERIC = {
    "hours": "Mon–Fri 8am–5pm",
    "services": "Repairs, Installations, Maintenance",
    "pricing_faq": (
        "Diagnostic/service-call fee: $89, waived if you book the repair. "
        "We give a firm quote before any work starts."
    ),
    "answer_mode": "backup",
}

_BY_TRADE = {
    "hvac": {
        "services": "AC repair, Heating repair, System replacement, Tune-ups, Duct work",
        "pricing_faq": (
            "Diagnostic fee: $89, waived if you book the repair. "
            "Replacement estimates are free. We quote before any work starts."
        ),
    },
    "plumbing": {
        "services": "Leak repair, Drain cleaning, Water heaters, Fixture install, Repiping",
        "pricing_faq": (
            "Service-call fee: $79, waived with a booked repair. "
            "Free estimates on replacements and repipes."
        ),
    },
    "electrical": {
        "services": "Panel upgrades, Wiring, Outlets & switches, Lighting, EV chargers",
        "pricing_faq": "Diagnostic fee: $95. Free estimates on panel and rewire jobs.",
    },
    "roofing": {
        "services": "Leak repair, Full replacement, Inspections, Storm damage",
        "pricing_faq": "Roof inspections are free. Repair and replacement quotes are free.",
    },
}


def defaults_for(trade: str) -> dict:
    key = (trade or "").strip().lower()
    return {**_GENERIC, **_BY_TRADE.get(key, {})}
```

- [ ] **Step 4: Add `should_allow_signup` to `portal.py`**

After `_current_client`:

```python
def should_allow_signup(environ) -> bool:
    """Self-serve /signup is off by default. Set SELF_SERVE_SIGNUP=1 to open
    the wizard — founder-only for now, public once Phase 0 (Stripe) and a
    working A2P 10DLC path both exist. Pure and testable, same convention as
    app.should_start_scheduler."""
    return environ.get("SELF_SERVE_SIGNUP") == "1"
```

- [ ] **Step 5: Run to verify pass**

Run: `cd agent && pytest tests/test_onboarding_defaults.py -q && pytest -q`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add agent/onboarding_defaults.py agent/portal.py agent/tests/test_onboarding_defaults.py
git commit -m "feat(onboarding): SELF_SERVE_SIGNUP flag + per-trade wizard defaults

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: Reopen `/signup` (flag-gated)

**Files:**
- Modify: `agent/portal.py` — `signup_form` (~185), `signup_submit` (~192); add `hash_password` import
- Modify: `agent/tests/test_portal_signup.py` — existing tests stay (flag off); add flag-on cases

**Interfaces:**
- Consumes: `portal.should_allow_signup` (Task 4), `auth.hash_password`
- Produces: with `SELF_SERVE_SIGNUP=1`, `POST /signup` (email, password, optional trade) creates a `Business(email=, password_hash=)`, sets `session["client_id"]` and `session["prefill_trade"]`, redirects to `/onboarding/business`.

- [ ] **Step 1: Write the failing tests**

Add to `agent/tests/test_portal_signup.py`:

```python
def test_signup_open_when_flagged(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    monkeypatch.setenv("SELF_SERVE_SIGNUP", "1")
    client = TestClient(app_module.app)

    r = client.get("/signup")
    assert r.status_code == 200

    r = client.post(
        "/signup",
        data={"email": "new@shop.com", "password": "a-good-long-passphrase", "trade": "HVAC"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/onboarding/business"
    with Session(test_engine) as s:
        biz = s.exec(select(Business).where(Business.email == "new@shop.com")).first()
        assert biz is not None and biz.password_hash is not None
        assert biz.inbound_number is None


def test_signup_still_closed_when_unflagged(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    client = TestClient(app_module.app)
    r = client.get("/signup", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == portal_module.SIGNUP_CLOSED_REDIRECT
```

- [ ] **Step 2: Run to verify failure**

Run: `cd agent && pytest tests/test_portal_signup.py -q`
Expected: `test_signup_open_when_flagged` FAILS (still redirects to `/`).

- [ ] **Step 3: Restore the routes**

In `agent/portal.py`, add to the imports from `auth`:

```python
from auth import hash_password, read_access_token, verify_password
```

Replace `signup_form` and `signup_submit`:

```python
@router.get("/signup")
def signup_form(request: Request):
    if not should_allow_signup(os.environ):
        return RedirectResponse(SIGNUP_CLOSED_REDIRECT, status_code=303)
    trade = request.query_params.get("trade", "")
    return templates.TemplateResponse(
        request,
        "signup.html",
        {"error": None, "email": "", "trade": trade, "google_enabled": google_enabled()},
    )


@router.post("/signup")
def signup_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    trade: str = Form(""),
):
    if not should_allow_signup(os.environ):
        return RedirectResponse(SIGNUP_CLOSED_REDIRECT, status_code=303)
    email = email.strip().lower()
    with Session(engine) as session:
        if session.exec(select(Business).where(Business.email == email)).first():
            return templates.TemplateResponse(
                request,
                "signup.html",
                {
                    "error": "That email's already registered — try logging in instead.",
                    "email": email,
                    "trade": trade,
                    "google_enabled": google_enabled(),
                },
                status_code=400,
            )
        client = Business(email=email, password_hash=hash_password(password))
        session.add(client)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return templates.TemplateResponse(
                request,
                "signup.html",
                {
                    "error": "That email's already registered — try logging in instead.",
                    "email": email,
                    "trade": trade,
                    "google_enabled": google_enabled(),
                },
                status_code=400,
            )
        session.refresh(client)
        request.session["client_id"] = client.id
        if trade.strip():
            request.session["prefill_trade"] = trade.strip()
    return RedirectResponse("/onboarding/business", status_code=303)
```

Add `from sqlalchemy.exc import IntegrityError` to `portal.py`'s imports if not present.

- [ ] **Step 4: Run to verify pass**

Run: `cd agent && pytest tests/test_portal_signup.py -q && pytest -q`
Expected: all green — the unflagged tests pass unchanged, the flagged ones now pass.

- [ ] **Step 5: Commit**

```bash
git add agent/portal.py agent/tests/test_portal_signup.py
git commit -m "feat(signup): reopen /signup behind SELF_SERVE_SIGNUP

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: Reopen `/onboarding/*` (flag-gated), wire `activate_config_only`

**Files:**
- Modify: `agent/portal.py` — remove the `_RETIRED_WIZARD_ROUTES` block (~346–369), restore the four routes; import `activate_config_only`, `defaults_for`
- Modify: `agent/tests/test_portal_onboarding.py` — flag-on walk-through; keep the flag-off closed assertions

**Interfaces:**
- Consumes: `activation.activate_config_only` (Task 2), `onboarding_defaults.defaults_for` (Task 4), `should_allow_signup` (Task 4)
- Produces: with the flag on, `GET/POST /onboarding/business` and `/onboarding/receptionist`; the receptionist POST calls `activate_config_only` and redirects to `/activation/live`.

- [ ] **Step 1: Write the failing test**

Replace the retired-route assertions in `agent/tests/test_portal_onboarding.py` with:

```python
def test_full_wizard_walk_when_flagged(monkeypatch, test_engine):
    import activation

    _wire(monkeypatch, test_engine)  # same helper the file already uses
    monkeypatch.setenv("SELF_SERVE_SIGNUP", "1")
    monkeypatch.setattr(activation, "buy_twilio_number", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("wizard must not buy a number")))
    client = TestClient(app_module.app)

    client.post("/signup", data={"email": "o@s.com", "password": "long-enough-passphrase", "trade": "HVAC"})
    client.post("/onboarding/business", data={
        "business_name": "Kestrel HVAC", "trade": "HVAC",
        "services": "AC repair, Heating", "hours": "Mon-Fri 8-5",
        "pricing_faq": "Diagnostic $89.",
    }, follow_redirects=False)
    r = client.post("/onboarding/receptionist", data={
        "escalation_phone": "512-555-0101", "answer_mode": "backup",
    }, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/activation/live"

    from db_models import Business, Employee
    with Session(test_engine) as s:
        biz = s.exec(select(Business).where(Business.email == "o@s.com")).first()
        assert biz.frontdesk_live is True
        assert biz.business_name == "Kestrel HVAC"
        assert biz.escalation_phone == "+15125550101"
        assert biz.inbound_number is None
        assert s.exec(select(Employee).where(Employee.business_id == biz.id)).first().role_key == "frontdesk"


def test_onboarding_closed_when_unflagged(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    client = TestClient(app_module.app)
    r = client.get("/onboarding/business", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == portal_module.DASHBOARD_HOME


def test_prefill_uses_trade_defaults(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    monkeypatch.setenv("SELF_SERVE_SIGNUP", "1")
    client = TestClient(app_module.app)
    client.post("/signup", data={"email": "o@s.com", "password": "long-enough-passphrase", "trade": "HVAC"})
    r = client.get("/onboarding/business")
    assert "AC repair" in r.text  # from onboarding_defaults._BY_TRADE["hvac"]
```

- [ ] **Step 2: Run to verify failure**

Run: `cd agent && pytest tests/test_portal_onboarding.py -q`
Expected: FAIL — routes redirect to the dashboard even when flagged.

- [ ] **Step 3: Restore the routes**

In `agent/portal.py` add imports:

```python
from activation import activate_config_only
from onboarding_defaults import defaults_for
```

Delete the `_RETIRED_WIZARD_ROUTES` tuple, `_retired_wizard`, and the `for _path, _methods in _RETIRED_WIZARD_ROUTES:` loop. Replace with:

```python
@router.get("/onboarding/business")
def onboarding_business_form(request: Request):
    if not should_allow_signup(os.environ):
        return RedirectResponse(DASHBOARD_HOME, status_code=303)
    with Session(engine) as session:
        client = _current_client(request, session)
        if client is None:
            return RedirectResponse("/login", status_code=303)
        if client.frontdesk_live:
            return RedirectResponse(DASHBOARD_HOME, status_code=303)
    trade = request.session.get("prefill_trade", "")
    return templates.TemplateResponse(
        request,
        "onboarding_business.html",
        {"prefill_trade": trade, "defaults": defaults_for(trade)},
    )


@router.post("/onboarding/business")
def onboarding_business_submit(
    request: Request,
    business_name: str = Form(...),
    trade: str = Form(...),
    services: str = Form(...),
    hours: str = Form(...),
    pricing_faq: str = Form(...),
):
    if not should_allow_signup(os.environ):
        return RedirectResponse(DASHBOARD_HOME, status_code=303)
    service_list = [s.strip() for s in services.split(",") if s.strip()]
    with Session(engine) as session:
        client = _current_client(request, session)
        if client is None:
            return RedirectResponse("/login", status_code=303)
        client.business_name = business_name.strip()
        client.trade = trade.strip()
        client.services_json = json.dumps(service_list)
        client.hours = hours.strip()
        client.pricing_faq = pricing_faq.strip()
        session.add(client)
        session.commit()
    return RedirectResponse("/onboarding/receptionist", status_code=303)


@router.get("/onboarding/receptionist")
def onboarding_receptionist_form(request: Request):
    if not should_allow_signup(os.environ):
        return RedirectResponse(DASHBOARD_HOME, status_code=303)
    with Session(engine) as session:
        client = _current_client(request, session)
        if client is None:
            return RedirectResponse("/login", status_code=303)
        if client.frontdesk_live:
            return RedirectResponse(DASHBOARD_HOME, status_code=303)
        if not client.business_name:
            return RedirectResponse("/onboarding/business", status_code=303)
        trade = client.trade
    return templates.TemplateResponse(
        request, "onboarding_receptionist.html", {"defaults": defaults_for(trade)}
    )


@router.post("/onboarding/receptionist")
def onboarding_receptionist_submit(
    request: Request,
    escalation_phone: str = Form(...),
    answer_mode: str = Form("backup"),
    business_phone: str = Form(""),
):
    if not should_allow_signup(os.environ):
        return RedirectResponse(DASHBOARD_HOME, status_code=303)
    from channels import normalize_phone

    with Session(engine) as session:
        client = _current_client(request, session)
        if client is None:
            return RedirectResponse("/login", status_code=303)
        if not client.business_name:
            return RedirectResponse("/onboarding/business", status_code=303)
        client.escalation_phone = normalize_phone(escalation_phone)
        client.answer_mode = answer_mode if answer_mode in ("primary", "backup") else "backup"
        client.business_phone = normalize_phone(business_phone)
        session.add(client)
        session.commit()
        # NOT activate_frontdesk: the wizard never buys a number. Roster
        # provisions it from the console once payment is verified / unlocked.
        activate_config_only(session, client)
    return RedirectResponse("/activation/live", status_code=303)
```

Update the stale comment block above `SIGNUP_CLOSED_REDIRECT` (lines ~167–182) to note that the wizard is reopened flag-gated per the 2026-09-02 design, and that the money-spending path is now behind `provisioning.provisioning_allowed`, not behind route closure.

- [ ] **Step 4: Template prefill**

In `agent/templates/onboarding_business.html`, set the field values/placeholders from `defaults` — e.g. `<textarea name="pricing_faq">{{ defaults.pricing_faq }}</textarea>`, `<input name="hours" value="{{ defaults.hours }}">`, `<input name="services" value="{{ defaults.services }}">`. In `onboarding_receptionist.html`, default the `answer_mode` radio to `defaults.answer_mode`.

- [ ] **Step 5: Run to verify pass**

Run: `cd agent && pytest tests/test_portal_onboarding.py tests/test_portal_signup.py tests/test_portal_nav.py -q && pytest -q`
Expected: green. Watch `test_public_surface.py` / `test_portal_nav.py` — if either asserts `/onboarding/business` is a permanent redirect, scope the assertion to "when unflagged".

- [ ] **Step 6: Commit**

```bash
git add agent/portal.py agent/templates/onboarding_business.html agent/templates/onboarding_receptionist.html agent/tests/test_portal_onboarding.py
git commit -m "feat(onboarding): reopen the wizard flag-gated; wire activate_config_only

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: End-to-end acceptance + ROADMAP note + land the spec

**Files:**
- Test: `agent/tests/test_self_serve_acceptance.py` (new)
- Modify: `ROADMAP.md` (the self-serve banner)
- Commit: `docs/superpowers/specs/2026-09-02-self-serve-onboarding-design.md`, `docs/superpowers/plans/2026-09-02-self-serve-phase-1a.md`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the acceptance test (spec §7)**

Create `agent/tests/test_self_serve_acceptance.py`:

```python
"""Spec §7: a founder with SELF_SERVE_SIGNUP=1 completes the wizard and lands
a Business in the same state a founder-created one would be in, minus the
number — and no code path buys that number until provisioning is unlocked."""

import activation
import app as app_module
import db as db_module
from conftest import DASH_AUTH
from db_models import Business, Employee
from fastapi.testclient import TestClient
from sqlmodel import Session, select


def test_signup_to_active_frontdesk_no_number_until_unlocked(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    import portal as portal_module
    monkeypatch.setattr(portal_module, "engine", test_engine)
    monkeypatch.setenv("SELF_SERVE_SIGNUP", "1")

    bought = {"n": 0}

    def _buy(*a, **k):
        bought["n"] += 1
        return {"phone_number": "+15125550123", "sid": "PNx"}

    monkeypatch.setattr(app_module, "buy_twilio_number", _buy)
    monkeypatch.setattr(activation, "buy_twilio_number", _buy)
    monkeypatch.setattr(app_module, "provision_voice", lambda *a, **k: None)
    monkeypatch.setattr(activation, "provision_voice", lambda *a, **k: None)

    c = TestClient(app_module.app)
    c.post("/signup", data={"email": "o@s.com", "password": "a-long-enough-passphrase", "trade": "HVAC"})
    c.post("/onboarding/business", data={
        "business_name": "Kestrel HVAC", "trade": "HVAC", "services": "AC repair",
        "hours": "Mon-Fri 8-5", "pricing_faq": "Diagnostic $89.",
    })
    c.post("/onboarding/receptionist", data={"escalation_phone": "5125550101", "answer_mode": "backup"})

    with Session(test_engine) as s:
        biz = s.exec(select(Business).where(Business.email == "o@s.com")).first()
        assert biz.frontdesk_live is True
        assert biz.inbound_number is None
        assert s.exec(select(Employee).where(Employee.business_id == biz.id)).first() is not None
    assert bought["n"] == 0

    # Founder unlocks, then provisions.
    c.post(f"/clients/{biz.id}/unlock-provisioning", headers=DASH_AUTH)
    c.post(f"/clients/{biz.id}/provision-number", data={"area_code": "512"}, headers=DASH_AUTH)
    with Session(test_engine) as s:
        assert s.get(Business, biz.id).inbound_number == "+15125550123"
    assert bought["n"] == 1
```

- [ ] **Step 2: Run it**

Run: `cd agent && pytest tests/test_self_serve_acceptance.py -q`
Expected: PASS (if the earlier tasks are correct).

- [ ] **Step 3: Update `ROADMAP.md`**

In the top banner section about self-serve, add a dated note:

```markdown
> **AMENDED 2026-09-02 (founder): self-serve onboarding is being reopened,
> flag-gated.** `docs/superpowers/specs/2026-09-02-self-serve-onboarding-design.md`
> is the design. `/signup` + `/onboarding/*` return behind `SELF_SERVE_SIGNUP`
> — founder-only while the first ~10 are hand-onboarded, public once Phase 0
> (Stripe) and a working A2P 10DLC path both exist. The 2026-07-21 metrics bar
> (onboarding time, activation rate, first-week retention) still governs the
> public open. The abuse vector that closed self-serve is now held by
> `provisioning.provisioning_allowed`, not by route closure.
```

- [ ] **Step 4: Run the full suite**

Run: `cd agent && pytest -q && ruff check .`
Expected: all green, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add agent/tests/test_self_serve_acceptance.py ROADMAP.md docs/superpowers/specs/2026-09-02-self-serve-onboarding-design.md docs/superpowers/plans/2026-09-02-self-serve-phase-1a.md
git commit -m "feat(self-serve): Phase 1a acceptance test + ROADMAP amendment + design docs

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- §5.1 routes + flag → Tasks 4, 5, 6. ✅
- §5.2 wizard content + per-trade defaults → Tasks 4, 6. ✅
- §5.3 provisioning precondition + `provisioning_unlocked_at` + founder route → Tasks 1, 3. ✅
- §5.4 tests (all five named) → `test_provisioning_gate` (Task 1/3 covers "requires verified payment" + "founder override"), `test_portal_signup` (Task 5, gating), `test_portal_onboarding` (Task 6, "creates pending Business" — `frontdesk_live` + Employee + no `inbound_number`), `test_onboarding_defaults` (Task 4, prefill). ✅
- §5.5 non-negotiables → Global Constraints + no booking/opt-out files touched. ✅
- §7 acceptance → Task 7. ✅
- Deferred correctly, not in this plan: Stripe (Phase 0), auth holds, velocity limiting, email magic-link, auto-research prefill, async provisioning.

**Placeholder scan:** Template class names in Task 3 Step 5 flagged as "illustrative — inspect the nearby forms" (the founder template's exact classes can't be known without reading it during execution; the instruction is explicit). Everything else is concrete.

**Type consistency:** `provisioning_allowed(business) -> bool` used identically in Tasks 2 and 3. `activate_config_only(session, client)` / `activate_frontdesk(session, client)` signatures consistent Tasks 2, 6. `should_allow_signup(environ) -> bool` and `defaults_for(trade) -> dict` consistent Tasks 4, 5, 6. `provisioning_unlocked_at` / `payment_method_verified_at` column names consistent Tasks 1, 3, 6, 7.
