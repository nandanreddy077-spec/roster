import asyncio
import base64
import json
import os
import secrets
import sys
from datetime import datetime
from pathlib import Path

# ponytail: naive KEY=VALUE parser, no quotes/multiline support — switch to
# python-dotenv if .env grows past that.
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _key, _, _value = _line.partition("=")
        os.environ.setdefault(_key.strip(), _value.strip())

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, delete, func, select
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from auth import ACCESS_LINK_MAX_AGE_SECONDS, make_access_token, resolve_session_secret
from bookings import parse_money_cents
from call_trace import CallTrace
from channels import get_channel, normalize_phone
import departments
from db import DATA_DIR, engine, init_db
from deployment import deploy_department, deploy_role
from eventbus import bus
from events import JOB_COMPLETED, DomainEvent
from expansion import mark_actioned, open_interests_for
import metrics
from notifications import is_test_thread
from locks import conversation_lock
from db_models import (
    BILLING_PAID,
    BILLING_TRIAL,
    AccessRequest,
    Business,
    Customer,
    DepartmentInterest,
    Employee,
    Event,
    Job,
    Message,
    OwnerNotification,
    RecoveryCampaign,
    RecoveryJob,
    RecoveryMessageLog,
    ReferralLead,
    WebhookDelivery,
)
from portal import router as portal_router
from provisioning import (
    ProvisioningError,
    buy_twilio_number,
    provision_voice,
    public_base_url,
    verify_voice_wiring,
)
from recovery_engine import FACE_DISPLAY_NAMES
from membership_service import find_active_membership_offer, handle_membership_reply
from recovery_service import create_campaign, find_active_recovery_job, handle_recovery_reply
from referral_service import find_active_referral_ask, handle_referral_reply
from review_service import find_active_review_ask, handle_review_reply
from runner import dispatch_job_completed
from service import handle_customer_message
from xai_voice_adapter import (
    parse_incoming_call_webhook as parse_xai_incoming_call,
    run_call as run_xai_call,
    verify_webhook_signature as verify_xai_signature,
)


# Credentials whose absence silently degrades a money path (no AI replies, no
# SMS sends, no voice, data on ephemeral SQLite). SESSION_SECRET_KEY and
# ADMIN_PASSWORD already fail closed elsewhere; these warn loudly instead of
# crashing so a deliberate partial deploy (e.g. pre-KYC, no Twilio yet) still
# boots — but never silently.
_PRODUCTION_CRITICAL_ENV = (
    "ANTHROPIC_API_KEY",  # no agent replies at all
    "DATABASE_URL",  # falls back to local SQLite: single-writer + dies with the container
    "TWILIO_ACCOUNT_SID",  # outbound SMS prints to console instead of sending
    "TWILIO_AUTH_TOKEN",
    "XAI_API_KEY",  # live-voice calls die at connect
    "PUBLIC_BASE_URL",  # webhooks register against the wrong host
)


def warn_missing_production_env(environ) -> list:
    """Names every critical env var missing in production (empty list in dev).
    Caller prints them; kept pure for testability."""
    if environ.get("ROSTER_ENV") != "production":
        return []
    return [v for v in _PRODUCTION_CRITICAL_ENV if not environ.get(v)]


def should_start_scheduler(environ) -> bool:
    """The in-process tick scheduler (recovery_tick.run, on a timer) must
    only ever run in production — never during the test suite or local dev,
    which would otherwise spin up a live loop hitting Twilio/Anthropic/the
    real database just because this module was imported. Kept pure for
    testability, same convention as warn_missing_production_env."""
    return environ.get("ROSTER_ENV") == "production"


def scheduler_interval_seconds(environ) -> int:
    """How often the scheduler ticks. Configurable via TICK_INTERVAL_SECONDS;
    a malformed override falls back to the safe default rather than crashing
    boot."""
    from scheduler import DEFAULT_INTERVAL_SECONDS

    raw = environ.get("TICK_INTERVAL_SECONDS")
    if raw is None:
        return DEFAULT_INTERVAL_SECONDS
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_INTERVAL_SECONDS


def session_cookie_kwargs(environ) -> dict:
    """Cookie-security flags for the portal session cookie. SameSite=Lax keeps
    the cookie off cross-site POSTs (CSRF defense for the dashboard's
    state-changing forms). Secure (https_only) is enabled in production so the
    session cookie can never ride over plaintext HTTP, and left off in dev so
    http://localhost sign-in still works."""
    return {
        "same_site": "lax",
        "https_only": environ.get("ROSTER_ENV") == "production",
    }


BASE_DIR = Path(__file__).parent
LANDING_DIR = BASE_DIR / "landing"
DASHBOARD_THREAD = "dashboard"
# Every inbound voice webhook + its whole event stream is captured here as
# {call_id}.jsonl, so the first real calls leave a replayable ground-truth log.
VOICE_CAPTURE_DIR = DATA_DIR / "call_captures"

# docs_url/redoc_url/openapi_url off: FastAPI's defaults publish a complete map
# of every founder and portal route to anyone who asks. The routes themselves
# are authenticated, so this is not a hole — it's handing an attacker the
# floor plan for free (2026-08-04 penetration test, recommendation 3). Roster
# has no external API consumers; nobody needs the schema.
app = FastAPI(title="Roster", docs_url=None, redoc_url=None, openapi_url=None)


class HeadAsGetMiddleware:
    """This FastAPI version doesn't add HEAD to GET-only routes (every route
    405s on HEAD, confirmed 2026-08-10), which trips crawlers/uptime checks
    that probe with HEAD before GET. Rewrite HEAD to GET for routing, then
    drop the body — one middleware covers every route, present and future,
    instead of listing methods=["GET", "HEAD"] on each."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "HEAD":
            return await self.app(scope, receive, send)

        scope = {**scope, "method": "GET"}

        async def send_no_body(message):
            if message["type"] == "http.response.body":
                message = {**message, "body": b""}
            await send(message)

        await self.app(scope, receive, send_no_body)


app.add_middleware(HeadAsGetMiddleware)
# Customer-portal session cookie — separate from the founder's HTTP-Basic
# admin auth above. SESSION_SECRET_KEY signs the cookie. In production
# (ROSTER_ENV=production) the app fails closed if it's unset — like
# ADMIN_PASSWORD — so a deploy never silently runs on the shared dev secret.
# In dev it falls back to an insecure default so local runs need no setup
# (see resolve_session_secret above). Cookie flags (SameSite=Lax, Secure in
# production) come from session_cookie_kwargs.
app.add_middleware(
    SessionMiddleware,
    secret_key=resolve_session_secret(os.environ),
    **session_cookie_kwargs(os.environ),
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
# The founder's job list showed test bookings as real work while the customer's
# tagged them (audit C4). Same public predicate both surfaces use, exposed to
# the template rather than reaching for notifications' private set.
templates.env.globals["is_test_thread"] = is_test_thread
sms_channel = get_channel()
app.include_router(portal_router)

init_db()

for _missing in warn_missing_production_env(os.environ):
    print(
        f"[PRODUCTION WARNING] {_missing} is not set — see app.py:_PRODUCTION_CRITICAL_ENV "
        "for what silently breaks without it.",
        file=sys.stderr,
    )


# ---- Background tick scheduler ----------------------------------------------
# Runs recovery_tick.run() (Lead Qualifier, Dispatcher, Quote Chaser
# enrollment, Reviews, Referral) on an interval inside this same process —
# not a separate Railway service, which on the current single-service SQLite
# volume would have no access to the real database at all. Production only
# (should_start_scheduler); the test suite and local dev never spin this up.
@app.on_event("startup")
async def _start_background_scheduler() -> None:
    if not should_start_scheduler(os.environ):
        return
    import recovery_tick
    from scheduler import run_scheduler

    task = asyncio.create_task(
        run_scheduler(recovery_tick.run, scheduler_interval_seconds(os.environ))
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


# Strong reference: an un-awaited create_task result nobody holds can be
# garbage-collected mid-loop, silently killing the scheduler.
_background_tasks: set = set()


# ---- Dashboard auth --------------------------------------------------------
# The founder dashboard (/clients and everything under it) holds every shop's
# conversations and jobs — it must never be public. HTTP Basic behind a single
# ADMIN_PASSWORD env var: enough for a one-founder ops tool, no login system to
# build. Fail-closed: if ADMIN_PASSWORD isn't set, the dashboard refuses to
# serve rather than silently opening up (forgetting an env var on a fresh
# deploy must not expose client data). Public surface stays public: the
# landing (/, /styles.css), the self-serve signup flow (/signup*), /static, and
# the Twilio/xAI webhooks (which authenticate their own way — routing by known
# number, xAI signature verification).
PROTECTED_PREFIX = "/clients"


def _authorized(header: str | None, expected_password: str) -> bool:
    if not header or not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[len("Basic ") :]).decode()
        _, _, password = decoded.partition(":")
    except Exception:
        return False
    return secrets.compare_digest(password, expected_password)


@app.middleware("http")
async def dashboard_auth(request: Request, call_next):
    if request.url.path == PROTECTED_PREFIX or request.url.path.startswith(PROTECTED_PREFIX + "/"):
        expected = os.environ.get("ADMIN_PASSWORD", "")
        if not expected:
            return Response(
                "Dashboard locked: set the ADMIN_PASSWORD environment variable to enable access.",
                status_code=503,
            )
        if not _authorized(request.headers.get("authorization"), expected):
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Roster dashboard"'},
            )
    return await call_next(request)


def extract_display_text(content) -> str:
    if isinstance(content, str):
        return content
    parts = [block["text"] for block in content if block.get("type") == "text"]
    return " ".join(parts).strip()


def twiml_reply(body: str) -> Response:
    safe = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    xml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{safe}</Message></Response>'
    return Response(content=xml, media_type="application/xml")


def twiml_empty() -> Response:
    return Response(
        content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
        media_type="application/xml",
    )


# ---- Public landing page ---------------------------------------------------
# The marketing site is served by this same app (Railway's root directory is
# agent/, so files outside it never reach the deployed image). index.html links
# its stylesheet as a relative "styles.css", which resolves to /styles.css when
# served at the root — hence the dedicated route beside it.


@app.get("/")
def root():
    return FileResponse(LANDING_DIR / "index-v2.html", media_type="text/html")


@app.get("/styles.css")
def landing_styles():
    return FileResponse(LANDING_DIR / "styles.css", media_type="text/css")


@app.get("/roster")
def roster_page():
    return FileResponse(LANDING_DIR / "roster.html", media_type="text/html")


# Nothing was pointing crawlers at the site or telling them what not to index
# (/clients is credentialed but a Disallow keeps it out of results entirely;
# /preview is a duplicate alias of "/"). Two evergreen pages exist today.
_SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://rosterhires.com/</loc></url>
<url><loc>https://rosterhires.com/roster</loc></url>
</urlset>
"""

_ROBOTS_TXT = """User-agent: *
Disallow: /clients
Disallow: /preview

Sitemap: https://rosterhires.com/sitemap.xml
"""


@app.get("/robots.txt")
def robots_txt():
    return Response(content=_ROBOTS_TXT, media_type="text/plain")


@app.get("/sitemap.xml")
def sitemap_xml():
    return Response(content=_SITEMAP_XML, media_type="application/xml")


# Kept as an alias after the homepage rewrite shipped (root() above now serves
# index-v2.html directly). Spec:
# docs/superpowers/specs/2026-07-27-homepage-craft-pass-design.md


@app.get("/preview")
def landing_preview():
    return FileResponse(LANDING_DIR / "index-v2.html", media_type="text/html")


@app.get("/styles-v2.css")
def landing_preview_styles():
    return FileResponse(LANDING_DIR / "styles-v2.css", media_type="text/css")


# The FOUNDER's wording for each department state. The customer portal keeps
# its own map over the same states — departments.DepartmentStatus is
# deliberately presentation-neutral so neither audience's voice leaks into the
# other's screen.
_FOUNDER_STATE_LABELS = {
    "staffed": "Staffed",
    "partial": "Partially staffed",
    "empty": "Not staffed",
    "unavailable": "No employees built yet",
}


def _employees_by_business(session, business_ids: list) -> dict:
    """One query for every listed business's employees, grouped in Python.

    A per-business query here would be an N+1 over the whole client list — the
    same trap the recovery counts above already avoid with a grouped query.
    test_ops_console asserts the query count so this can't quietly regress.
    """
    if not business_ids:
        return {}
    rows = session.exec(select(Employee).where(Employee.business_id.in_(business_ids))).all()
    grouped = {bid: [] for bid in business_ids}
    for e in rows:
        grouped.setdefault(e.business_id, []).append(e)
    return grouped


def _founder_department_rows(employees: list) -> list:
    """The shared department facts, paired with the FOUNDER's wording.

    The computation lives in departments.department_status_for so the customer
    dashboard answers "what's deployed?" with the identical code over the
    identical rows. Only the labels are ours.
    """
    return [
        {"status": s, "label": _FOUNDER_STATE_LABELS[s.state]}
        for s in departments.department_status_for(employees)
    ]


def _setup_checklist(client: Business, department_rows: list, real_job_count: int) -> list:
    """ "Is this shop actually live?" answered in one place, in the order the
    founder does the work, so setup stops being a memory game across five
    scattered panels.

    Every item is derived from a fact already on the row — never from a
    "marked done" flag someone could tick optimistically. The last item is
    deliberately the hardest and cannot be faked: until a real (non-test) job
    exists, this business has never actually done its job, and the checklist
    says so no matter how green everything above it is.
    """
    staffed = [r for r in department_rows if r["status"].state in ("staffed", "partial")]
    return [
        {
            "label": "Business details captured",
            "done": bool(client.business_name and client.trade and client.hours),
            "hint": "Name, trade, hours and pricing — what Frontdesk answers from.",
        },
        {
            "label": "AI phone number provisioned",
            "done": bool(client.inbound_number),
            "hint": "Buy & wire up a number below. Without one there is no SMS and no voice.",
        },
        {
            "label": "Live voice registered with xAI",
            "done": bool(client.xai_phone_number),
            "hint": "SMS works without this; answering an actual phone call does not.",
        },
        {
            "label": "A department staffed",
            "done": bool(staffed),
            "hint": "Deploy at least one department, or nothing runs for this business.",
        },
        {
            "label": "Owner can reach their dashboard",
            "done": bool(client.email),
            "hint": "Send the access link below. Setting their email adds Google sign-in as a backup.",
        },
        {
            "label": "Answered a real customer",
            "done": real_job_count > 0,
            "hint": "Not done until a real call or text books a job. Test bookings don't count.",
        },
    ]


def _access_link(client: Business) -> str:
    """The URL the founder sends the owner so they can see their dashboard.

    Always absolute: this gets pasted into a text message, where a relative
    path is useless. Falls back to the SAME constant every other public-URL
    consumer uses (provisioning's webhook registration, the Twilio signature
    check) rather than inventing a second answer to "where does this app
    live" — if that fallback were ever wrong, inbound SMS would already be
    failing its signature check, so a link built from it can't be wrong alone.
    """
    base = public_base_url().rstrip("/")
    return f"{base}/access/{make_access_token(client.id, os.environ)}"


@app.get("/clients")
def list_clients(request: Request):
    with Session(engine) as session:
        clients = session.exec(select(Business).order_by(Business.created_at.desc())).all()
        employees_by_business = _employees_by_business(session, [c.id for c in clients])
        staffed_departments = {
            c.id: departments.active_departments_for(employees_by_business.get(c.id, []))
            for c in clients
        }
        count_rows = session.exec(
            select(RecoveryCampaign.business_id, func.count(RecoveryCampaign.id)).group_by(
                RecoveryCampaign.business_id
            )
        ).all()
        recovery_counts = {client_id: count for client_id, count in count_rows}
        access_requests = session.exec(
            select(AccessRequest).order_by(AccessRequest.created_at.desc())
        ).all()
    return templates.TemplateResponse(
        request,
        "clients.html",
        {
            "clients": clients,
            "recovery_counts": recovery_counts,
            "access_requests": access_requests,
            "staffed_departments": staffed_departments,
        },
    )


# ---- Request early access (public landing conversion) ----------------------
# The landing's only inbound path while Twilio KYC is pending: an owner fills
# this, we store the lead, and the founder follows up from /clients. Public
# (no auth) — it's on the marketing site. Replaces self-serve signup + the
# phone-number CTAs for now.
@app.post("/request-access")
def request_access(
    name: str = Form(...),
    phone: str = Form(...),
    trade: str = Form(""),
    business_name: str = Form(""),
):
    with Session(engine) as session:
        session.add(
            AccessRequest(
                name=name.strip(),
                phone=phone.strip(),
                trade=trade.strip(),
                business_name=business_name.strip(),
            )
        )
        session.commit()
    return RedirectResponse("/thanks", status_code=303)


@app.get("/thanks")
def request_access_thanks(request: Request):
    return templates.TemplateResponse(request, "request_thanks.html", {})


@app.get("/clients/new")
def new_client_form(request: Request):
    return templates.TemplateResponse(
        request,
        "new_client.html",
        {
            "deployable_departments": [
                d
                for d in departments.hireable_departments()
                if departments.deployable_employees_for(d.key)
            ]
        },
    )


@app.post("/clients/new")
def create_client(
    business_name: str = Form(...),
    trade: str = Form(...),
    services: str = Form(...),
    hours: str = Form(...),
    pricing_faq: str = Form(...),
    escalation_phone: str = Form(...),
    answer_mode: str = Form("backup"),
    inbound_number: str = Form(""),
    business_phone: str = Form(""),
    department_key: str = Form(""),
    owner_email: str = Form(""),
):
    """The single business-creation path in the product once Phase 7 retires
    /signup — so it must capture everything the retired onboarding wizard
    collected, plus the department the discovery call recommended.

    A department that can't be staffed does NOT lose the business: the
    business is the valuable thing on this form, and the deploy error is
    reported on its page instead. Same rule for the owner's email: a duplicate
    is reported, never a 500 that discards a whole discovery call's notes.
    """
    service_list = [s.strip() for s in services.split(",") if s.strip()]
    email = owner_email.strip().lower() or None
    error = None
    access_error = None
    with Session(engine) as session:
        if email and session.exec(select(Business).where(Business.email == email)).first():
            # Business.email is unique. Keep the business, drop the collision:
            # the founder fixes the address from the client page, which is a
            # far smaller loss than discarding a discovery call's notes.
            access_error = (
                f"{email} is already on another business — created without an owner email. "
                "Set the right one below to enable Google sign-in."
            )
            email = None
        client = Business(
            business_name=business_name,
            trade=trade,
            services_json=json.dumps(service_list),
            hours=hours,
            pricing_faq=pricing_faq,
            escalation_phone=normalize_phone(escalation_phone),
            answer_mode=answer_mode,
            inbound_number=normalize_phone(inbound_number) or None,
            business_phone=normalize_phone(business_phone),
            email=email,
        )
        session.add(client)
        session.commit()
        session.refresh(client)
        # Read the id BEFORE deploying: deploy_role commits, which expires
        # every instance in this session, and `client` is detached once the
        # block exits — touching client.id afterwards would raise.
        client_id = client.id
        if department_key:
            try:
                deploy_department(session, client_id, department_key)
            except ValueError as e:
                error = str(e)

    from urllib.parse import urlencode

    params = {k: v for k, v in (("deploy_error", error), ("access_error", access_error)) if v}
    redirect_url = f"/clients/{client_id}"
    if params:
        redirect_url += f"?{urlencode(params)}"
    return RedirectResponse(redirect_url, status_code=303)


@app.post("/clients/{client_id}/owner-email")
def set_owner_email(client_id: int, owner_email: str = Form("")):
    """The owner's email is not a login on its own — Roster provisions every
    customer by hand and never sets a password for them. It's what lets the
    owner use Google sign-in as a second door, so a lost access link isn't a
    support ticket."""
    email = owner_email.strip().lower() or None
    access_error = None
    with Session(engine) as session:
        client = session.get(Business, client_id)
        if client is None:
            raise HTTPException(status_code=404, detail="No such client")
        taken = (
            session.exec(select(Business).where(Business.email == email)).first() if email else None
        )
        if taken is not None and taken.id != client_id:
            access_error = f"{email} is already on another business."
        else:
            client.email = email
            session.add(client)
            session.commit()

    redirect_url = f"/clients/{client_id}"
    if access_error:
        from urllib.parse import quote

        redirect_url += f"?access_error={quote(access_error)}"
    return RedirectResponse(redirect_url, status_code=303)


@app.post("/clients/{client_id}/referral-incentive")
def set_referral_incentive(client_id: int, referral_incentive: str = Form(...)):
    with Session(engine) as session:
        client = session.get(Business, client_id)
        client.referral_incentive = referral_incentive.strip() or None
        session.add(client)
        session.commit()
    return RedirectResponse(f"/clients/{client_id}", status_code=303)


@app.post("/clients/{client_id}/membership-plan")
def set_membership_plan(client_id: int, membership_plan: str = Form(...)):
    """The owner's plan in their own words — quoted verbatim into the offer
    text, so this field is the whole of what Membership Agent is allowed to
    say about the plan. Blank switches the employee off without firing it."""
    with Session(engine) as session:
        client = session.get(Business, client_id)
        client.membership_plan = membership_plan.strip() or None
        session.add(client)
        session.commit()
    return RedirectResponse(f"/clients/{client_id}", status_code=303)


@app.post("/clients/{client_id}/provision-number")
def provision_number(client_id: int, area_code: str = Form("")):
    """Buys a Twilio number, registers it with xAI for voice, and attaches it
    to the shared xAI-origination trunk in one click. If the xAI half fails
    (e.g. missing XAI_API_KEY or an xAI-side error), the Twilio half is kept —
    SMS agents work immediately — and the error is reported so voice can be
    retried later, rather than losing the purchased number."""
    error = None
    with Session(engine) as session:
        client = session.get(Business, client_id)
        # This route BUYS a number every time it runs. POST-redirect-GET stops
        # a refresh from re-submitting, but not a double-click while the first
        # request is still in flight — that bought two numbers, and the spare
        # was never released. Refuse when one is already on file; the xAI half
        # is retried via retry-xai-registration, which buys nothing.
        if client is not None and client.twilio_number_sid:
            from urllib.parse import quote

            already = (
                f"{client.business_name or 'This business'} already has "
                f"{client.inbound_number or 'a number'} — not buying another. "
                "Use 'Retry xAI voice registration' to finish wiring it up."
            )
            return RedirectResponse(
                f"/clients/{client_id}?provision_error={quote(already)}", status_code=303
            )
        try:
            purchase = buy_twilio_number(area_code.strip() or None)
            client.inbound_number = purchase["phone_number"]
            client.twilio_number_sid = purchase["sid"]
            session.add(client)
            session.commit()

            # Voice half — see provisioning.provision_voice for why the signing
            # secret is persisted before the trunk work rather than after.
            voice_error = provision_voice(session, client)
            if voice_error:
                error = (
                    f"Number {purchase['phone_number']} bought and SMS-ready, but voice "
                    f"registration with xAI didn't complete: {voice_error}"
                )
        except ProvisioningError as e:
            error = str(e)

    redirect_url = f"/clients/{client_id}"
    if error:
        from urllib.parse import quote

        redirect_url += f"?provision_error={quote(error)}"
    return RedirectResponse(redirect_url, status_code=303)


@app.post("/clients/{client_id}/retry-xai-registration")
def retry_xai_registration(client_id: int):
    """Retry only the xAI voice half for a number already bought via Twilio
    (provision-number's Twilio half succeeded, its xAI half didn't). Does not
    purchase a new number -- that route always buys fresh, which would waste
    money re-buying on every retry while debugging the xAI side."""
    error = None
    with Session(engine) as session:
        client = session.get(Business, client_id)
        if not client.inbound_number or not client.twilio_number_sid:
            raise HTTPException(
                status_code=400, detail="No purchased number to retry xAI registration for"
            )
        # Idempotent: if registration already succeeded and only the trunk work
        # failed, this resumes from there instead of re-registering (which would
        # 409) — the whole reason the secret is now persisted separately.
        error = provision_voice(session, client)

    redirect_url = f"/clients/{client_id}"
    if error:
        from urllib.parse import quote

        redirect_url += f"?provision_error={quote(error)}"
    return RedirectResponse(redirect_url, status_code=303)


@app.get("/clients/{client_id}/voice-preflight")
def voice_preflight(client_id: int):
    """Read-only check of every link in the voice chain, so a broken one is
    found before someone dials instead of by hearing silence. Changes nothing.

    `ready: true` means the wiring is complete — NOT that voice is proven to
    work. Only a real inbound call exercises xAI's realtime session, the audio
    path, and the tool round-trip (see agent/README.md)."""
    with Session(engine) as session:
        client = session.get(Business, client_id)
        if client is None:
            raise HTTPException(status_code=404, detail="No such client")
        result = verify_voice_wiring(client)
    return {
        "business": client.business_name,
        "number": client.xai_phone_number or client.inbound_number,
        "ready": result["ready"],
        "checks": {k: {"ok": ok, "detail": detail} for k, (ok, detail) in result["checks"].items()},
        "last_error": client.voice_provisioning_error,
    }


@app.post("/clients/{client_id}/attach-xai-number")
def attach_xai_number(
    client_id: int,
    xai_phone_number: str = Form(...),
    xai_signing_secret: str = Form(...),
):
    """Attach an xAI voice number to a shop. xAI provisions numbers in its
    console (not via API — see the SIP docs), so there's nothing to automate:
    the founder pastes the number and the per-number webhook signing secret
    shown in the console, and this wires them so an inbound call to that number
    routes to this business (`_find_client_by_xai_number`) and its webhook
    verifies (`xai_signing_secret`). This is the no-Twilio path to the first
    real call — point the number's webhook at /webhook/xai-incoming-call in the
    xAI console, attach it here, and dial."""
    number = normalize_phone(xai_phone_number)
    secret = xai_signing_secret.strip()
    with Session(engine) as session:
        client = session.get(Business, client_id)
        if client is None:
            raise HTTPException(status_code=404, detail="No such client")
        if number and secret:
            client.xai_phone_number = number
            client.xai_signing_secret = secret
            # Default the display/SMS line to this number too, but never
            # overwrite a real Twilio inbound number already set.
            if not client.inbound_number:
                client.inbound_number = number
            session.add(client)
            session.commit()
    return RedirectResponse(f"/clients/{client_id}", status_code=303)


@app.post("/clients/{client_id}/review-link")
def set_review_link(client_id: int, review_link: str = Form(...)):
    with Session(engine) as session:
        client = session.get(Business, client_id)
        client.review_link = review_link.strip() or None
        session.add(client)
        session.commit()
    return RedirectResponse(f"/clients/{client_id}", status_code=303)


@app.post("/clients/{client_id}/jobs/{job_id}/complete")
def complete_job(client_id: int, job_id: int, value: str = Form("")):
    """Marks a job done, optionally recording what it was worth.

    "Mark done" is the only moment the true value of a job is known, which is
    why the amount is captured here rather than at booking (a quote is not a
    sale). Left blank it stays None — an unpriced job reads as unknown, never
    as $0, so it can't drag down a total it was never counted in.

    The review-request text is no longer sent here — it moved to
    review_service.send_due_review_requests, run on a delay via
    recovery_tick.py (2026-07-29/30 Reviews plan: "wait an appropriate
    amount of time" before asking, which an instant synchronous send here
    could never do)."""
    with Session(engine) as session:
        client = session.get(Business, client_id)
        job = session.get(Job, job_id)
        if client is None or job is None or job.business_id != client_id:
            raise HTTPException(status_code=404, detail="No such job")
        already_completed = job.completed_at is not None
        job.completed_at = job.completed_at or datetime.utcnow()
        job.value_cents = parse_money_cents(value) or job.value_cents
        session.add(job)
        session.commit()
        if not already_completed:
            try:
                dispatch_job_completed(session, client, job)
            except Exception as e:
                print(f"Runner: dispatch_job_completed failed for job {job_id}: {e}")
            # After dispatch, not before: a deduped publish rolls the session
            # back, and the employees reacting to this completion matter more
            # than the record that it happened. Post-commit either way — the
            # completion above is already durable.
            try:
                bus.publish(
                    session,
                    DomainEvent(
                        type=JOB_COMPLETED,
                        business_id=job.business_id,
                        customer_id=job.customer_id,
                        payload={"job_id": job.id, "service_type": job.service_type},
                        dedup_key=f"job.completed:{job.id}",
                    ),
                )
            except Exception as e:
                print(
                    f"[events] failed to publish job.completed for job {job_id}: {e}",
                    file=sys.stderr,
                )
    return RedirectResponse(f"/clients/{client_id}", status_code=303)


# Roster's provisioning pipeline (blueprint §10a). Ordered for display only —
# the route takes an explicit target, so ops can move a business backward to
# correct a mis-click without a database edit.
PIPELINE_STAGES = ("lead", "discovery", "provisioning", "qa", "live", "managed")


@app.post("/clients/{client_id}/interests/{interest_id}/actioned")
def action_department_interest(client_id: int, interest_id: int):
    """Close an expansion request — ops has handled it, whether that meant
    deploying the department or declining.

    Handled is NOT deployed: this only closes the request. Deploying is the
    separate action above, and Phase 3 has a permanent test asserting these
    stay independent.

    The interest id comes from the URL, so it is verified to belong to the
    business in the path — business isolation is the security boundary
    everywhere in Roster (platform PRD §12).
    """
    with Session(engine) as session:
        interest = session.get(DepartmentInterest, interest_id)
        if interest is None or interest.business_id != client_id:
            raise HTTPException(status_code=404, detail="No such expansion request")
        mark_actioned(session, interest_id)
    return RedirectResponse(f"/clients/{client_id}", status_code=303)


@app.post("/clients/{client_id}/pipeline-stage")
def set_pipeline_stage(client_id: int, stage: str = Form(...)):
    """Move a business to an EXPLICIT pipeline stage.

    Deliberately not an "advance to next" action: that isn't idempotent, and a
    double-submit would silently skip a stage. Setting the stage a business is
    already on is a no-op success, so a stale tab re-posting never shows the
    founder an error.
    """
    error = None
    if stage not in PIPELINE_STAGES:
        error = f"Unknown pipeline stage: {stage}"
    else:
        with Session(engine) as session:
            client = session.get(Business, client_id)
            if client is None:
                raise HTTPException(status_code=404, detail="No such client")
            if client.pipeline_stage != stage:
                client.pipeline_stage = stage
                session.add(client)
                session.commit()

    redirect_url = f"/clients/{client_id}"
    if error:
        from urllib.parse import quote

        redirect_url += f"?stage_error={quote(error)}"
    return RedirectResponse(redirect_url, status_code=303)


@app.post("/clients/{client_id}/billing-state")
def set_billing_state(client_id: int, billing_state: str = Form(...)):
    """Move a business between trial (spend-capped) and paid (uncapped).

    The control this replaces did not exist. Every business carried a $20 cap
    that silently stopped five of the six live employees once crossed, and
    nothing in the app could lift it — the only remedy was SQL against
    production. This is the exit.

    Explicit target rather than a toggle, for the same reason set_pipeline_stage
    is: a double-submit from a stale tab must be a no-op, not a flip back to
    trial that takes a paying customer's office offline.
    """
    error = None
    if billing_state not in (BILLING_TRIAL, BILLING_PAID):
        error = f"Unknown billing state: {billing_state}"
    else:
        with Session(engine) as session:
            client = session.get(Business, client_id)
            if client is None:
                raise HTTPException(status_code=404, detail="No such client")
            if client.billing_state != billing_state:
                client.billing_state = billing_state
                # Clear the one-time alert claim on the way back to trial, so a
                # business that trials again can still be alerted a second time.
                if billing_state == BILLING_TRIAL:
                    client.trial_cap_notified = False
                session.add(client)
                session.commit()

    redirect_url = f"/clients/{client_id}"
    if error:
        from urllib.parse import quote

        redirect_url += f"?billing_error={quote(error)}"
    return RedirectResponse(redirect_url, status_code=303)


@app.post("/clients/{client_id}/employees/deploy")
def deploy_employee(
    client_id: int,
    department_key: str = Form(""),
    role_key: str = Form(""),
):
    """Founder-admin action: the only place an employee gets deployed for a
    business (platform PRD §11a — Founder Admin configures, Customer Portal
    only reflects).

    Takes EITHER a `department_key` (the founder-facing unit, blueprint §1) or
    the older single `role_key`. Both go through deployment.py, so both are
    idempotent: a double-submit or a stale re-post creates only what's missing
    and reaches the same end state.

    A department with nothing deployable raises, and that surfaces as a message
    on the page via ?deploy_error= — the same mechanism provision-number
    already uses — never as a 500.
    """
    error = None
    with Session(engine) as session:
        try:
            if department_key:
                deploy_department(session, client_id, department_key)
            elif role_key:
                client = session.get(Business, client_id)
                requested = json.loads(client.requested_roster) if client.requested_roster else []
                if role_key not in requested:
                    requested.append(role_key)
                    client.requested_roster = json.dumps(requested)
                    session.add(client)
                    session.commit()
                # The Employee row is the deployment record, created NOW rather
                # than at the next boot's backfill (audit F1). requested_roster
                # is still written above until Phase 7 removes it; it is simply
                # no longer what decides whether an employee is running.
                deploy_role(session, client_id, role_key)
        except ValueError as e:
            error = str(e)

    redirect_url = f"/clients/{client_id}"
    if error:
        from urllib.parse import quote

        redirect_url += f"?deploy_error={quote(error)}"
    return RedirectResponse(redirect_url, status_code=303)


@app.post("/clients/{client_id}/delete")
def delete_client(client_id: int, confirm_name: str = Form(...)):
    """Founder-admin, permanent delete of a business and everything under it
    (customers, jobs, messages, recovery/referral history, employees, events).
    Requires typing the business's exact name to confirm -- irreversible, no
    undo, so a typo'd client_id can't silently wipe the wrong business.

    SQLite doesn't enforce foreign keys here (no PRAGMA foreign_keys=ON in
    db.py), so a bare Business delete wouldn't error -- it would just leave
    orphaned rows in every child table. Delete children first, in dependency
    order, so nothing orphans regardless."""
    with Session(engine) as session:
        client = session.get(Business, client_id)
        if client is None:
            raise HTTPException(status_code=404, detail="No such client")
        if confirm_name.strip() != client.business_name:
            raise HTTPException(status_code=400, detail="Business name confirmation did not match")

        recovery_job_ids = select(RecoveryJob.id).where(RecoveryJob.business_id == client_id)
        session.exec(
            delete(RecoveryMessageLog).where(
                RecoveryMessageLog.recovery_job_id.in_(recovery_job_ids)
            )
        )
        session.exec(delete(Event).where(Event.business_id == client_id))
        session.exec(delete(DepartmentInterest).where(DepartmentInterest.business_id == client_id))
        session.exec(delete(OwnerNotification).where(OwnerNotification.business_id == client_id))
        session.exec(delete(ReferralLead).where(ReferralLead.business_id == client_id))
        session.exec(delete(RecoveryJob).where(RecoveryJob.business_id == client_id))
        session.exec(delete(RecoveryCampaign).where(RecoveryCampaign.business_id == client_id))
        session.exec(delete(Job).where(Job.business_id == client_id))
        session.exec(delete(Message).where(Message.business_id == client_id))
        session.exec(delete(Employee).where(Employee.business_id == client_id))
        session.exec(delete(Customer).where(Customer.business_id == client_id))
        session.delete(client)
        session.commit()

    return RedirectResponse("/clients", status_code=303)


@app.get("/clients/{client_id}/recovery/new")
def new_recovery_campaign_form(request: Request, client_id: int):
    with Session(engine) as session:
        client = session.get(Business, client_id)
    preselect_face = request.query_params.get("face", "quote")
    return templates.TemplateResponse(
        request, "recovery_new.html", {"client": client, "preselect_face": preselect_face}
    )


@app.post("/clients/{client_id}/recovery/new")
def create_recovery_campaign(
    client_id: int,
    face: str = Form(...),
    name: str = Form(...),
    customers_raw: str = Form(...),
):
    """`customers_raw` is one customer per line:
    phone,name,service_type,amount_or_days_since_or_renewal_date
    (the 4th column's meaning depends on `face`: estimate_amount for quote,
    days_since for reactivation, renewal_date (YYYY-MM-DD) for membership)."""
    customers = []
    for i, line in enumerate(customers_raw.strip().splitlines(), start=1):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        # A pasted CRM export is the messiest phone source in the product —
        # "(770) 288-1238", "770.288.1238" and "7702881238" all arrive in the
        # same column. Normalize once here so the whole recovery sequence, and
        # the reply matching that keys off customer_phone, agree on one shape.
        entry = {"phone": normalize_phone(parts[0]), "name": parts[1], "service_type": parts[2]}
        if len(parts) > 3 and parts[3]:
            if face == "quote":
                entry["estimate_amount"] = parts[3]
            elif face == "membership":
                try:
                    datetime.strptime(parts[3], "%Y-%m-%d")
                except ValueError:
                    raise HTTPException(
                        400,
                        detail=f"Row {i}: '{parts[3]}' is not a valid renewal date (use YYYY-MM-DD)",
                    )
                entry["anchor_date"] = parts[3]
            else:
                entry["days_since"] = parts[3]
        customers.append(entry)

    with Session(engine) as session:
        client = session.get(Business, client_id)
        campaign = create_campaign(session, client, face, name, customers)
        campaign_id = campaign.id
    return RedirectResponse(f"/clients/{client_id}/recovery/{campaign_id}", status_code=303)


@app.get("/clients/{client_id}/recovery/{campaign_id}")
def recovery_campaign_detail(request: Request, client_id: int, campaign_id: int):
    with Session(engine) as session:
        client = session.get(Business, client_id)
        campaign = session.get(RecoveryCampaign, campaign_id)
        jobs = session.exec(
            select(RecoveryJob)
            .where(RecoveryJob.campaign_id == campaign_id)
            .order_by(RecoveryJob.id)
        ).all()
    return templates.TemplateResponse(
        request,
        "recovery_detail.html",
        {
            "client": client,
            "campaign": campaign,
            "jobs": jobs,
            "face_display_name": FACE_DISPLAY_NAMES[campaign.face],
        },
    )


@app.get("/clients/{client_id}")
def client_detail(request: Request, client_id: int):
    with Session(engine) as session:
        client = session.get(Business, client_id)
        if client is None:
            raise HTTPException(status_code=404, detail="No such client")
        messages = session.exec(
            select(Message)
            .where(Message.business_id == client_id, Message.customer_phone == DASHBOARD_THREAD)
            .order_by(Message.id)
        ).all()
        jobs = session.exec(
            select(Job).where(Job.business_id == client_id).order_by(Job.created_at.desc())
        ).all()
        campaigns = session.exec(
            select(RecoveryCampaign)
            .where(RecoveryCampaign.business_id == client_id)
            .order_by(RecoveryCampaign.created_at.desc())
        ).all()
        referral_leads = session.exec(
            select(ReferralLead)
            .where(ReferralLead.business_id == client_id)
            .order_by(ReferralLead.created_at.desc())
        ).all()
        department_rows = _founder_department_rows(
            session.exec(select(Employee).where(Employee.business_id == client_id)).all()
        )
        # Oldest first — a work queue, so ops handles what has waited longest.
        open_interests = [
            {"interest": i, "department": departments.get_department(i.department_key)}
            for i in open_interests_for(session, client_id)
        ]
        # The ONE definition of a real booked job, shared with the customer
        # dashboard — not a second count that can drift from theirs.
        checklist = _setup_checklist(
            client, department_rows, metrics.booked_jobs(session, client_id)
        )
        access_link = _access_link(client)

    chat = [
        {"role": m.role, "text": extract_display_text(json.loads(m.content_json))} for m in messages
    ]
    chat = [c for c in chat if c["text"]]

    campaigns_by_face = {"quote": [], "reactivation": [], "membership": []}
    for c in campaigns:
        campaigns_by_face.setdefault(c.face, []).append(c)

    return templates.TemplateResponse(
        request,
        "client_detail.html",
        {
            "client": client,
            "chat": chat,
            "jobs": jobs,
            "campaigns_by_face": campaigns_by_face,
            "face_display_names": FACE_DISPLAY_NAMES,
            "referral_leads": referral_leads,
            "department_rows": department_rows,
            "open_interests": open_interests,
            "pipeline_stages": PIPELINE_STAGES,
            "checklist": checklist,
            "access_link": access_link,
            "access_link_days": ACCESS_LINK_MAX_AGE_SECONDS // 86400,
            "provision_error": request.query_params.get("provision_error"),
            "deploy_error": request.query_params.get("deploy_error"),
            "access_error": request.query_params.get("access_error"),
            "stage_error": request.query_params.get("stage_error"),
            "billing_error": request.query_params.get("billing_error"),
        },
    )


@app.post("/clients/{client_id}/chat")
def send_message(client_id: int, message: str = Form(...)):
    with Session(engine) as session:
        client = session.get(Business, client_id)
        handle_customer_message(session, client, DASHBOARD_THREAD, message)
    return RedirectResponse(f"/clients/{client_id}", status_code=303)


def _find_client_by_inbound(session: Session, to_number: str) -> Business | None:
    return session.exec(select(Business).where(Business.inbound_number == to_number)).first()


def _twilio_signature_ok(request: Request, form) -> bool:
    """Verify a webhook really came from Twilio (X-Twilio-Signature = HMAC of
    the exact URL + params, keyed by the account Auth Token). Unenforced only
    in dev (ROSTER_ENV != production), where there's no real Twilio traffic to
    forge, so the endpoints stay open with no token configured. In production,
    a missing token or missing signature header fails closed instead of
    silently accepting unsigned requests (2026-08-10 pentest, VULN-0001). The
    URL is rebuilt from PUBLIC_BASE_URL (the host Twilio was configured to
    call) rather than request.url, which behind Railway's TLS proxy is the
    internal http host and would never match Twilio's signature."""
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    if not token:
        return os.environ.get("ROSTER_ENV") != "production"
    signature = request.headers.get("X-Twilio-Signature")
    if not signature:
        return False
    from twilio.request_validator import RequestValidator

    base = public_base_url().rstrip("/")
    url = f"{base}{request.url.path}"
    return RequestValidator(token).validate(url, dict(form), signature)


def _find_client_by_xai_number(session: Session, to_number: str) -> Business | None:
    return session.exec(select(Business).where(Business.xai_phone_number == to_number)).first()


# Svix-convention webhooks include a unix-seconds timestamp; anything outside
# this window is treated as a replay of a captured delivery and rejected.
WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS = 300


def _webhook_timestamp_fresh(timestamp_header: str | None) -> bool:
    if not timestamp_header:
        return False
    try:
        ts = int(timestamp_header)
    except ValueError:
        return False
    import time as _time

    return abs(_time.time() - ts) <= WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS


@app.post("/webhook/sms")
async def inbound_sms(request: Request):
    """Twilio inbound SMS. Routes by the business line texted (To) and replies via
    TwiML — so the AI's response is sent with no outbound credentials required.
    An active Revenue Recovery conversation for this customer takes priority over
    Frontdesk, since Recovery started this thread; once it resolves (booked,
    declined, or no_response) future texts fall through to Frontdesk as before.
    An active referral ask (sent within the last few days, not yet replied to)
    is checked next — a one-shot capture with nothing time-sensitive about it,
    so it comes after Recovery's active negotiation but still ahead of Frontdesk.

    Twilio delivery is at-least-once: each MessageSid is claimed in
    WebhookDelivery, and a retry REPLAYS the cached TwiML instead of running a
    second agent turn — so the customer gets the original answer, never a
    duplicate reply or a duplicate booking.

    Authenticity: when TWILIO_AUTH_TOKEN is set, a valid X-Twilio-Signature is
    required (see _twilio_signature_ok) so a spoofed request can't drive the AI
    or trigger outbound SMS."""
    form = await request.form()
    if not _twilio_signature_ok(request, form):
        return Response(status_code=403)
    From = form.get("From", "")
    To = form.get("To", "")
    Body = form.get("Body", "")
    MessageSid = form.get("MessageSid", "")

    dedup_key = f"twilio-sms:{MessageSid}" if MessageSid else None
    if dedup_key:
        with Session(engine) as session:
            seen = session.exec(
                select(WebhookDelivery).where(WebhookDelivery.dedup_key == dedup_key)
            ).first()
            if seen is not None and seen.response_text is not None:
                return Response(content=seen.response_text, media_type="application/xml")
            if seen is None:
                session.add(WebhookDelivery(provider="twilio-sms", dedup_key=dedup_key))
                try:
                    session.commit()
                except IntegrityError:
                    # Another worker holds this delivery right now; stay silent —
                    # Twilio's retry will replay the cached reply once it's stored.
                    session.rollback()
                    return twiml_empty()
            # seen-with-no-response falls through: a previous attempt died
            # mid-turn, and reprocessing is safe (message insert is deduped by
            # external_id, bookings by book_job's upsert).

    reply = await run_in_threadpool(_process_inbound_sms, From, To, Body, MessageSid or None)
    response = twiml_empty() if reply is None else twiml_reply(reply)

    if dedup_key:
        with Session(engine) as session:
            row = session.exec(
                select(WebhookDelivery).where(WebhookDelivery.dedup_key == dedup_key)
            ).first()
            if row is not None:
                row.response_text = bytes(response.body).decode()
                session.add(row)
                session.commit()
    return response


def _process_inbound_sms(
    from_number: str, to_number: str, body: str, message_sid: str | None = None
) -> str | None:
    """The actual (blocking) work for an inbound SMS turn — runs in a worker
    thread so one slow Claude call doesn't stall every other concurrent call/text
    this process is handling (see run_in_threadpool call above)."""
    with Session(engine) as session:
        client = _find_client_by_inbound(session, to_number)
        if client is None:
            return "Sorry, this number isn't set up to receive messages."
        # Serialize per conversation: a rapid double-text from one customer
        # must not run two interleaved agent turns (corrupts history ordering).
        with conversation_lock(client.id, from_number):
            recovery_job = find_active_recovery_job(session, client.id, from_number)
            if recovery_job is not None:
                return handle_recovery_reply(session, client, recovery_job, body)
            # Ordered by RECENCY of the last outbound touch, not by employee
            # seniority. A membership offer goes out on day 7, after the
            # referral ask (day 4) and the review ask (day 1) — but both of
            # those stay "active" for a 14-day reply window, so checking them
            # first would hand a reply about the membership offer to the review
            # classifier. Whoever texted the customer most recently is who they
            # are answering.
            membership_offer = find_active_membership_offer(session, client.id, from_number)
            if membership_offer is not None:
                return handle_membership_reply(session, client, membership_offer, body)
            referral_job = find_active_referral_ask(session, client.id, from_number)
            if referral_job is not None:
                return handle_referral_reply(session, client, referral_job, body)
            review_job = find_active_review_ask(session, client.id, from_number)
            if review_job is not None:
                return handle_review_reply(session, client, review_job, body)
            result = handle_customer_message(
                session, client, from_number, body, external_id=message_sid
            )
            return result["reply"]


@app.post("/webhook/voice-status")
async def missed_call(request: Request):
    """Twilio voice status callback. When a call goes unanswered, Roster texts the
    caller first — the missed-call text-back. Needs outbound credentials (or prints
    to console in dev). Signature-verified like /webhook/sms — this endpoint
    triggers an outbound SMS, so an unverified caller must never reach it.

    Twilio delivery is at-least-once here too, but unlike /webhook/sms there's
    no TwiML reply to replay — CallSid is claimed in WebhookDelivery purely to
    make the send-and-insert a one-time side effect (2026-08-10 pentest,
    VULN-0002)."""
    form = await request.form()
    if not _twilio_signature_ok(request, form):
        return Response(status_code=403)
    From = form.get("From", "")
    To = form.get("To", "")
    CallSid = form.get("CallSid", "")
    CallStatus = form.get("CallStatus", "")
    if CallStatus not in ("no-answer", "busy", "failed"):
        return Response(status_code=204)
    if not CallSid:
        return Response(status_code=400)
    with Session(engine) as session:
        client = _find_client_by_inbound(session, To)
        if client is None:
            return Response(status_code=204)
        session.add(
            WebhookDelivery(
                provider="twilio-voice-status", dedup_key=f"twilio-voice-status:{CallSid}"
            )
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return Response(status_code=204)
        opener = (
            f"Hi! Sorry we missed your call to {client.business_name}. "
            "I can help right here over text — what do you need a hand with?"
        )
        sms_channel.send(from_number=To, to_number=From, body=opener)
        session.add(
            Message(
                business_id=client.id,
                customer_phone=From,
                role="assistant",
                content_json=json.dumps([{"type": "text", "text": opener}]),
            )
        )
        session.commit()
    return Response(status_code=204)


@app.post("/webhook/xai-incoming-call")
async def xai_incoming_call(request: Request):
    """xAI's `realtime.call.incoming` webhook, fired when a call lands on a
    number registered with the Grok Voice Agent API (see xai_voice_adapter.py
    and agent/README.md for the Twilio-SIP-trunk setup this depends on).

    The signing secret is per-number (returned when that number was
    registered with xAI — see provisioning.py), so we look up the client by
    the dialed number *before* we can verify, then verify using that
    client's own stored secret. An unknown number can't be verified at all
    (no secret to check against) and is dropped either way.
    """
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body)
    except ValueError:
        payload = {}
    parsed = parse_xai_incoming_call(payload)

    # Capture the raw headers + body for EVERY inbound webhook — including
    # malformed, unknown, or unsigned ones — to a single fixed quarantine file.
    # That raw capture is what turns the first real call into a full protocol
    # confirmation instead of a blind debug session. The filename is a constant
    # (never the caller-supplied call_id), so an unauthenticated request can't
    # steer this pre-auth write's path (CWE-22/CWE-73); the per-call
    # {call_id}.jsonl log is only opened once the signature verifies below.
    CallTrace.capture_unverified(VOICE_CAPTURE_DIR, dict(request.headers), raw_body)

    # Create the trace at receipt so every latency offset is measured from
    # "call received". It records to memory + stderr only (no capture_dir) until
    # the webhook is authenticated — enable_capture() turns on disk persistence.
    trace = CallTrace(parsed["call_id"] if parsed else "unparsed")
    trace.webhook(dict(request.headers), raw_body)
    trace.stage("webhook_received")

    if parsed is None:
        trace.stage("dropped", reason="unparseable_or_wrong_type")
        return Response(status_code=204)

    with Session(engine) as session:
        client = _find_client_by_xai_number(session, parsed["to"])
        if client is None:
            trace.stage("dropped", reason="unknown_number", to=parsed["to"])
            return Response(status_code=204)

        # Fail closed: a voice-enabled number with no stored secret can't be
        # verified, so it must never trigger an unauthenticated live call.
        if not client.xai_signing_secret:
            trace.stage("dropped", reason="no_signing_secret")
            return Response(status_code=401)

        # Replay window: a correctly-signed webhook with an old timestamp is a
        # capture being replayed, not a live call — reject it.
        if not _webhook_timestamp_fresh(request.headers.get("webhook-timestamp")):
            trace.stage("dropped", reason="stale_timestamp")
            return Response(status_code=401)

        if not verify_xai_signature(
            request.headers.get("webhook-id"),
            request.headers.get("webhook-timestamp"),
            raw_body,
            request.headers.get("webhook-signature"),
            client.xai_signing_secret,
        ):
            trace.stage("dropped", reason="signature_failed")
            return Response(status_code=401)
        trace.stage("signature_verified")
        # Authenticated: now it's safe to persist the per-call ground-truth log.
        # call_id was constrained to a filename-safe token at parse time, and
        # enable_capture re-checks path containment before opening the file.
        trace.enable_capture(VOICE_CAPTURE_DIR)

    # Svix delivery is at-least-once: claim this call_id atomically so a
    # webhook retry (or a concurrent duplicate across workers) can never spawn
    # a second live session for the same call.
    with Session(engine) as session:
        session.add(WebhookDelivery(provider="xai-call", dedup_key=f"xai-call:{parsed['call_id']}"))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            trace.stage("dropped", reason="duplicate_call_id")
            return Response(status_code=200)

    # Must not block this webhook response on the call itself — the call
    # lives for minutes, xAI just wants a fast ack that we're handling it.
    task = asyncio.create_task(
        run_xai_call(
            parsed["call_id"], client, parsed["from"], lambda: Session(engine), trace=trace
        )
    )
    supervise_call_task(task, parsed["call_id"])
    return Response(status_code=200)


# Strong references to in-flight call tasks: a bare create_task result that
# nobody holds can be garbage-collected mid-call, killing a live phone
# conversation silently. The done-callback also surfaces any exception —
# an un-awaited task's exception is otherwise swallowed forever.
_active_call_tasks: set = set()


def supervise_call_task(task: "asyncio.Task", call_id: str) -> None:
    _active_call_tasks.add(task)

    def _done(t: "asyncio.Task") -> None:
        _active_call_tasks.discard(t)
        if t.cancelled():
            print(f"[voice] call {call_id} task was cancelled", file=sys.stderr)
            return
        exc = t.exception()
        if exc is not None:
            print(f"[voice] call {call_id} task crashed: {exc!r}", file=sys.stderr)

    task.add_done_callback(_done)
