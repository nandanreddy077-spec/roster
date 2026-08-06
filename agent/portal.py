"""Self-serve customer portal: signup, login, onboarding, activation, and the
customer-facing dashboard. Fully separate from the founder's HTTP-Basic
/clients routes in app.py — session-cookie auth only (see SessionMiddleware
in app.py), one client per logged-in session.
"""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from authlib.integrations.starlette_client import OAuthError

from activation import activate_frontdesk
from auth import hash_password, read_access_token, verify_password
from channels import normalize_phone
from db import engine
from db_models import Business, Employee, Job, Message
from departments import department_status_for, get_department
from expansion import record_interest
from deployment import deploy_role
from google_auth import callback_url, get_oauth, google_enabled
from locks import conversation_lock
import metrics
from notifications import is_test_thread, recent_notifications
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
# Where a logged-in owner lands, and where every action redirects back to. One
# constant because the Phase 6 cutover found TWELVE hardcoded "/dashboard"
# redirects, all of which had to move together — the next move should be one
# line, not another twelve-site sweep.
DASHBOARD_HOME = "/v2/dashboard"
SETTINGS_HOME = "/v2/dashboard/settings"

NAV_ITEMS = (
    {"key": "overview", "label": "Overview", "href": DASHBOARD_HOME},
    {"key": "departments", "label": "Departments", "href": "/v2/dashboard/departments"},
    {"key": "briefing", "label": BRIEFING_LABEL, "href": "/v2/dashboard/briefing"},
    {"key": "notifications", "label": "Notifications", "href": "/v2/dashboard/notifications"},
    {"key": "settings", "label": "Settings", "href": SETTINGS_HOME},
)
templates.env.globals["nav_items"] = NAV_ITEMS

# CUSTOMER_STATE_LABELS, ACTIVE_STATES and METRIC_LABELS now live in
# workspace.py — the one home for the customer's wording, so there is exactly
# one copy rather than two maps that can drift (founder, 2026-07-29). Kept as
# module attributes here too, via the import below, so existing references
# (portal.METRIC_LABELS, etc.) and templates.env.globals need no other change.
from workspace import (  # noqa: E402
    ACTIVE_STATES as _ACTIVE_STATES,
    CUSTOMER_STATE_LABELS,
    METRIC_LABELS,
    NOTIFICATION_KIND_LABELS,
    build_briefing_workspace,
    build_department_workspace,
    build_employee_workspace,
    build_expansion_workspace,
    headline_outcome,
)

templates.env.globals["metric_labels"] = METRIC_LABELS
templates.env.globals["notification_kind_labels"] = NOTIFICATION_KIND_LABELS

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
        # The decision has to happen HERE now. It used to be delegated to
        # /dashboard, which bounced un-activated shops into onboarding; that
        # route is gone (Phase 6 cutover) and /v2/dashboard deliberately does
        # not bounce, so a plain password login would otherwise drop a shop
        # that never onboarded onto an empty dashboard and never ask its name.
        return _post_login_redirect(session, client)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/access/{token}")
def dashboard_access(request: Request, token: str):
    """A founder-issued dashboard link — the door for hand-provisioned
    customers.

    Roster provisions every business through the ops console, which never sets
    a password for the owner. Before this route existed, a shop the founder had
    fully set up — number bought, voice wired, departments staffed — had no way
    at all to see its own dashboard: /login needs a password_hash the founder
    can't create, and Google sign-in only helps once someone put an email on
    the row. This is the link the founder texts them.

    Straight to the dashboard, never onboarding: the founder already did the
    setup, and bouncing an owner into a wizard for work that's finished is
    exactly the confusion this replaces.
    """
    business_id = read_access_token(token, os.environ)
    if business_id is not None:
        with Session(engine) as session:
            # Re-check the business still exists: a link outlives a deletion,
            # and a session pointing at a dead id 500s on the next page.
            if session.get(Business, business_id) is not None:
                request.session["client_id"] = business_id
                return RedirectResponse(DASHBOARD_HOME, status_code=303)
    return templates.TemplateResponse(request, "access_expired.html", {}, status_code=400)


def _post_login_redirect(session: Session, client: Business) -> RedirectResponse:
    """The one rule for where an authenticated owner goes. Shared by password
    login and Google so the two doors can't drift apart.

    Deployment state decides, not `frontdesk_live` alone: that column is set by
    the retired self-serve activation flow, so a business the FOUNDER
    provisioned has real Employee rows and a live phone number while
    `frontdesk_live` is still False — and would have been bounced into an
    onboarding wizard it already finished. Same rule as ARCHITECTURE.md
    invariant 8: state derives from Employee rows, never from a status column.
    """
    staffed = session.exec(
        select(Employee).where(Employee.business_id == client.id, Employee.status != "fired")
    ).first()
    if client.frontdesk_live or staffed is not None:
        return RedirectResponse(DASHBOARD_HOME, status_code=303)
    return RedirectResponse("/onboarding/business", status_code=303)


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
    return _post_login_redirect(session, client)


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
            return RedirectResponse(DASHBOARD_HOME, status_code=303)
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
            return RedirectResponse(DASHBOARD_HOME, status_code=303)
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
        # The owner types this one by hand on their phone, so it arrives in
        # every shape a human writes a number in. It is also the only way we
        # reach them when a live call escalates — a wrong shape here is a
        # missed emergency, not a formatting nit.
        client.escalation_phone = normalize_phone(escalation_phone)
        # "primary" = AI answers every call; "backup" = AI catches only missed
        # calls. Anything unexpected falls back to the safe backup mode.
        client.answer_mode = answer_mode if answer_mode in ("primary", "backup") else "backup"
        client.business_phone = normalize_phone(business_phone)
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


def _gateway_card(session, business_id: int, status) -> dict:
    """A GATEWAY, not a report (founder, 2026-07-29): the department's
    question and at most one headline number, with the whole card navigating
    into the Department Workspace — which is where the full outcomes list and
    the employee roster live now. Used by both Overview and the Departments
    grid so the two pages can never show a staffed department differently."""
    outcomes = _outcomes(session, business_id, status.department.key, status.staffed)
    return {
        "department": status.department,
        "health_label": CUSTOMER_STATE_LABELS[status.state],
        "headline": headline_outcome(outcomes),
    }


@router.get("/v2/dashboard")
def v2_overview(request: Request):
    """Requires a session but NOT frontdesk_live: the old dashboard bounced
    un-activated businesses into onboarding, and after Phase 6 there is no
    onboarding wizard to bounce them to. They see honest empty states.

    Navigation, not reporting: every card here is a gateway into its
    Department Workspace, never the full outcomes list."""
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
                "gateway_cards": [_gateway_card(session, business.id, s) for s in working],
            },
        )


@router.get("/v2/dashboard/departments")
def v2_departments(request: Request):
    """The whole org, every time. Active departments render as gateway cards
    into their Department Workspace; inactive ones educate rather than just
    reporting absence (blueprint §7). Leadership is excluded: it isn't
    hireable and comes with every workforce, so it lives in the nav as the
    executive view.

    An inactive card links to Expansion only when `state == "empty"` —
    Operations/Finance/Marketing today are `unavailable` (zero deployable
    employees: employees.py's own registry says `planned` means "no engine,
    no route, no UI"), and build_expansion_workspace correctly 404s for them.
    Linking every inactive card unconditionally would have been a dead link
    for exactly those three — caught by a route test, not left to a browser.
    """
    with Session(engine) as session:
        business = _current_client(request, session)
        if business is None:
            return RedirectResponse("/login", status_code=303)
        cards = []
        for status in _statuses(session, business.id):
            if not status.department.hireable:
                continue
            if status.state in _ACTIVE_STATES:
                cards.append({"active": True, **_gateway_card(session, business.id, status)})
            else:
                cards.append({
                    "active": False,
                    "department": status.department,
                    "label": CUSTOMER_STATE_LABELS[status.state],
                    "can_expand": status.state == "empty",
                })
        return templates.TemplateResponse(
            request,
            "dashboard_v2/departments.html",
            {"business": business, "active_nav": "departments", "cards": cards},
        )


@router.get("/v2/dashboard/departments/{department_key}")
def v2_department_workspace(request: Request, department_key: str):
    """Open one department. Renders a SINGLE assembled view model
    (workspace.build_department_workspace) — this route never joins
    DepartmentStatus, EMPLOYEE_RECORDS and METRIC_RECORDS itself.

    404s for an unknown department key or one this business hasn't deployed:
    you open your own office, not a directory of ones you might rent.
    """
    with Session(engine) as session:
        business = _current_client(request, session)
        if business is None:
            return RedirectResponse("/login", status_code=303)
        ws = build_department_workspace(session, business.id, department_key)
        if ws is None:
            raise HTTPException(status_code=404, detail="No such department")
        # Existence check only, same builder Task 9's own route calls — the
        # "Expand" link appears exactly when there's somewhere for it to go,
        # never a dead control.
        can_expand = build_expansion_workspace(session, business.id, department_key) is not None
        return templates.TemplateResponse(
            request,
            "dashboard_v2/department.html",
            {
                "business": business, "active_nav": "departments", "workspace": ws,
                "department_key": department_key, "can_expand": can_expand,
            },
        )


@router.get("/v2/dashboard/departments/{department_key}/employees/{role_key}")
def v2_employee_workspace(request: Request, department_key: str, role_key: str):
    """The drill-down leaf: Overview -> Department -> Employee -> Activity.
    Renders a SINGLE assembled EmployeeWorkspace — this route never touches
    EMPLOYEE_RECORDS or metrics.py directly."""
    with Session(engine) as session:
        business = _current_client(request, session)
        if business is None:
            return RedirectResponse("/login", status_code=303)
        ws = build_employee_workspace(session, business.id, department_key, role_key)
        if ws is None:
            raise HTTPException(status_code=404, detail="No such employee")
        department = get_department(department_key)
        return templates.TemplateResponse(
            request,
            "dashboard_v2/employee.html",
            {
                "business": business, "active_nav": "departments", "workspace": ws,
                "department_key": department_key,
                "department_display_name": department.display_name if department else department_key,
            },
        )


@router.get("/v2/dashboard/departments/{department_key}/expand")
def v2_expand_department(request: Request, department_key: str, requested: bool = False):
    """Expansion is not a separate destination — an action nested under the
    Department Workspace, or (for a department with nothing deployed yet)
    reached directly from its educational card on the Departments grid.
    Renders a SINGLE assembled ExpansionWorkspace; this route never inspects
    DepartmentInterest or deployment state itself."""
    with Session(engine) as session:
        business = _current_client(request, session)
        if business is None:
            return RedirectResponse("/login", status_code=303)
        ws = build_expansion_workspace(session, business.id, department_key)
        if ws is None:
            raise HTTPException(status_code=404, detail="Nothing to expand here")
        return templates.TemplateResponse(
            request,
            "dashboard_v2/expansion.html",
            {
                "business": business, "active_nav": "departments", "workspace": ws,
                "department_key": department_key, "requested": requested,
            },
        )


@router.post("/v2/dashboard/departments/{department_key}/expand")
def v2_request_expansion(request: Request, department_key: str):
    """Records interest via Phase 3's record_interest — unchanged, and
    already idempotent, so a double-submit records nothing twice. Deploys
    nothing: asking is not the same as being staffed (Phase 3's permanent
    guard).

    Gated on the SAME build_expansion_workspace check the GET route uses,
    before recording anything — so POST and GET agree on what's requestable,
    and a submit can never land on a 404 for the page it just redirects to.
    """
    with Session(engine) as session:
        business = _current_client(request, session)
        if business is None:
            return RedirectResponse("/login", status_code=303)
        if build_expansion_workspace(session, business.id, department_key) is None:
            raise HTTPException(status_code=404, detail="Nothing to expand here")
        try:
            record_interest(session, business.id, department_key)
        except ValueError:
            raise HTTPException(status_code=400, detail="Not a department you can request")
    return RedirectResponse(
        f"/v2/dashboard/departments/{department_key}/expand?requested=true", status_code=303
    )


@router.get("/v2/dashboard/briefing")
def v2_briefing(request: Request):
    """Renders a SINGLE assembled BriefingWorkspace — a synthesis over the
    Department/Expansion Workspaces already built, never a second independent
    report. GET-only: viewing a growth nudge must never itself record
    interest (Phase 3's guard, upheld here the same way it is on the /expand
    GET route)."""
    with Session(engine) as session:
        business = _current_client(request, session)
        if business is None:
            return RedirectResponse("/login", status_code=303)
        ws = build_briefing_workspace(session, business.id)
        return templates.TemplateResponse(
            request,
            "dashboard_v2/briefing.html",
            {"business": business, "active_nav": "briefing", "workspace": ws},
        )


@router.get("/v2/dashboard/notifications")
def v2_notifications(request: Request):
    """A plain list over Phase 2's recent_notifications() — no view model,
    since there's no synthesis to do over a list that's already exactly what
    this page shows."""
    with Session(engine) as session:
        business = _current_client(request, session)
        if business is None:
            return RedirectResponse("/login", status_code=303)
        notifications = recent_notifications(session, business.id, limit=50)
        return templates.TemplateResponse(
            request,
            "dashboard_v2/notifications.html",
            {"business": business, "active_nav": "notifications", "notifications": notifications},
        )


@router.get("/v2/dashboard/settings")
def v2_settings(request: Request):
    """The only page in the dashboard an owner CHANGES something from —
    everything else reports, and Roster provisions.

    It holds exactly the two things the owner genuinely owns (their review
    link, their referral offer) plus read-only proof of what Roster set up for
    them. Anything else is a conversation with Roster, not a form: the owner
    can't repoint their own phone number or staff a department, and a field
    that silently does nothing is worse than no field.

    No view model, same reasoning as notifications above: these are columns on
    the business row, not a composition over departments and metrics.
    """
    with Session(engine) as session:
        business = _current_client(request, session)
        if business is None:
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(
            request,
            "dashboard_v2/settings.html",
            {"business": business, "active_nav": "settings", "saved": "saved" in request.query_params},
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
    return RedirectResponse(DASHBOARD_HOME, status_code=303)


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
    return RedirectResponse(DASHBOARD_HOME, status_code=303)


@router.post("/dashboard/review-link")
def set_review_link(request: Request, review_link: str = Form("")):
    with Session(engine) as session:
        client = _current_client(request, session)
        if client is None:
            return RedirectResponse("/login", status_code=303)
        client.review_link = review_link.strip() or None
        session.add(client)
        session.commit()
    return RedirectResponse(f"{SETTINGS_HOME}?saved=1", status_code=303)


@router.post("/dashboard/referral-incentive")
def set_referral_incentive(request: Request, referral_incentive: str = Form("")):
    with Session(engine) as session:
        client = _current_client(request, session)
        if client is None:
            return RedirectResponse("/login", status_code=303)
        client.referral_incentive = referral_incentive.strip() or None
        session.add(client)
        session.commit()
    return RedirectResponse(f"{SETTINGS_HOME}?saved=1", status_code=303)


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
    return RedirectResponse(DASHBOARD_HOME, status_code=303)


@router.get("/roster/hire/retention-manager")
def roster_hire_retention_manager_form(request: Request):
    with Session(engine) as session:
        client = _current_client(request, session)
        if client is None:
            return RedirectResponse("/login", status_code=303)
        requested = json.loads(client.requested_roster) if client.requested_roster else []
        if next_hire(requested) != "Retention Manager":
            return RedirectResponse(DASHBOARD_HOME, status_code=303)
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
            return RedirectResponse(DASHBOARD_HOME, status_code=303)
        if review_link.strip():
            client.review_link = review_link.strip()
        if referral_incentive.strip():
            client.referral_incentive = referral_incentive.strip()
        requested.append("Retention Manager")
        client.requested_roster = json.dumps(requested)
        session.add(client)
        _hire_employee(session, client.id, "Retention Manager")
        session.commit()
    return RedirectResponse(DASHBOARD_HOME, status_code=303)
