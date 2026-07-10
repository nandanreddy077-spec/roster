# Self-Serve Signup, Frontdesk-First Activation, Customer Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-shot `/hire` wizard with an email+password self-serve
flow (signup → minimal business questions → Receptionist goes live → outcomes
dashboard → sequential "Your Roster" expansion), matching
`docs/superpowers/specs/2026-07-10-self-serve-signup-dashboard-design.md`.

**Architecture:** New routes live in a dedicated `agent/portal.py` router
(mounted into the existing `app.py`), kept separate from the founder's
Basic-Auth `/clients` routes and their own `SessionMiddleware`-backed cookie
auth. `Client` gains the new fields the spec calls for; existing required
fields get Python-level defaults so a `Client` row can exist mid-onboarding.
Internal agent naming (Frontdesk, Chaser, Rebooker, Renewals, Reviews) is
untouched — a new `agent/roles.py` module is the only place that maps trade →
customer-facing role name and computes roster hire sequencing.

**Tech Stack:** FastAPI, SQLModel/SQLite, Jinja2, Starlette `SessionMiddleware`
(new dependency: `itsdangerous`), `bcrypt` (new dependency) for password
hashing, pytest + `TestClient`.

## Global Constraints

- Internal agent names (Frontdesk, Chaser, Rebooker, Renewals, Reviews) never
  appear in customer-facing templates — only in `agent/roles.py`'s mapping,
  the engine, and the founder's admin dashboard.
- Customer-facing hire copy always uses hiring language ("Hire", "Your
  Roster") — never "add", "install", or "enable".
- No invented statistics on roster cards — outcome-description copy only.
- This plan implements full self-serve activation for the Receptionist
  (Frontdesk) only. Quote Chaser / Retention Manager "Hire" clicks queue the
  request into the existing `Client.requested_roster` field for the founder
  to set up by hand — no automatic target-discovery is built here (see the
  spec's "Explicitly out of scope").
- Trial spend-cap tracking applies to the SMS/Frontdesk path only
  (`service.py`), since live voice (xAI) registration is not yet implemented
  in this codebase (`provisioning.register_number_with_xai` raises
  `NotImplementedError` by design — see `provisioning.py`).
- Founder-facing dashboard (`/clients`, Basic-Auth) is not restructured —
  only gets one new banner (trial cap reached).
- All new customer-facing routes and templates follow the existing repo
  convention: plain `with Session(engine) as session:` blocks (no FastAPI
  `Depends`-based DB injection), matching every existing route in `app.py`.

---

### Task 1: Extend the `Client` model

**Files:**
- Modify: `agent/db_models.py`
- Modify: `agent/db.py` (add migration statements to `_migrate_add_columns`)
- Test: `agent/tests/test_db_models.py` (new)

**Interfaces:**
- Produces: `Client.email`, `Client.password_hash`, `Client.tone`,
  `Client.source`, `Client.source_prompt_dismissed`, `Client.frontdesk_live`,
  `Client.activated_at`, `Client.trial_spend_cents`, `Client.trial_cap_cents`,
  `Client.trial_soft_buffer_cents` — all consumed by every later task.
  `Client.business_name`, `.trade`, `.services_json`, `.hours`,
  `.pricing_faq`, `.escalation_phone` change from required (no default) to
  defaulted (`""` / `"[]"`), so a `Client` row can be created with only
  `email`/`password_hash` set.

- [ ] **Step 1: Write the failing test**

```python
# agent/tests/test_db_models.py
from db_models import Client


def test_client_can_be_created_with_only_email_and_password():
    """Signup creates a Client before any business info is known — every
    previously-required field must have a usable default."""
    client = Client(email="owner@example.com", password_hash="hashed")
    assert client.business_name == ""
    assert client.trade == ""
    assert client.services == []
    assert client.hours == ""
    assert client.pricing_faq == ""
    assert client.escalation_phone == ""


def test_client_trial_and_activation_defaults():
    client = Client(email="owner@example.com", password_hash="hashed")
    assert client.tone == "professional and friendly"
    assert client.source is None
    assert client.source_prompt_dismissed is False
    assert client.frontdesk_live is False
    assert client.activated_at is None
    assert client.trial_spend_cents == 0
    assert client.trial_cap_cents == 2000
    assert client.trial_soft_buffer_cents == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_db_models.py -v`
Expected: FAIL — `TypeError` (missing required positional arguments for
`business_name`, `trade`, etc.) or `AttributeError` on the new fields.

- [ ] **Step 3: Implement the model changes**

In `agent/db_models.py`, replace the `Client` class's field declarations with:

```python
class Client(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    business_name: str = ""
    trade: str = ""
    services_json: str = "[]"  # JSON-encoded list[str]
    hours: str = ""
    pricing_faq: str = ""
    escalation_phone: str = ""
    answer_mode: str = Field(default="backup")  # "primary" or "backup" - set during onboarding
    inbound_number: Optional[str] = None  # the business line customers text/call; routes inbound SMS
    xai_phone_number: Optional[str] = None  # number registered with xAI's Voice Agent API (see xai_voice_adapter.py); unset = no live-voice receptionist configured for this client yet
    xai_signing_secret: Optional[str] = None  # webhook signing secret returned when xai_phone_number was registered (per-number, not account-wide — see provisioning.py)
    twilio_number_sid: Optional[str] = None  # Twilio's SID for the purchased number, needed to later attach it to a SIP trunk
    review_link: Optional[str] = None  # owner's Google/Yelp review URL; unset until they provide one
    referral_incentive: Optional[str] = None  # e.g. "$25 off"; unset = referrals off for this client
    requested_roster: Optional[str] = None  # JSON list of extra roles queued for founder setup — self-serve "Hire" clicks on Quote Chaser/Retention Manager append here (see agent/roles.py), same mechanism the old /hire addons used
    email: Optional[str] = Field(default=None, unique=True, index=True)
    password_hash: Optional[str] = None
    tone: str = "professional and friendly"
    source: Optional[str] = None  # how the owner heard about Roster; asked from the dashboard, not at signup
    source_prompt_dismissed: bool = False
    frontdesk_live: bool = False
    activated_at: Optional[datetime] = None
    trial_spend_cents: int = 0
    trial_cap_cents: int = 2000  # $20 hard cap
    trial_soft_buffer_cents: int = 200  # $2 grace on top of the hard cap — see trial_cap.py
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def services(self) -> List[str]:
        return json.loads(self.services_json)

    def to_config(self) -> ClientConfig:
        return ClientConfig(
            client_id=str(self.id),
            business_name=self.business_name,
            trade=self.trade,
            services=self.services,
            hours=self.hours,
            pricing_faq=self.pricing_faq,
            escalation_phone=self.escalation_phone,
            answer_mode=self.answer_mode,
        )
```

(Only the field declarations and defaults changed; `services`/`to_config` are
unchanged, shown for context.)

In `agent/db.py`, extend `_migrate_add_columns`'s `statements` tuple:

```python
    statements = (
        "ALTER TABLE client ADD COLUMN requested_roster VARCHAR",
        "ALTER TABLE client ADD COLUMN email VARCHAR",
        "ALTER TABLE client ADD COLUMN password_hash VARCHAR",
        "ALTER TABLE client ADD COLUMN tone VARCHAR DEFAULT 'professional and friendly'",
        "ALTER TABLE client ADD COLUMN source VARCHAR",
        "ALTER TABLE client ADD COLUMN source_prompt_dismissed BOOLEAN DEFAULT 0",
        "ALTER TABLE client ADD COLUMN frontdesk_live BOOLEAN DEFAULT 0",
        "ALTER TABLE client ADD COLUMN activated_at DATETIME",
        "ALTER TABLE client ADD COLUMN trial_spend_cents INTEGER DEFAULT 0",
        "ALTER TABLE client ADD COLUMN trial_cap_cents INTEGER DEFAULT 2000",
        "ALTER TABLE client ADD COLUMN trial_soft_buffer_cents INTEGER DEFAULT 200",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent && pytest tests/test_db_models.py -v`
Expected: PASS

- [ ] **Step 5: Run the full existing suite to check nothing else broke**

Run: `cd agent && pytest -v`
Expected: PASS (existing tests construct `Client` with all fields explicitly,
so defaults don't change their behavior)

- [ ] **Step 6: Commit**

```bash
git add agent/db_models.py agent/db.py agent/tests/test_db_models.py
git commit -m "feat: add self-serve signup fields to Client model"
```

---

### Task 2: `agent/roles.py` — customer-facing role names and roster sequencing

**Files:**
- Create: `agent/roles.py`
- Test: `agent/tests/test_roles.py`

**Interfaces:**
- Consumes: nothing (pure module)
- Produces: `receptionist_display_name(trade: str) -> str`,
  `ROSTER_HIRE_ORDER: list[str]`, `ROSTER_DESCRIPTIONS: dict[str, str]`,
  `next_hire(requested_roster: list[str]) -> str | None`,
  `coming_later_after(next_role: str | None) -> str | None`. Used by
  Task 8 (activation), Task 9 (reveal screen), Task 10 (dashboard/roster).

- [ ] **Step 1: Write the failing test**

```python
# agent/tests/test_roles.py
from roles import (
    ROSTER_DESCRIPTIONS,
    ROSTER_HIRE_ORDER,
    coming_later_after,
    next_hire,
    receptionist_display_name,
)


def test_receptionist_name_uses_trade_mapping():
    assert receptionist_display_name("HVAC") == "CSR"
    assert receptionist_display_name("hvac") == "CSR"
    assert receptionist_display_name("Plumbing") == "Receptionist"
    assert receptionist_display_name("Electrical") == "Office Manager"
    assert receptionist_display_name("Roofing") == "Office Coordinator"


def test_receptionist_name_defaults_for_unmapped_trade():
    assert receptionist_display_name("Landscaping") == "Receptionist"
    assert receptionist_display_name("") == "Receptionist"


def test_roster_order_is_chaser_then_retention():
    assert ROSTER_HIRE_ORDER == ["Quote Chaser", "Retention Manager"]
    assert set(ROSTER_DESCRIPTIONS) == set(ROSTER_HIRE_ORDER)


def test_next_hire_is_quote_chaser_when_nothing_requested():
    assert next_hire([]) == "Quote Chaser"


def test_next_hire_is_retention_manager_after_chaser_requested():
    assert next_hire(["Quote Chaser"]) == "Retention Manager"


def test_next_hire_is_none_once_full_roster_requested():
    assert next_hire(["Quote Chaser", "Retention Manager"]) is None


def test_coming_later_follows_next_hire():
    assert coming_later_after("Quote Chaser") == "Retention Manager"
    assert coming_later_after("Retention Manager") is None
    assert coming_later_after(None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_roles.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'roles'`

- [ ] **Step 3: Implement `agent/roles.py`**

```python
"""Maps the internal agent architecture (Frontdesk, Chaser, Rebooker,
Renewals, Reviews) onto the customer-facing employee model. Customers never
see internal agent names — only roles tied to the problem being solved. See
docs/superpowers/specs/2026-07-10-self-serve-signup-dashboard-design.md.
"""
from typing import List, Optional

RECEPTIONIST_TRADE_NAMES = {
    "hvac": "CSR",
    "plumbing": "Receptionist",
    "electrical": "Office Manager",
    "roofing": "Office Coordinator",
}
DEFAULT_RECEPTIONIST_NAME = "Receptionist"


def receptionist_display_name(trade: str) -> str:
    return RECEPTIONIST_TRADE_NAMES.get((trade or "").strip().lower(), DEFAULT_RECEPTIONIST_NAME)


# Fixed hire sequence after the Receptionist (always hired first, at signup).
# "Retention Manager" is the customer-facing name for the internal
# Rebooker + Renewals + Reviews engine — one employee, adaptive behavior
# based on whatever data the business actually has.
ROSTER_HIRE_ORDER = ["Quote Chaser", "Retention Manager"]

ROSTER_DESCRIPTIONS = {
    "Quote Chaser": "Follows up every estimate automatically.",
    "Retention Manager": "Keeps customers coming back — renewals, rebooking, or review asks, whichever fits your business.",
}


def next_hire(requested_roster: List[str]) -> Optional[str]:
    """The single role that's currently hireable. Enforces one-role-at-a-time
    sequencing — a role later in ROSTER_HIRE_ORDER is never offered before
    the ones ahead of it have been requested."""
    for role in ROSTER_HIRE_ORDER:
        if role not in requested_roster:
            return role
    return None


def coming_later_after(next_role: Optional[str]) -> Optional[str]:
    """The role shown greyed-out, informational-only, right after `next_role`."""
    if next_role is None:
        return None
    idx = ROSTER_HIRE_ORDER.index(next_role)
    if idx + 1 < len(ROSTER_HIRE_ORDER):
        return ROSTER_HIRE_ORDER[idx + 1]
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent && pytest tests/test_roles.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/roles.py agent/tests/test_roles.py
git commit -m "feat: add customer-facing role naming and roster hire sequencing"
```

---

### Task 3: `agent/auth.py` — password hashing

**Files:**
- Create: `agent/auth.py`
- Modify: `agent/requirements.txt` (add `bcrypt`)
- Test: `agent/tests/test_auth.py`

**Interfaces:**
- Produces: `hash_password(password: str) -> str`,
  `verify_password(password: str, password_hash: str) -> bool`. Used by
  Task 5 (signup) and Task 6 (login).

- [ ] **Step 1: Write the failing test**

```python
# agent/tests/test_auth.py
from auth import hash_password, verify_password


def test_hash_password_produces_a_verifiable_hash():
    hashed = hash_password("correct-horse-battery-staple")
    assert hashed != "correct-horse-battery-staple"
    assert verify_password("correct-horse-battery-staple", hashed)


def test_verify_password_rejects_wrong_password():
    hashed = hash_password("correct-horse-battery-staple")
    assert not verify_password("wrong-password", hashed)


def test_hash_password_is_salted():
    """Two hashes of the same password must differ (bcrypt salts per-call) —
    guards against someone swapping in a naive unsalted hash later."""
    assert hash_password("same-password") != hash_password("same-password")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'auth'`

- [ ] **Step 3: Add the dependency and implement `agent/auth.py`**

Add to `agent/requirements.txt`:

```
bcrypt>=4.0.0
```

Install it: `cd agent && pip install -r requirements.txt`

Create `agent/auth.py`:

```python
"""Password hashing for the self-serve customer portal (agent/portal.py).
Fully separate from the founder's HTTP-Basic admin auth in app.py — no
shared credential path between the two.
"""
import bcrypt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent && pytest tests/test_auth.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/auth.py agent/requirements.txt agent/tests/test_auth.py
git commit -m "feat: add bcrypt password hashing for customer portal auth"
```

---

### Task 4: Session cookie middleware

**Files:**
- Modify: `agent/app.py` (add `SessionMiddleware`)
- Modify: `agent/requirements.txt` (add `itsdangerous`)
- Modify: `agent/conftest.py` (add a `SESSION_SECRET_KEY` autouse fixture)
- Test: `agent/tests/test_portal_session.py`

**Interfaces:**
- Consumes: nothing new
- Produces: every request gets `request.session` (a dict-like, signed-cookie
  session) once `SessionMiddleware` is installed. Task 5 onward store
  `request.session["client_id"]` there.

- [ ] **Step 1: Write the failing test**

```python
# agent/tests/test_portal_session.py
from fastapi.testclient import TestClient

import app as app_module


def test_session_cookie_is_set_and_readable(monkeypatch):
    """Smoke test for the middleware itself: a route that writes to
    request.session should produce a session cookie the same client can read
    back on a later request."""
    @app_module.app.get("/__test_session_probe__")
    def _probe(request):
        from starlette.requests import Request as StarletteRequest
        assert isinstance(request, StarletteRequest)
        request.session["probe"] = "hello"
        return {"ok": True}

    @app_module.app.get("/__test_session_read__")
    def _read(request):
        return {"probe": request.session.get("probe")}

    client = TestClient(app_module.app)
    client.get("/__test_session_probe__")
    response = client.get("/__test_session_read__")
    assert response.json() == {"probe": "hello"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_portal_session.py -v`
Expected: FAIL — `AssertionError: 'SessionMiddleware must be installed to
access request.session'` (Starlette's own error) or similar.

- [ ] **Step 3: Add the dependency and wire the middleware**

Add to `agent/requirements.txt`:

```
itsdangerous>=2.0.0
```

Install it: `cd agent && pip install -r requirements.txt`

In `agent/app.py`, add the import near the top (with the other `fastapi`/
`starlette` imports):

```python
from starlette.middleware.sessions import SessionMiddleware
```

Immediately after `app = FastAPI(title="Roster")` (before the `app.mount(...)`
line), add:

```python
# Customer-portal session cookie — separate from the founder's HTTP-Basic
# admin auth above. SESSION_SECRET_KEY signs the cookie; a dev fallback keeps
# local runs working without extra setup (unlike ADMIN_PASSWORD, a leaked
# portal session cookie only exposes one customer's own dashboard, not every
# client's data, so this doesn't need to fail closed).
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET_KEY", "dev-only-insecure-secret-change-in-production"),
)
```

In `agent/conftest.py`, add an autouse fixture alongside the existing
`_admin_password` one so tests get a deterministic secret:

```python
@pytest.fixture(autouse=True)
def _session_secret(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-session-secret-key")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent && pytest tests/test_portal_session.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `cd agent && pytest -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agent/app.py agent/requirements.txt agent/conftest.py agent/tests/test_portal_session.py
git commit -m "feat: add session cookie middleware for customer portal"
```

---

### Task 5: `agent/portal.py` router + signup

**Files:**
- Create: `agent/portal.py`
- Create: `agent/templates/signup.html`
- Create: `agent/static/portal.css`
- Modify: `agent/app.py` (mount the router)
- Test: `agent/tests/test_portal_signup.py`

**Interfaces:**
- Consumes: `hash_password` (Task 3), `Client` (Task 1)
- Produces: `portal.router` (a `fastapi.APIRouter`), `GET/POST /signup`. On
  success, `request.session["client_id"]` is set and the browser is
  redirected to `/onboarding/business` (built in Task 7). Later tasks add
  more routes to this same `router`.

- [ ] **Step 1: Write the failing test**

```python
# agent/tests/test_portal_signup.py
from fastapi.testclient import TestClient
from sqlmodel import Session, select

import app as app_module
from db_models import Client


def test_signup_form_renders(monkeypatch):
    client = TestClient(app_module.app)
    response = client.get("/signup")
    assert response.status_code == 200
    assert "Roster" in response.text


def test_signup_creates_client_and_starts_session(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client = TestClient(app_module.app)
    response = client.post(
        "/signup",
        data={"email": "owner@example.com", "password": "hunter22"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/onboarding/business"

    with Session(test_engine) as session:
        created = session.exec(select(Client).where(Client.email == "owner@example.com")).first()
        assert created is not None
        assert created.password_hash != "hunter22"


def test_signup_rejects_duplicate_email(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client = TestClient(app_module.app)
    client.post("/signup", data={"email": "owner@example.com", "password": "hunter22"})
    response = client.post("/signup", data={"email": "owner@example.com", "password": "different"})
    assert response.status_code == 400
    assert "already registered" in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_portal_signup.py -v`
Expected: FAIL — `404` (no `/signup` route yet)

- [ ] **Step 3: Create the portal router, template, and stylesheet**

Create `agent/static/portal.css` (shared by every customer-portal template
added in this and later tasks; reuses the same design tokens as
`hire.css`/`DESIGN.md`):

```css
/* Customer portal — signup, login, onboarding, dashboard. Reuses the same
   design tokens as hire.css/DESIGN.md so the portal reads as one continuous
   surface with the landing page. */
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600&display=swap');

:root {
  --bg: #F7F4EC;
  --bg-alt: #F0EBDF;
  --card: #FFFFFF;
  --border: #E4DDCB;
  --text: #1F1B16;
  --text-dim: #6B6354;
  --accent: #C2693D;
  --accent-dim: #F1DDD0;
  --accent-dark: #A2532F;
  --ink: #17140F;
  --proof: #3F6B4A;
  --proof-dim: #DEE9E1;
  --soon: #A39E8F;
  --serif: "Fraunces", Georgia, "Iowan Old Style", "Times New Roman", serif;
}

* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}
h1, h2, .logo { font-family: var(--serif); font-weight: 600; color: var(--ink); }

.nav { background: var(--card); border-bottom: 1px solid var(--border); padding: 16px 24px; }
.logo { font-size: 20px; text-decoration: none; color: var(--ink); }

.portal-card {
  max-width: 420px;
  margin: 48px auto;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 32px;
}
.portal-card h2 { font-size: 24px; margin-bottom: 8px; }
.portal-card .lede { color: var(--text-dim); margin-bottom: 24px; }

.field { margin-bottom: 16px; }
.field label { display: block; font-size: 14px; margin-bottom: 6px; color: var(--text-dim); }
.field .hint { font-weight: 400; }
.field input, .field select, .field textarea {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  font-size: 15px;
  font-family: inherit;
  background: var(--bg);
  color: var(--text);
}
.field textarea { min-height: 90px; resize: vertical; }

.btn {
  display: inline-block;
  padding: 12px 20px;
  border-radius: 10px;
  border: none;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  text-decoration: none;
  text-align: center;
}
.btn-primary { background: var(--accent); color: #fff; width: 100%; }
.btn-primary:hover { background: var(--accent-dark); }
.btn-secondary { background: var(--bg-alt); color: var(--text); border: 1px solid var(--border); }

.err { color: #A8331F; font-size: 14px; margin-top: 12px; min-height: 1.2em; }
.portal-foot { text-align: center; margin-top: 20px; font-size: 14px; color: var(--text-dim); }
.portal-foot a { color: var(--accent); }
```

Create `agent/portal.py`:

```python
"""Self-serve customer portal: signup, login, onboarding, activation, and the
customer-facing dashboard. Fully separate from the founder's HTTP-Basic
/clients routes in app.py — session-cookie auth only (see SessionMiddleware
in app.py), one client per logged-in session.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from auth import hash_password, verify_password
from db import engine
from db_models import Client

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter()


def _current_client(request: Request, session: Session) -> Optional[Client]:
    client_id = request.session.get("client_id")
    if client_id is None:
        return None
    return session.get(Client, client_id)


@router.get("/signup")
def signup_form(request: Request):
    trade = request.query_params.get("trade", "")
    return templates.TemplateResponse(request, "signup.html", {"error": None, "email": "", "trade": trade})


@router.post("/signup")
def signup_submit(request: Request, email: str = Form(...), password: str = Form(...), trade: str = Form("")):
    email = email.strip().lower()
    with Session(engine) as session:
        existing = session.exec(select(Client).where(Client.email == email)).first()
        if existing:
            return templates.TemplateResponse(
                request,
                "signup.html",
                {"error": "That email's already registered — try logging in instead.", "email": email, "trade": trade},
                status_code=400,
            )
        client = Client(email=email, password_hash=hash_password(password))
        session.add(client)
        session.commit()
        session.refresh(client)
        request.session["client_id"] = client.id
        if trade.strip():
            request.session["prefill_trade"] = trade.strip()
    return RedirectResponse("/onboarding/business", status_code=303)
```

In `agent/app.py`, add the import near the other local module imports:

```python
from portal import router as portal_router
```

Immediately after `sms_channel = get_channel()` (before `init_db()`), add:

```python
app.include_router(portal_router)
```

Create `agent/templates/signup.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Hire your AI employee — Roster</title>
<meta name="robots" content="noindex">
<link rel="stylesheet" href="/static/portal.css">
</head>
<body>
<header class="nav"><a class="logo" href="/">Roster</a></header>

<div class="portal-card">
  <h2>Hire your AI employee</h2>
  <p class="lede">Create your account — we'll ask a couple of quick questions about your business next.</p>

  <form method="post" action="/signup">
    <input type="hidden" name="trade" value="{{ trade }}">
    <div class="field">
      <label for="email">Email</label>
      <input type="email" id="email" name="email" value="{{ email }}" required autocomplete="email">
    </div>
    <div class="field">
      <label for="password">Password</label>
      <input type="password" id="password" name="password" required autocomplete="new-password" minlength="8">
    </div>
    <button type="submit" class="btn btn-primary">Create account</button>
    <div class="err">{{ error or "" }}</div>
  </form>

  <p class="portal-foot">Already hired your team? <a href="/login">Log in</a></p>
</div>
</body>
</html>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent && pytest tests/test_portal_signup.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/portal.py agent/templates/signup.html agent/static/portal.css agent/app.py agent/tests/test_portal_signup.py
git commit -m "feat: add self-serve signup route"
```

---

### Task 6: Login / logout

**Files:**
- Modify: `agent/portal.py` (add login/logout routes)
- Create: `agent/templates/login.html`
- Test: `agent/tests/test_portal_login.py`

**Interfaces:**
- Consumes: `verify_password` (Task 3), `_current_client` (Task 5)
- Produces: `GET/POST /login`, `POST /logout`

- [ ] **Step 1: Write the failing test**

```python
# agent/tests/test_portal_login.py
from fastapi.testclient import TestClient

import app as app_module


def test_login_form_renders():
    client = TestClient(app_module.app)
    response = client.get("/login")
    assert response.status_code == 200


def test_login_succeeds_and_redirects_to_dashboard(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client = TestClient(app_module.app)
    client.post("/signup", data={"email": "owner@example.com", "password": "hunter22"})
    client.post("/logout")

    response = client.post(
        "/login", data={"email": "owner@example.com", "password": "hunter22"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"


def test_login_rejects_wrong_password(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client = TestClient(app_module.app)
    client.post("/signup", data={"email": "owner@example.com", "password": "hunter22"})

    response = client.post("/login", data={"email": "owner@example.com", "password": "wrong"})
    assert response.status_code == 400
    assert "Invalid email or password" in response.text


def test_login_rejects_unknown_email(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client = TestClient(app_module.app)
    response = client.post("/login", data={"email": "nobody@example.com", "password": "whatever"})
    assert response.status_code == 400
    assert "Invalid email or password" in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_portal_login.py -v`
Expected: FAIL — `404` on `/login`

- [ ] **Step 3: Add the routes and template**

Add to `agent/portal.py` (below the signup routes):

```python
@router.get("/login")
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None, "email": ""})


@router.post("/login")
def login_submit(request: Request, email: str = Form(...), password: str = Form(...)):
    email = email.strip().lower()
    with Session(engine) as session:
        client = session.exec(select(Client).where(Client.email == email)).first()
        if client is None or not client.password_hash or not verify_password(password, client.password_hash):
            return templates.TemplateResponse(
                request,
                "login.html",
                {"error": "Invalid email or password.", "email": email},
                status_code=400,
            )
        request.session["client_id"] = client.id
    return RedirectResponse("/dashboard", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
```

Create `agent/templates/login.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Log in — Roster</title>
<meta name="robots" content="noindex">
<link rel="stylesheet" href="/static/portal.css">
</head>
<body>
<header class="nav"><a class="logo" href="/">Roster</a></header>

<div class="portal-card">
  <h2>Welcome back</h2>
  <p class="lede">Log in to see how your team's doing.</p>

  <form method="post" action="/login">
    <div class="field">
      <label for="email">Email</label>
      <input type="email" id="email" name="email" value="{{ email }}" required autocomplete="email">
    </div>
    <div class="field">
      <label for="password">Password</label>
      <input type="password" id="password" name="password" required autocomplete="current-password">
    </div>
    <button type="submit" class="btn btn-primary">Log in</button>
    <div class="err">{{ error or "" }}</div>
  </form>

  <p class="portal-foot">Haven't hired your team yet? <a href="/signup">Get started</a></p>
</div>
</body>
</html>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent && pytest tests/test_portal_login.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/portal.py agent/templates/login.html agent/tests/test_portal_login.py
git commit -m "feat: add login and logout routes"
```

---

### Task 7: Onboarding step 1 — business basics

**Files:**
- Modify: `agent/portal.py`
- Create: `agent/templates/onboarding_business.html`
- Test: `agent/tests/test_portal_onboarding.py`

**Interfaces:**
- Consumes: `_current_client` (Task 5)
- Produces: `GET/POST /onboarding/business`. On success, saves
  `business_name`, `trade`, `services_json`, `hours` on the session's client
  and redirects to `/onboarding/receptionist` (Task 8).

- [ ] **Step 1: Write the failing test**

```python
# agent/tests/test_portal_onboarding.py
from fastapi.testclient import TestClient
from sqlmodel import Session

import app as app_module
from db_models import Client


def _signed_up_client(client: TestClient):
    client.post("/signup", data={"email": "owner@example.com", "password": "hunter22"})


def test_onboarding_business_requires_login(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client = TestClient(app_module.app)
    response = client.get("/onboarding/business", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_onboarding_business_saves_and_redirects(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client = TestClient(app_module.app)
    _signed_up_client(client)

    response = client.post(
        "/onboarding/business",
        data={
            "business_name": "Ridgeline Plumbing",
            "trade": "Plumbing",
            "services": "Drains, water heaters",
            "hours": "Mon-Sat 7am-7pm",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/onboarding/receptionist"

    with Session(test_engine) as session:
        saved = session.exec(select_client_by_email("owner@example.com", session))
    assert saved.business_name == "Ridgeline Plumbing"
    assert saved.trade == "Plumbing"
    assert saved.services == ["Drains", "water heaters"]
    assert saved.hours == "Mon-Sat 7am-7pm"


def select_client_by_email(email, session):
    from sqlmodel import select
    return session.exec(select(Client).where(Client.email == email)).first()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_portal_onboarding.py -v`
Expected: FAIL — `404` on `/onboarding/business`

- [ ] **Step 3: Add the routes and template**

Add to `agent/portal.py`:

```python
@router.get("/onboarding/business")
def onboarding_business_form(request: Request):
    with Session(engine) as session:
        client = _current_client(request, session)
        if client is None:
            return RedirectResponse("/login", status_code=303)
        if client.frontdesk_live:
            return RedirectResponse("/dashboard", status_code=303)
    prefill_trade = request.session.get("prefill_trade", "")
    return templates.TemplateResponse(request, "onboarding_business.html", {"prefill_trade": prefill_trade})


@router.post("/onboarding/business")
def onboarding_business_submit(
    request: Request,
    business_name: str = Form(...),
    trade: str = Form(...),
    services: str = Form(...),
    hours: str = Form(...),
):
    service_list = [s.strip() for s in services.split(",") if s.strip()]
    with Session(engine) as session:
        client = _current_client(request, session)
        if client is None:
            return RedirectResponse("/login", status_code=303)
        client.business_name = business_name.strip()
        client.trade = trade.strip()
        client.services_json = json.dumps(service_list)
        client.hours = hours.strip()
        session.add(client)
        session.commit()
    return RedirectResponse("/onboarding/receptionist", status_code=303)
```

Create `agent/templates/onboarding_business.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Tell us about your business — Roster</title>
<meta name="robots" content="noindex">
<link rel="stylesheet" href="/static/portal.css">
</head>
<body>
<header class="nav"><a class="logo" href="/">Roster</a></header>

<div class="portal-card">
  <h2>Tell us about your business</h2>
  <p class="lede">Just what a new hire needs on day one — everything else you can edit later.</p>

  <form method="post" action="/onboarding/business">
    <div class="field">
      <label for="business_name">What's your business called?</label>
      <input type="text" id="business_name" name="business_name" required autocomplete="organization" placeholder="e.g. Ridgeline Plumbing">
    </div>
    <div class="field">
      <label for="trade">Your trade</label>
      <select id="trade" name="trade" required>
        <option value="" disabled {{ '' if prefill_trade else 'selected' }}>Choose one…</option>
        {% for t in ["Plumbing", "HVAC", "Electrical", "Roofing", "Landscaping", "Pest control", "Other"] %}
        <option value="{{ t }}" {{ 'selected' if t.lower() == prefill_trade.lower() else '' }}>{{ t }}</option>
        {% endfor %}
      </select>
    </div>
    <div class="field">
      <label for="services">What services do you offer? <span class="hint">— separate with commas</span></label>
      <input type="text" id="services" name="services" required placeholder="Drain cleaning, water heaters, leak repair...">
    </div>
    <div class="field">
      <label for="hours">Your hours</label>
      <input type="text" id="hours" name="hours" required placeholder="Mon–Sat 7am–7pm">
    </div>
    <button type="submit" class="btn btn-primary">Continue</button>
  </form>
</div>
</body>
</html>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent && pytest tests/test_portal_onboarding.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/portal.py agent/templates/onboarding_business.html agent/tests/test_portal_onboarding.py
git commit -m "feat: add onboarding business-basics step"
```

---

### Task 8: `agent/activation.py` + onboarding step 2 (receptionist question + go-live)

**Files:**
- Create: `agent/activation.py`
- Modify: `agent/portal.py`
- Create: `agent/templates/onboarding_receptionist.html`
- Test: `agent/tests/test_activation.py`, additions to
  `agent/tests/test_portal_onboarding.py`

**Interfaces:**
- Consumes: `Client` (Task 1), `provisioning.buy_twilio_number`,
  `attach_number_to_xai_trunk`, `register_number_with_xai`,
  `ProvisioningError` (existing `provisioning.py`)
- Produces: `activate_frontdesk(session: Session, client: Client) -> None`
  (sets `frontdesk_live=True`, `activated_at`, provisions a number
  best-effort). `GET/POST /onboarding/receptionist`, which calls it and
  redirects to `/activation/live` (Task 9).

- [ ] **Step 1: Write the failing test**

```python
# agent/tests/test_activation.py
from datetime import datetime

from sqlmodel import Session

from activation import activate_frontdesk
from db_models import Client


def test_activate_frontdesk_marks_live_even_without_twilio_creds(test_engine, monkeypatch):
    """No TWILIO_ACCOUNT_SID/TOKEN in dev/test — activation must still
    complete (SMS/voice number can be provisioned later by the founder),
    matching the tolerance the existing /clients/{id}/provision-number route
    already has for ProvisioningError."""
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)

    with Session(test_engine) as session:
        client = Client(email="owner@example.com", password_hash="x", business_name="Ridgeline")
        session.add(client)
        session.commit()
        session.refresh(client)

        activate_frontdesk(session, client)

        assert client.frontdesk_live is True
        assert isinstance(client.activated_at, datetime)
        assert client.inbound_number is None  # no creds → nothing purchased, but still live


def test_activate_frontdesk_skips_provisioning_if_number_already_set(test_engine):
    with Session(test_engine) as session:
        client = Client(
            email="owner@example.com", password_hash="x", business_name="Ridgeline",
            inbound_number="+15550001111",
        )
        session.add(client)
        session.commit()
        session.refresh(client)

        activate_frontdesk(session, client)

        assert client.inbound_number == "+15550001111"
        assert client.frontdesk_live is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_activation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'activation'`

- [ ] **Step 3: Implement `agent/activation.py`**

```python
"""Self-serve activation: goes from a filled-in onboarding profile to a live
Frontdesk. Mirrors the tolerance app.py's founder-facing
/clients/{id}/provision-number route already has for xAI's voice
registration not being implemented yet (see provisioning.py) — SMS-only is
still a fully working Frontdesk, and a missing Twilio number is something the
founder can provision later from the admin dashboard.
"""
from datetime import datetime

from sqlmodel import Session

from db_models import Client
from provisioning import ProvisioningError, attach_number_to_xai_trunk, buy_twilio_number, register_number_with_xai


def activate_frontdesk(session: Session, client: Client) -> None:
    if not client.inbound_number:
        try:
            purchase = buy_twilio_number()
            attach_number_to_xai_trunk(purchase["sid"])
            client.inbound_number = purchase["phone_number"]
            client.twilio_number_sid = purchase["sid"]
            try:
                xai_registration = register_number_with_xai(purchase["phone_number"])
                client.xai_phone_number = purchase["phone_number"]
                client.xai_signing_secret = xai_registration["signing_secret"]
            except NotImplementedError:
                pass  # SMS-only for now; voice needs a manual xAI step — see provisioning.py
        except ProvisioningError:
            pass  # No number purchased (e.g. no Twilio creds in dev) — still go live;
                  # founder can provision a number later from /clients/{id}.

    client.frontdesk_live = True
    client.activated_at = datetime.utcnow()
    session.add(client)
    session.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent && pytest tests/test_activation.py -v`
Expected: PASS

- [ ] **Step 5: Write the failing test for the onboarding/receptionist route**

Append to `agent/tests/test_portal_onboarding.py`:

```python
def test_onboarding_receptionist_redirects_to_business_if_not_done(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client = TestClient(app_module.app)
    _signed_up_client(client)

    response = client.get("/onboarding/receptionist", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/onboarding/business"


def test_onboarding_receptionist_activates_and_redirects(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    client = TestClient(app_module.app)
    _signed_up_client(client)
    client.post(
        "/onboarding/business",
        data={"business_name": "Ridgeline Plumbing", "trade": "Plumbing", "services": "Drains", "hours": "9-5"},
    )

    response = client.post(
        "/onboarding/receptionist", data={"escalation_phone": "(555) 555-0101"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/activation/live"

    with Session(test_engine) as session:
        saved = select_client_by_email("owner@example.com", session)
    assert saved.escalation_phone == "(555) 555-0101"
    assert saved.frontdesk_live is True
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd agent && pytest tests/test_portal_onboarding.py -v`
Expected: FAIL — `404` on `/onboarding/receptionist`

- [ ] **Step 7: Add the routes and template**

Add to `agent/portal.py` (add `from activation import activate_frontdesk` to
the imports at the top):

```python
@router.get("/onboarding/receptionist")
def onboarding_receptionist_form(request: Request):
    with Session(engine) as session:
        client = _current_client(request, session)
        if client is None:
            return RedirectResponse("/login", status_code=303)
        if client.frontdesk_live:
            return RedirectResponse("/dashboard", status_code=303)
        if not client.business_name:
            return RedirectResponse("/onboarding/business", status_code=303)
    return templates.TemplateResponse(request, "onboarding_receptionist.html", {})


@router.post("/onboarding/receptionist")
def onboarding_receptionist_submit(request: Request, escalation_phone: str = Form(...)):
    with Session(engine) as session:
        client = _current_client(request, session)
        if client is None:
            return RedirectResponse("/login", status_code=303)
        if not client.business_name:
            return RedirectResponse("/onboarding/business", status_code=303)
        client.escalation_phone = escalation_phone.strip()
        client.pricing_faq = (
            "Escalation: Hand off to the owner for emergencies and any caller "
            "who asks for a person; handle everything else."
        )
        session.add(client)
        session.commit()
        activate_frontdesk(session, client)
    return RedirectResponse("/activation/live", status_code=303)
```

Create `agent/templates/onboarding_receptionist.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>One last thing — Roster</title>
<meta name="robots" content="noindex">
<link rel="stylesheet" href="/static/portal.css">
</head>
<body>
<header class="nav"><a class="logo" href="/">Roster</a></header>

<div class="portal-card">
  <h2>One last thing</h2>
  <p class="lede">Where should we reach you if something needs your attention — an emergency, or a caller who asks for a person?</p>

  <form method="post" action="/onboarding/receptionist">
    <div class="field">
      <label for="escalation_phone">Your cell <span class="hint">— never shared with customers</span></label>
      <input type="tel" id="escalation_phone" name="escalation_phone" required autocomplete="tel" placeholder="(555) 555-0142">
    </div>
    <button type="submit" class="btn btn-primary">Hire</button>
  </form>
</div>
</body>
</html>
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd agent && pytest tests/test_portal_onboarding.py tests/test_activation.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add agent/activation.py agent/portal.py agent/templates/onboarding_receptionist.html agent/tests/test_activation.py agent/tests/test_portal_onboarding.py
git commit -m "feat: activate Frontdesk self-serve after the receptionist question"
```

---

### Task 9: Activation reveal screen

**Files:**
- Modify: `agent/portal.py`
- Create: `agent/templates/activation_live.html`
- Test: `agent/tests/test_activation_live.py`

**Interfaces:**
- Consumes: `receptionist_display_name` (Task 2), `_current_client` (Task 5)
- Produces: `GET /activation/live`

- [ ] **Step 1: Write the failing test**

```python
# agent/tests/test_activation_live.py
from fastapi.testclient import TestClient

import app as app_module


def _fully_onboarded_client(client: TestClient):
    client.post("/signup", data={"email": "owner@example.com", "password": "hunter22"})
    client.post(
        "/onboarding/business",
        data={"business_name": "Ridgeline Plumbing", "trade": "Plumbing", "services": "Drains", "hours": "9-5"},
    )
    client.post("/onboarding/receptionist", data={"escalation_phone": "(555) 555-0101"})


def test_activation_live_redirects_if_not_yet_live(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client = TestClient(app_module.app)
    client.post("/signup", data={"email": "owner@example.com", "password": "hunter22"})

    response = client.get("/activation/live", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/onboarding/business"


def test_activation_live_shows_role_name_and_checklist(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    client = TestClient(app_module.app)
    _fully_onboarded_client(client)

    response = client.get("/activation/live")
    assert response.status_code == 200
    assert "Receptionist" in response.text  # Plumbing -> "Receptionist" per roles.py
    assert "Ridgeline Plumbing" in response.text
    assert "Answer" in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_activation_live.py -v`
Expected: FAIL — `404` on `/activation/live`

- [ ] **Step 3: Add the route and template**

Add to `agent/portal.py` (add `from roles import receptionist_display_name`
to the imports):

```python
@router.get("/activation/live")
def activation_live(request: Request):
    with Session(engine) as session:
        client = _current_client(request, session)
        if client is None:
            return RedirectResponse("/login", status_code=303)
        if not client.frontdesk_live:
            return RedirectResponse("/onboarding/business", status_code=303)
        role_name = receptionist_display_name(client.trade)
        return templates.TemplateResponse(
            request, "activation_live.html", {"client": client, "role_name": role_name}
        )
```

Create `agent/templates/activation_live.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>You're live — Roster</title>
<meta name="robots" content="noindex">
<link rel="stylesheet" href="/static/portal.css">
</head>
<body>
<header class="nav"><a class="logo" href="/">Roster</a></header>

<div class="portal-card">
  <h2>{{ role_name }} is live 🎉</h2>
  {% if client.inbound_number %}
  <p class="lede">Your new number: <strong>{{ client.inbound_number }}</strong></p>
  {% endif %}
  <p class="lede">We're now answering calls for {{ client.business_name }}.</p>

  <p style="font-weight:600; margin-bottom:8px;">What happens now</p>
  <ul style="margin-bottom:24px; padding-left:20px;">
    <li>✓ We'll answer every incoming call.</li>
    <li>✓ We'll book jobs.</li>
    <li>✓ We'll text confirmations.</li>
    <li>✓ You can edit anything later.</li>
  </ul>

  {% if client.inbound_number %}
  <a class="btn btn-primary" href="tel:{{ client.inbound_number }}">📞 Call your {{ role_name }}</a>
  {% endif %}
  <a class="btn btn-secondary" href="/dashboard" style="display:block; text-align:center; margin-top:12px;">Go to dashboard</a>
</div>
</body>
</html>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent && pytest tests/test_activation_live.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/portal.py agent/templates/activation_live.html agent/tests/test_activation_live.py
git commit -m "feat: add the you're-live activation reveal screen"
```

---

### Task 10: Customer dashboard (zero-state, outcomes, Your Roster)

**Files:**
- Modify: `agent/portal.py`
- Create: `agent/templates/dashboard.html`
- Test: `agent/tests/test_portal_dashboard.py`

**Interfaces:**
- Consumes: `receptionist_display_name`, `ROSTER_DESCRIPTIONS`, `next_hire`,
  `coming_later_after` (Task 2), `_current_client` (Task 5), `Job`, `Message`
  (existing `db_models.py`)
- Produces: `GET /dashboard`, `POST /roster/hire`

- [ ] **Step 1: Write the failing test**

```python
# agent/tests/test_portal_dashboard.py
import json

from fastapi.testclient import TestClient
from sqlmodel import Session, select

import app as app_module
from db_models import Client, Job


def _fully_onboarded_client(client: TestClient, monkeypatch):
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    client.post("/signup", data={"email": "owner@example.com", "password": "hunter22"})
    client.post(
        "/onboarding/business",
        data={"business_name": "Ridgeline Plumbing", "trade": "Plumbing", "services": "Drains", "hours": "9-5"},
    )
    client.post("/onboarding/receptionist", data={"escalation_phone": "(555) 555-0101"})


def test_dashboard_requires_login(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client = TestClient(app_module.app)
    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_dashboard_zero_state_before_any_jobs(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client = TestClient(app_module.app)
    _fully_onboarded_client(client, monkeypatch)

    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "waiting for your first call" in response.text
    assert "Quote Chaser" in response.text  # hire-next card


def test_dashboard_shows_outcomes_once_a_job_exists(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client = TestClient(app_module.app)
    _fully_onboarded_client(client, monkeypatch)

    with Session(test_engine) as session:
        owner = session.exec(select(Client).where(Client.email == "owner@example.com")).first()
        session.add(Job(client_id=owner.id, service_type="drain cleaning", urgency="routine"))
        session.commit()

    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "waiting for your first call" not in response.text


def test_roster_hire_queues_quote_chaser(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client = TestClient(app_module.app)
    _fully_onboarded_client(client, monkeypatch)

    response = client.post("/roster/hire", data={"role": "Quote Chaser"}, follow_redirects=False)
    assert response.status_code == 303

    with Session(test_engine) as session:
        owner = session.exec(select(Client).where(Client.email == "owner@example.com")).first()
        assert json.loads(owner.requested_roster) == ["Quote Chaser"]

    response = client.get("/dashboard")
    assert "Retention Manager" in response.text  # now the next hire-next card


def test_roster_hire_rejects_out_of_order_role(monkeypatch, test_engine):
    """Can't hire Retention Manager before Quote Chaser — enforces sequencing."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    client = TestClient(app_module.app)
    _fully_onboarded_client(client, monkeypatch)

    client.post("/roster/hire", data={"role": "Retention Manager"})

    with Session(test_engine) as session:
        owner = session.exec(select(Client).where(Client.email == "owner@example.com")).first()
        assert owner.requested_roster is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_portal_dashboard.py -v`
Expected: FAIL — `404` on `/dashboard`

- [ ] **Step 3: Add the routes and template**

Add to `agent/portal.py` (add these imports at the top:
`from sqlmodel import func` alongside the existing `sqlmodel` import, and
`from db_models import Job, Message` alongside `Client`, and
`from roles import ROSTER_DESCRIPTIONS, coming_later_after, next_hire,
receptionist_display_name`):

```python
@router.get("/dashboard")
def dashboard(request: Request):
    with Session(engine) as session:
        client = _current_client(request, session)
        if client is None:
            return RedirectResponse("/login", status_code=303)
        if not client.frontdesk_live:
            return RedirectResponse("/onboarding/business", status_code=303)

        job_count = session.exec(select(func.count(Job.id)).where(Job.client_id == client.id)).one()
        assistant_message_count = session.exec(
            select(func.count(Message.id)).where(Message.client_id == client.id, Message.role == "assistant")
        ).one()
        has_data = job_count > 0 or assistant_message_count > 0

        requested = json.loads(client.requested_roster) if client.requested_roster else []
        next_role = next_hire(requested)
        coming_later = coming_later_after(next_role)

        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "client": client,
                "role_name": receptionist_display_name(client.trade),
                "has_data": has_data,
                "job_count": job_count,
                "next_role": next_role,
                "next_role_description": ROSTER_DESCRIPTIONS.get(next_role, ""),
                "coming_later": coming_later,
            },
        )


@router.post("/roster/hire")
def roster_hire(request: Request, role: str = Form(...)):
    with Session(engine) as session:
        client = _current_client(request, session)
        if client is None:
            return RedirectResponse("/login", status_code=303)
        requested = json.loads(client.requested_roster) if client.requested_roster else []
        if role == next_hire(requested):
            requested.append(role)
            client.requested_roster = json.dumps(requested)
            session.add(client)
            session.commit()
    return RedirectResponse("/dashboard", status_code=303)
```

Create `agent/templates/dashboard.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dashboard — Roster</title>
<meta name="robots" content="noindex">
<link rel="stylesheet" href="/static/portal.css">
</head>
<body>
<header class="nav">
  <a class="logo" href="/">Roster</a>
  <form method="post" action="/logout" style="display:inline; float:right;">
    <button type="submit" class="btn btn-secondary" style="padding:6px 12px;">Log out</button>
  </form>
</header>

<div class="portal-card" style="max-width:560px;">
  <h2>{{ client.business_name }}</h2>

  {% if not has_data %}
  <p class="lede">{{ role_name }}: Live — waiting for your first call.</p>
  {% if client.inbound_number %}
  <a class="btn btn-primary" href="tel:{{ client.inbound_number }}">📞 Call your {{ role_name }}</a>
  {% endif %}
  {% else %}
  <p class="lede">{{ role_name }}: Live</p>
  <p style="font-size:28px; font-weight:600; margin-bottom:4px;">{{ job_count }}</p>
  <p style="color:var(--text-dim); margin-bottom:24px;">jobs booked</p>
  {% endif %}
</div>

<div class="portal-card" style="max-width:560px;">
  <h2 style="font-size:20px;">Your Roster</h2>
  <p style="margin:12px 0;">✅ {{ role_name }} — Live</p>

  {% if next_role %}
  <div style="border-top:1px solid var(--border); padding-top:16px; margin-top:8px;">
    <p style="color:var(--text-dim); font-size:13px;">Hire next</p>
    <p style="font-weight:600;">{{ next_role }}</p>
    <p style="color:var(--text-dim); margin-bottom:12px;">{{ next_role_description }}</p>
    <form method="post" action="/roster/hire">
      <input type="hidden" name="role" value="{{ next_role }}">
      <button type="submit" class="btn btn-primary" style="width:auto; padding:10px 20px;">Hire</button>
    </form>
  </div>
  {% endif %}

  {% if coming_later %}
  <div style="border-top:1px solid var(--border); padding-top:16px; margin-top:16px; opacity:0.55;">
    <p style="color:var(--text-dim); font-size:13px;">Coming later</p>
    <p style="font-weight:600;">{{ coming_later }}</p>
  </div>
  {% endif %}
</div>
</body>
</html>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent && pytest tests/test_portal_dashboard.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/portal.py agent/templates/dashboard.html agent/tests/test_portal_dashboard.py
git commit -m "feat: add customer dashboard with zero-state, outcomes, and Your Roster"
```

---

### Task 11: Attribution banner ("where'd you hear about us")

**Files:**
- Modify: `agent/portal.py`
- Modify: `agent/templates/dashboard.html`
- Test: additions to `agent/tests/test_portal_dashboard.py`

**Interfaces:**
- Consumes: `Client.source`, `.source_prompt_dismissed`, `.activated_at`
  (Task 1)
- Produces: `POST /dashboard/source`; `dashboard()` gains a
  `show_source_banner` context flag.

- [ ] **Step 1: Write the failing test**

Append to `agent/tests/test_portal_dashboard.py`:

```python
from datetime import datetime, timedelta

from db_models import Client as ClientModel


def test_source_banner_hidden_before_three_days(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client = TestClient(app_module.app)
    _fully_onboarded_client(client, monkeypatch)

    response = client.get("/dashboard")
    assert "Where" not in response.text or "hear about Roster" not in response.text


def test_source_banner_shown_after_three_days(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client = TestClient(app_module.app)
    _fully_onboarded_client(client, monkeypatch)

    with Session(test_engine) as session:
        owner = session.exec(select(ClientModel).where(ClientModel.email == "owner@example.com")).first()
        owner.activated_at = datetime.utcnow() - timedelta(days=4)
        session.add(owner)
        session.commit()

    response = client.get("/dashboard")
    assert "hear about Roster" in response.text


def test_dismissing_source_banner_hides_it_going_forward(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client = TestClient(app_module.app)
    _fully_onboarded_client(client, monkeypatch)

    with Session(test_engine) as session:
        owner = session.exec(select(ClientModel).where(ClientModel.email == "owner@example.com")).first()
        owner.activated_at = datetime.utcnow() - timedelta(days=4)
        session.add(owner)
        session.commit()

    client.post("/dashboard/source", data={"source": "A friend recommended us"})

    with Session(test_engine) as session:
        owner = session.exec(select(ClientModel).where(ClientModel.email == "owner@example.com")).first()
        assert owner.source == "A friend recommended us"
        assert owner.source_prompt_dismissed is True

    response = client.get("/dashboard")
    assert "hear about Roster" not in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_portal_dashboard.py -v`
Expected: FAIL — banner text never appears (no such feature yet), and
`/dashboard/source` is `404`.

- [ ] **Step 3: Add the route and update `dashboard()`**

In `agent/portal.py`, update the `dashboard` route body to also compute
`show_source_banner` (`datetime`/`timedelta` are already imported at the top
of `portal.py` from Task 5 — no new import needed):

```python
        show_source_banner = (
            client.source is None
            and not client.source_prompt_dismissed
            and client.activated_at is not None
            and datetime.utcnow() - client.activated_at >= timedelta(days=3)
        )

        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "client": client,
                "role_name": receptionist_display_name(client.trade),
                "has_data": has_data,
                "job_count": job_count,
                "next_role": next_role,
                "next_role_description": ROSTER_DESCRIPTIONS.get(next_role, ""),
                "coming_later": coming_later,
                "show_source_banner": show_source_banner,
            },
        )
```

Add the new route:

```python
@router.post("/dashboard/source")
def set_source(request: Request, source: str = Form("")):
    with Session(engine) as session:
        client = _current_client(request, session)
        if client is None:
            return RedirectResponse("/login", status_code=303)
        source = source.strip()
        if source:
            client.source = source
        client.source_prompt_dismissed = True
        session.add(client)
        session.commit()
    return RedirectResponse("/dashboard", status_code=303)
```

In `agent/templates/dashboard.html`, add this block right after the
`<header class="nav">...</header>` line:

```html
{% if show_source_banner %}
<div class="portal-card" style="max-width:560px; padding:16px 24px;">
  <form method="post" action="/dashboard/source" style="display:flex; gap:8px; align-items:center;">
    <span style="flex:1;">Quick question — where'd you hear about Roster?</span>
    <input type="text" name="source" placeholder="e.g. Google, a friend..." style="flex:1; padding:8px; border:1px solid var(--border); border-radius:8px;">
    <button type="submit" class="btn btn-secondary" style="width:auto; padding:8px 16px;">Send</button>
  </form>
</div>
{% endif %}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent && pytest tests/test_portal_dashboard.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/portal.py agent/templates/dashboard.html agent/tests/test_portal_dashboard.py
git commit -m "feat: add deferred where'd-you-hear-about-us attribution banner"
```

---

### Task 12: Trial spend cap enforcement

**Files:**
- Create: `agent/trial_cap.py`
- Modify: `agent/service.py`
- Modify: `agent/app.py` (`_process_inbound_sms`, `inbound_sms`, add
  `twiml_empty`)
- Modify: `agent/templates/client_detail.html` (founder-facing banner)
- Test: `agent/tests/test_trial_cap.py`, additions to
  `agent/tests/test_public_surface.py`

**Interfaces:**
- Consumes: `Client.trial_spend_cents`, `.trial_cap_cents`,
  `.trial_soft_buffer_cents` (Task 1)
- Produces: `can_respond(client: Client) -> bool`,
  `record_usage(session: Session, client: Client, cost_cents: int = 50) ->
  None`. `service.handle_customer_message` returns `{"reply": None, "jobs":
  []}` once the soft buffer is exhausted instead of calling the (paid) agent.

- [ ] **Step 1: Write the failing test**

```python
# agent/tests/test_trial_cap.py
from sqlmodel import Session

from db_models import Client
from trial_cap import TRIAL_TURN_COST_CENTS, can_respond, record_usage


def _client(**overrides):
    defaults = dict(email="owner@example.com", password_hash="x", business_name="Ridgeline")
    defaults.update(overrides)
    return Client(**defaults)


def test_can_respond_true_when_under_cap():
    client = _client(trial_spend_cents=0, trial_cap_cents=2000, trial_soft_buffer_cents=200)
    assert can_respond(client) is True


def test_can_respond_true_within_soft_buffer():
    client = _client(trial_spend_cents=2050, trial_cap_cents=2000, trial_soft_buffer_cents=200)
    assert can_respond(client) is True


def test_can_respond_false_once_soft_buffer_exhausted():
    client = _client(trial_spend_cents=2200, trial_cap_cents=2000, trial_soft_buffer_cents=200)
    assert can_respond(client) is False


def test_record_usage_increments_spend(test_engine):
    with Session(test_engine) as session:
        client = _client()
        session.add(client)
        session.commit()
        session.refresh(client)

        record_usage(session, client)

        assert client.trial_spend_cents == TRIAL_TURN_COST_CENTS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_trial_cap.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trial_cap'`

- [ ] **Step 3: Implement `agent/trial_cap.py`**

```python
"""Trial spend cap for self-serve signups. A caller (soft-buffer exhausted)
is never silently dropped mid-conversation; the buffer exists so one
expensive call doesn't cut off a live customer the moment the hard cap is
crossed. See docs/superpowers/specs/2026-07-10-self-serve-signup-dashboard-design.md.
"""
from sqlmodel import Session

from db_models import Client

# Flat per-turn estimate, not real per-token billing — matches the founder's
# own ~$0.30-0.60-per-call all-in cost research (Twilio + orchestration +
# Anthropic) closely enough to be a meaningful cap, while staying
# deterministic and easy to test.
TRIAL_TURN_COST_CENTS = 50


def can_respond(client: Client) -> bool:
    return client.trial_spend_cents < (client.trial_cap_cents + client.trial_soft_buffer_cents)


def record_usage(session: Session, client: Client, cost_cents: int = TRIAL_TURN_COST_CENTS) -> None:
    client.trial_spend_cents += cost_cents
    session.add(client)
    session.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent && pytest tests/test_trial_cap.py -v`
Expected: PASS

- [ ] **Step 5: Write the failing test for `service.handle_customer_message`**

Create `agent/tests/test_service_trial_cap.py`:

```python
# agent/tests/test_service_trial_cap.py
from sqlmodel import Session

from conftest import StubAgent
from db_models import Client
import service


def test_handle_customer_message_skips_agent_when_cap_exhausted(test_engine, monkeypatch):
    monkeypatch.setattr(
        service,
        "agent",
        StubAgent({"reply": "should not be used", "jobs": [], "new_messages": []}),
    )
    with Session(test_engine) as session:
        client = Client(
            email="owner@example.com", password_hash="x", business_name="Ridgeline",
            trade="Plumbing", services_json="[]", hours="9-5", escalation_phone="555",
            trial_spend_cents=2200, trial_cap_cents=2000, trial_soft_buffer_cents=200,
        )
        session.add(client)
        session.commit()
        session.refresh(client)

        result = service.handle_customer_message(session, client, "+15550001111", "hello")

        assert result["reply"] is None
        assert result["jobs"] == []
        assert client.trial_spend_cents == 2200  # unchanged — no call was made


def test_handle_customer_message_runs_agent_and_records_usage_when_under_cap(test_engine, monkeypatch):
    monkeypatch.setattr(
        service,
        "agent",
        StubAgent({"reply": "Hi there!", "jobs": [], "new_messages": []}),
    )
    with Session(test_engine) as session:
        client = Client(
            email="owner@example.com", password_hash="x", business_name="Ridgeline",
            trade="Plumbing", services_json="[]", hours="9-5", escalation_phone="555",
        )
        session.add(client)
        session.commit()
        session.refresh(client)

        result = service.handle_customer_message(session, client, "+15550001111", "hello")

        assert result["reply"] == "Hi there!"
        assert client.trial_spend_cents == 50
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd agent && pytest tests/test_service_trial_cap.py -v`
Expected: FAIL — `AssertionError` (reply is not `None`; cap isn't enforced
yet)

- [ ] **Step 7: Update `agent/service.py`**

Add `from trial_cap import can_respond, record_usage` to the imports, then
replace `handle_customer_message` with:

```python
def handle_customer_message(
    session: Session, client: Client, customer_phone: str, text: str
) -> Dict[str, Any]:
    """Run one customer turn through the agent. Persists messages and any jobs.

    Returns {"reply": str | None, "jobs": list[Job]}. `reply` is None once the
    client's trial spend cap (hard cap + soft buffer) is exhausted — the
    inbound message is still recorded, but no paid model call is made and no
    reply is sent. Does not send anything itself — the caller decides how the
    reply leaves the building (TwiML, REST, or UI).
    """
    history = _load_history(session, client.id, customer_phone)
    history.append({"role": "user", "content": [{"type": "text", "text": text}]})

    session.add(
        Message(
            client_id=client.id,
            customer_phone=customer_phone,
            role="user",
            content_json=json.dumps(text),
        )
    )
    session.commit()

    if not can_respond(client):
        return {"reply": None, "jobs": []}

    result = agent.respond(client.to_config(), history)
    record_usage(session, client)

    # The engine produced the full turn (assistant tool_use, tool_result, final
    # reply); persist each so the next turn loads valid alternating history.
    for nm in result["new_messages"]:
        session.add(
            Message(
                client_id=client.id,
                customer_phone=customer_phone,
                role=nm["role"],
                content_json=json.dumps(nm["content"]),
            )
        )

    captured: List[Job] = []
    for call in result["jobs"]:
        ji = call["input"]
        job = Job(
            client_id=client.id,
            customer_phone=customer_phone,
            customer_name=ji.get("customer_name"),
            service_type=ji["service_type"],
            urgency=ji["urgency"],
            address=ji.get("address"),
            callback_number=ji.get("callback_number") or customer_phone,
            notes=ji.get("notes"),
        )
        session.add(job)
        captured.append(job)

    session.commit()
    return {"reply": result["reply"], "jobs": captured}
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd agent && pytest tests/test_service_trial_cap.py -v`
Expected: PASS

- [ ] **Step 9: Write the failing test for the SMS webhook's empty-reply handling**

Append to `agent/tests/test_public_surface.py`:

```python
def test_sms_webhook_stays_silent_once_trial_cap_exhausted(monkeypatch, test_engine):
    monkeypatch.setenv("ADMIN_PASSWORD", "hunter2")
    monkeypatch.setattr(app_module, "engine", test_engine)
    from sqlmodel import Session

    from db_models import Client

    with Session(test_engine) as session:
        session.add(
            Client(
                email="owner@example.com", password_hash="x", business_name="Ridgeline",
                trade="Plumbing", services_json="[]", hours="9-5", escalation_phone="555",
                inbound_number="+15559998888",
                trial_spend_cents=2200, trial_cap_cents=2000, trial_soft_buffer_cents=200,
            )
        )
        session.commit()

    client = TestClient(app_module.app)
    response = client.post(
        "/webhook/sms",
        data={"From": "+15550001111", "To": "+15559998888", "Body": "hello"},
    )
    assert response.status_code == 200
    assert "<Message>" not in response.text
```

- [ ] **Step 10: Run test to verify it fails**

Run: `cd agent && pytest tests/test_public_surface.py -v -k trial_cap`
Expected: FAIL — `AttributeError` (`.replace` called on `None` inside
`twiml_reply`)

- [ ] **Step 11: Update `agent/app.py`**

Add this helper next to the existing `twiml_reply` function:

```python
def twiml_empty() -> Response:
    return Response(content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>', media_type="application/xml")
```

Update the webhook route:

```python
@app.post("/webhook/sms")
async def inbound_sms(From: str = Form(...), To: str = Form(...), Body: str = Form(...)):
    reply = await run_in_threadpool(_process_inbound_sms, From, To, Body)
    if reply is None:
        return twiml_empty()
    return twiml_reply(reply)
```

- [ ] **Step 12: Run test to verify it passes**

Run: `cd agent && pytest tests/test_public_surface.py -v`
Expected: PASS

- [ ] **Step 13: Add the founder-facing banner**

In `agent/templates/client_detail.html`, immediately after the existing
`{% if provision_error %}...{% endif %}` block (around line 37), add:

```html
{% if client.trial_spend_cents >= client.trial_cap_cents %}
<p class="job-notes">⚠ Trial cap reached (${{ "%.2f"|format(client.trial_spend_cents / 100) }} spent, ${{ "%.2f"|format((client.trial_cap_cents + client.trial_soft_buffer_cents) / 100) }} hard stop) — raise the cap or move this client to paid.</p>
{% endif %}
```

- [ ] **Step 14: Run the full suite**

Run: `cd agent && pytest -v`
Expected: PASS

- [ ] **Step 15: Commit**

```bash
git add agent/trial_cap.py agent/service.py agent/app.py agent/templates/client_detail.html agent/tests/test_trial_cap.py agent/tests/test_service_trial_cap.py agent/tests/test_public_surface.py
git commit -m "feat: enforce trial spend cap with soft buffer, never silently drop a live caller"
```

---

### Task 13: Retire `/hire`, repoint landing CTAs to `/signup`

**Files:**
- Modify: `agent/app.py` (remove `/hire` and `/hire/done/{client_id}` routes
  and `_build_hire_sample`)
- Delete: `agent/templates/hire.html`, `agent/templates/hire_done.html`
- Delete: `agent/static/hire.css`
- Modify: `agent/landing/index.html` (repoint the 3 `/hire` links to
  `/signup`)
- Modify: `agent/tests/test_public_surface.py` (replace the two `/hire`
  tests with `/signup` equivalents)

**Interfaces:**
- Consumes: nothing new
- Produces: nothing new — this is cleanup, no new interfaces for later
  tasks (this is the last task in the plan).

- [ ] **Step 1: Update the existing `/hire`-testing tests to test `/signup` instead**

In `agent/tests/test_public_surface.py`, replace
`test_hire_flow_is_public` and `test_hire_submit_is_public` with:

```python
def test_signup_flow_is_public(monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    client = TestClient(app_module.app)
    response = client.get("/signup")
    assert response.status_code == 200
    assert "Roster" in response.text


def test_signup_submit_is_public(monkeypatch, test_engine):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr(app_module, "engine", test_engine)
    client = TestClient(app_module.app)
    response = client.post(
        "/signup",
        data={"email": "auth-test@example.com", "password": "hunter22"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/onboarding/business"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_public_surface.py -v`
Expected: These two pass already (Task 5 built `/signup`) — this step just
confirms the replacement tests are equivalent in strength to what they
replace, not a fresh red/green cycle.

- [ ] **Step 3: Remove the `/hire` routes and helper from `agent/app.py`**

Delete the entire block from the `# ---- Self-serve hire flow (public)
-------` comment through the end of the `hire_done` function (roughly
`app.py` lines 113–235 as read during planning — re-locate by searching for
`_build_hire_sample`, `/hire`, and `hire_done` to be safe against any drift).

- [ ] **Step 4: Delete the retired template and stylesheet files**

```bash
rm agent/templates/hire.html agent/templates/hire_done.html agent/static/hire.css
```

- [ ] **Step 5: Repoint the landing page's CTAs**

In `agent/landing/index.html`, change:
- `href="/hire"` → `href="/signup"` (line ~34)
- `href="/hire?trade=HVAC"` → `href="/signup?trade=HVAC"` (line ~293)
- `href="/hire?trade=Plumbing"` → `href="/signup?trade=Plumbing"` (line ~299)

- [ ] **Step 6: Run the full suite**

Run: `cd agent && pytest -v`
Expected: PASS (all tests, including the founder-dashboard and webhook
tests, which don't touch `/hire`)

- [ ] **Step 7: Commit**

```bash
git add -A agent/app.py agent/templates agent/static agent/landing/index.html agent/tests/test_public_surface.py
git commit -m "chore: retire /hire wizard in favor of self-serve signup"
```

---

## Final verification

After Task 13, run the entire suite once more from the `agent/` directory:

```bash
cd agent && pytest -v
```

Expected: all tests pass, including every test file created across this
plan (`test_db_models.py`, `test_roles.py`, `test_auth.py`,
`test_portal_session.py`, `test_portal_signup.py`, `test_portal_login.py`,
`test_portal_onboarding.py`, `test_activation.py`, `test_activation_live.py`,
`test_portal_dashboard.py`, `test_trial_cap.py`,
`test_service_trial_cap.py`) plus every pre-existing test file.
