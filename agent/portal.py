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
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from authlib.integrations.starlette_client import OAuthError

from activation import activate_frontdesk
from auth import hash_password, verify_password
from db import engine
from db_models import Business, Employee, Job, Message
from departments import department_status_for
from deployment import deploy_role
from google_auth import callback_url, get_oauth, google_enabled
from locks import conversation_lock
import metrics
from notifications import is_test_thread
from roles import ROSTER_DESCRIPTIONS, coming_later_after, next_hire, receptionist_display_name, role_key_for
from service import handle_customer_message

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Cache-busting for portal.css: its filename never changes, so browsers that
# cached it before a deploy keep using the stale copy (harmless for most
# edits, but a class added/renamed in CSS silently stops applying — e.g. the
# Google button rendering as a bare unstyled link after the button was added
# in the same deploy). File mtime changes on every deploy, so a `?v=` query
# param built from it forces a fresh fetch without hand-bumping a version.
PORTAL_CSS_VERSION = str(int((BASE_DIR / "static" / "portal.css").stat().st_mtime))
templates.env.globals["portal_css_version"] = PORTAL_CSS_VERSION

# The executive-view page's VISIBLE name. Provisional by founder decision
# (blueprint §4a: "safe to build against, not safe to consider final"), so
# routes, modules and template filenames stay `briefing` permanently and only
# this constant changes when the name does. Registered as a Jinja global so
# every template reads it without any route having to pass it — and
# test_briefing_label.py fails if a template hardcodes the words instead.
BRIEFING_LABEL = "The Briefing"
templates.env.globals["briefing_label"] = BRIEFING_LABEL

# Navigation represents stable CUSTOMER concepts, never implementation
# structure (founder design principle, 2026-07-29). These name things an owner
# already thinks about — never "Employees", "Jobs", "Campaigns" or "Agents",
# which are how the work is built, not how they think about their business.
#
# Expansion is deliberately absent: a permanent "grow your workforce" tab would
# make the product read as a storefront. It appears contextually instead
# (blueprint §5) — an Overview recommendation, an inactive department card, a
# Briefing nudge.
#
# Every visible label lives here so renaming one — including the provisional
# Briefing name — never means editing templates.
NAV_ITEMS = (
    {"key": "overview", "label": "Overview", "href": "/v2/dashboard"},
    {"key": "departments", "label": "Departments", "href": "/v2/dashboard/departments"},
    {"key": "briefing", "label": BRIEFING_LABEL, "href": "/v2/dashboard/briefing"},
    {"key": "notifications", "label": "Notifications", "href": "/v2/dashboard/notifications"},
    {"key": "settings", "label": "Settings", "href": "/v2/dashboard/settings"},
)
templates.env.globals["nav_items"] = NAV_ITEMS

# The CUSTOMER's wording for each department state. The founder console keeps
# its own map over the same states (app.py) — departments.DepartmentStatus is
# presentation-neutral so neither audience's voice leaks into the other's.
#
# `partial` reads as Working on purpose: a half-staffed department IS doing
# work, and finishing it is Roster's operational problem, not the owner's
# worry. `unavailable` reads the same as `empty` — the customer doesn't need
# to know whether a department is unstaffed or unbuilt.
CUSTOMER_STATE_LABELS = {
    "staffed": "Working",
    "partial": "Working",
    "empty": "Not yet part of your workforce",
    "unavailable": "Not yet part of your workforce",
}
_ACTIVE_STATES = ("staffed", "partial")

# The CUSTOMER's wording for each metric. Centralized so renaming one never
# means editing a template — and every key in metrics.METRIC_RECORDS must
# appear here or it would render as a bare number.
METRIC_LABELS = {
    metrics.JOBS_BOOKED: "Jobs booked",
    metrics.CALLS_ANSWERED: "Calls answered",
    metrics.ESCALATIONS: "Sent to you personally",
    metrics.REVIEW_REQUESTS_SENT: "Review requests sent",
    metrics.QUOTES_CHASED: "Estimates followed up",
    metrics.QUOTES_RECOVERED: "Estimates won back",
    metrics.CUSTOMERS_REACHED: "Past customers contacted",
    metrics.CUSTOMERS_RETURNED: "Customers who came back",
    metrics.REFERRALS_RECEIVED: "Referrals received",
}
templates.env.globals["metric_labels"] = METRIC_LABELS

router = APIRouter()

# The owner's own dashboard test chat runs on its own conversation thread, kept
# separate from real customer threads and from the founder-admin "dashboard"
# thread so a test never mixes into real activity.
PORTAL_TEST_THREAD = "portal-test"


def _hire_employee(session: Session, business_id: int, role: str) -> None:
    """Routes through deployment.py so exactly ONE module creates Employee
    rows. (This whole self-serve hire route is deleted in Phase 7; delegating
    keeps it correct until then rather than leaving a second writer alive.)"""
    try:
        deploy_role(session, business_id, role_key_for(role))
    except ValueError:
        # A role the registry doesn't consider deployable. The customer-facing
        # hire path is retired in Phase 7; until then, don't 500 on it.
        pass


def _display_text(content) -> str:
    if isinstance(content, str):
        return content
    return " ".join(b["text"] for b in content if b.get("type") == "text").strip()


def _current_client(request: Request, session: Session) -> Optional[Business]:
    client_id = request.session.get("client_id")
    if client_id is None:
        return None
    return session.get(Business, client_id)


@router.get("/signup")
def signup_form(request: Request):
    trade = request.query_params.get("trade", "")
    return templates.TemplateResponse(
        request, "signup.html", {"error": None, "email": "", "trade": trade, "google_enabled": google_enabled()}
    )


@router.post("/signup")
def signup_submit(request: Request, email: str = Form(...), password: str = Form(...), trade: str = Form("")):
    email = email.strip().lower()
    with Session(engine) as session:
        existing = session.exec(select(Business).where(Business.email == email)).first()
        if existing:
            return templates.TemplateResponse(
                request,
                "signup.html",
                {"error": "That email's already registered — try logging in instead.", "email": email, "trade": trade},
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
                {"error": "That email's already registered — try logging in instead.", "email": email, "trade": trade},
                status_code=400,
            )
        session.refresh(client)
        request.session["client_id"] = client.id
        if trade.strip():
            request.session["prefill_trade"] = trade.strip()
    return RedirectResponse("/onboarding/business", status_code=303)


@router.get("/login")
def login_form(request: Request):
    return templates.TemplateResponse(
        request, "login.html", {"error": None, "email": "", "google_enabled": google_enabled()}
    )


@router.post("/login")
def login_submit(request: Request, email: str = Form(...), password: str = Form(...)):
    email = email.strip().lower()
    with Session(engine) as session:
        client = session.exec(select(Business).where(Business.email == email)).first()
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


def _login_or_create_by_email(request: Request, session: Session, email: str) -> RedirectResponse:
    """Log a verified email in — creating a passwordless Business on first sight —
    and route by the same rules as password login: live shops to the dashboard,
    everyone else into (or back into) onboarding. Testable without any OAuth
    network round-trip; the Google callback just feeds it a verified email."""
    email = email.strip().lower()
    client = session.exec(select(Business).where(Business.email == email)).first()
    if client is None:
        client = Business(email=email)  # no password_hash — Google is their sign-in
        session.add(client)
        try:
            session.commit()
        except IntegrityError:
            # Lost a race with a concurrent signup/login for the same email —
            # that row now exists, so use it instead of failing this request.
            session.rollback()
            client = session.exec(select(Business).where(Business.email == email)).first()
        else:
            session.refresh(client)
    request.session["client_id"] = client.id
    if client.frontdesk_live:
        return RedirectResponse("/dashboard", status_code=303)
    return RedirectResponse("/onboarding/business", status_code=303)


@router.get("/auth/google/login")
async def google_login(request: Request):
    if not google_enabled():
        return RedirectResponse("/login", status_code=303)
    return await get_oauth().google.authorize_redirect(request, callback_url(request))


@router.get("/auth/google/callback", name="google_callback")
async def google_callback(request: Request):
    if not google_enabled():
        return RedirectResponse("/login", status_code=303)
    try:
        token = await get_oauth().google.authorize_access_token(request)
    except OAuthError:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Google sign-in didn't complete. Please try again.", "email": "", "google_enabled": True},
            status_code=400,
        )

    userinfo = token.get("userinfo") or {}
    email = userinfo.get("email")
    if not email or userinfo.get("email_verified") is False:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "We couldn't get a verified email from Google.", "email": "", "google_enabled": True},
            status_code=400,
        )

    with Session(engine) as session:
        return _login_or_create_by_email(request, session, email)


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
    pricing_faq: str = Form(...),
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
        client.pricing_faq = pricing_faq.strip()
        session.add(client)
        session.commit()
    return RedirectResponse("/onboarding/receptionist", status_code=303)


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
def onboarding_receptionist_submit(
    request: Request,
    escalation_phone: str = Form(...),
    answer_mode: str = Form("backup"),
    business_phone: str = Form(""),
):
    with Session(engine) as session:
        client = _current_client(request, session)
        if client is None:
            return RedirectResponse("/login", status_code=303)
        if not client.business_name:
            return RedirectResponse("/onboarding/business", status_code=303)
        client.escalation_phone = escalation_phone.strip()
        # "primary" = AI answers every call; "backup" = AI catches only missed
        # calls. Anything unexpected falls back to the safe backup mode.
        client.answer_mode = answer_mode if answer_mode in ("primary", "backup") else "backup"
        client.business_phone = business_phone.strip()
        session.add(client)
        session.commit()
        activate_frontdesk(session, client)
    return RedirectResponse("/activation/live", status_code=303)


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


@router.get("/dashboard")
def dashboard(request: Request):
    with Session(engine) as session:
        client = _current_client(request, session)
        if client is None:
            return RedirectResponse("/login", status_code=303)
        if not client.frontdesk_live:
            return RedirectResponse("/onboarding/business", status_code=303)

        # The owner's test conversation — the proof-of-working panel.
        test_messages = session.exec(
            select(Message)
            .where(Message.business_id == client.id, Message.customer_phone == PORTAL_TEST_THREAD)
            .order_by(Message.id)
        ).all()
        test_chat = [
            {"role": m.role, "text": _display_text(json.loads(m.content_json))}
            for m in test_messages
        ]
        test_chat = [c for c in test_chat if c["text"]]

        # Real activity: every job booked, newest first. Jobs captured during a
        # test are tagged so the log never passes a test off as real work.
        jobs = session.exec(
            select(Job).where(Job.business_id == client.id).order_by(Job.created_at.desc())
        ).all()
        job_rows = [
            {
                "service_type": j.service_type,
                "urgency": j.urgency,
                "customer_name": j.customer_name,
                "callback_number": j.callback_number,
                "created_at": j.created_at,
                "is_test": is_test_thread(j.customer_phone),
            }
            for j in jobs
        ]
        # One definition, shared with every other surface (metrics.py) — not a
        # second count that can drift from the founder console's.
        real_job_count = metrics.booked_jobs(session, client.id)

        requested = json.loads(client.requested_roster) if client.requested_roster else []
        next_role = next_hire(requested)
        coming_later = coming_later_after(next_role)

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
                "tested": client.tested_at is not None,
                "test_chat": test_chat,
                "job_rows": job_rows,
                "real_job_count": real_job_count,
                "next_role": next_role,
                "next_role_description": ROSTER_DESCRIPTIONS.get(next_role, ""),
                "coming_later": coming_later,
                "requested": requested,
                "show_source_banner": show_source_banner,
            },
        )


# ---- The department-first dashboard (Phase 5) ------------------------------
# Built at /v2/* alongside the live /dashboard, which stays untouched until
# Phase 6 repoints it — the same pattern the landing rebuild used with
# /preview. Deployment state comes from departments.department_status_for over
# Employee rows, never from requested_roster or a hardcoded badge (the
# blueprint's permanent state-derivation invariant).


def _statuses(session, business_id: int):
    """This business's real department states, from Employee rows via the ONE
    shared helper the founder console also uses."""
    employees = session.exec(
        select(Employee).where(Employee.business_id == business_id)
    ).all()
    return department_status_for(employees)


def _outcomes(session, business_id: int, department_key: str, staffed) -> list:
    """[(label, value)] for one department — presentation applied here, facts
    from metrics.py, derived from the DEPLOYED employees so a department can
    never report a number for an employee it hasn't deployed."""
    role_keys = [e.key for e in staffed]
    outcomes = metrics.department_outcomes(session, business_id, department_key, role_keys)
    return [(METRIC_LABELS[key], value) for key, value in outcomes.items()]


@router.get("/v2/dashboard")
def v2_overview(request: Request):
    """Requires a session but NOT frontdesk_live: the old dashboard bounced
    un-activated businesses into onboarding, and after Phase 6 there is no
    onboarding wizard to bounce them to. They see honest empty states."""
    with Session(engine) as session:
        business = _current_client(request, session)
        if business is None:
            return RedirectResponse("/login", status_code=303)
        working = [s for s in _statuses(session, business.id) if s.state in _ACTIVE_STATES]
        return templates.TemplateResponse(
            request,
            "dashboard_v2/overview.html",
            {
                "business": business,
                "active_nav": "overview",
                "working": [
                    {
                        "department": s.department,
                        "outcomes": _outcomes(session, business.id, s.department.key, s.staffed),
                    }
                    for s in working
                ],
            },
        )


@router.get("/v2/dashboard/departments")
def v2_departments(request: Request):
    """The whole org, every time — active departments with real numbers, and
    inactive ones that educate rather than just reporting absence
    (blueprint §7). Leadership is excluded: it isn't hireable and comes with
    every workforce, so it lives in the nav as the executive view."""
    with Session(engine) as session:
        business = _current_client(request, session)
        if business is None:
            return RedirectResponse("/login", status_code=303)
        cards = []
        for status in _statuses(session, business.id):
            if not status.department.hireable:
                continue
            active = status.state in _ACTIVE_STATES
            cards.append({
                "department": status.department,
                "active": active,
                "label": CUSTOMER_STATE_LABELS[status.state],
                "employees": status.staffed,
                "outcomes": _outcomes(session, business.id, status.department.key, status.staffed) if active else [],
            })
        return templates.TemplateResponse(
            request,
            "dashboard_v2/departments.html",
            {"business": business, "active_nav": "departments", "cards": cards},
        )


@router.post("/dashboard/test")
def dashboard_test(request: Request, message: str = Form(...)):
    with Session(engine) as session:
        client = _current_client(request, session)
        if client is None:
            return RedirectResponse("/login", status_code=303)
        if not client.frontdesk_live:
            return RedirectResponse("/onboarding/business", status_code=303)
        text = message.strip()
        if text:
            with conversation_lock(client.id, PORTAL_TEST_THREAD):
                result = handle_customer_message(session, client, PORTAL_TEST_THREAD, text)
            # A real reply back is what earns the honest "Working" status —
            # never form submission. Set once, on the first successful test.
            if result["reply"] is not None and client.tested_at is None:
                client.tested_at = datetime.utcnow()
                session.add(client)
                session.commit()
    return RedirectResponse("/dashboard", status_code=303)


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


@router.post("/dashboard/review-link")
def set_review_link(request: Request, review_link: str = Form("")):
    with Session(engine) as session:
        client = _current_client(request, session)
        if client is None:
            return RedirectResponse("/login", status_code=303)
        client.review_link = review_link.strip() or None
        session.add(client)
        session.commit()
    return RedirectResponse("/dashboard", status_code=303)


@router.post("/dashboard/referral-incentive")
def set_referral_incentive(request: Request, referral_incentive: str = Form("")):
    with Session(engine) as session:
        client = _current_client(request, session)
        if client is None:
            return RedirectResponse("/login", status_code=303)
        client.referral_incentive = referral_incentive.strip() or None
        session.add(client)
        session.commit()
    return RedirectResponse("/dashboard", status_code=303)


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
            _hire_employee(session, client.id, role)
            session.commit()
    return RedirectResponse("/dashboard", status_code=303)


@router.get("/roster/hire/retention-manager")
def roster_hire_retention_manager_form(request: Request):
    with Session(engine) as session:
        client = _current_client(request, session)
        if client is None:
            return RedirectResponse("/login", status_code=303)
        requested = json.loads(client.requested_roster) if client.requested_roster else []
        if next_hire(requested) != "Retention Manager":
            return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse(request, "roster_hire_retention_manager.html", {})


@router.post("/roster/hire/retention-manager")
def roster_hire_retention_manager_submit(
    request: Request, review_link: str = Form(""), referral_incentive: str = Form("")
):
    with Session(engine) as session:
        client = _current_client(request, session)
        if client is None:
            return RedirectResponse("/login", status_code=303)
        requested = json.loads(client.requested_roster) if client.requested_roster else []
        if next_hire(requested) != "Retention Manager":
            return RedirectResponse("/dashboard", status_code=303)
        if review_link.strip():
            client.review_link = review_link.strip()
        if referral_incentive.strip():
            client.referral_incentive = referral_incentive.strip()
        requested.append("Retention Manager")
        client.requested_roster = json.dumps(requested)
        session.add(client)
        _hire_employee(session, client.id, "Retention Manager")
        session.commit()
    return RedirectResponse("/dashboard", status_code=303)
