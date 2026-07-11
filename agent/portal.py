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

from authlib.integrations.starlette_client import OAuthError

from activation import activate_frontdesk
from auth import hash_password, verify_password
from db import engine
from db_models import Client, Job, Message
from google_auth import callback_url, get_oauth, google_enabled
from roles import ROSTER_DESCRIPTIONS, coming_later_after, next_hire, receptionist_display_name
from service import handle_customer_message

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter()

# The owner's own dashboard test chat runs on its own conversation thread, kept
# separate from real customer threads and from the founder-admin "dashboard"
# thread so a test never mixes into real activity.
PORTAL_TEST_THREAD = "portal-test"


def _display_text(content) -> str:
    if isinstance(content, str):
        return content
    return " ".join(b["text"] for b in content if b.get("type") == "text").strip()


def _current_client(request: Request, session: Session) -> Optional[Client]:
    client_id = request.session.get("client_id")
    if client_id is None:
        return None
    return session.get(Client, client_id)


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


@router.get("/login")
def login_form(request: Request):
    return templates.TemplateResponse(
        request, "login.html", {"error": None, "email": "", "google_enabled": google_enabled()}
    )


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


def _login_or_create_by_email(request: Request, session: Session, email: str) -> RedirectResponse:
    """Log a verified email in — creating a passwordless Client on first sight —
    and route by the same rules as password login: live shops to the dashboard,
    everyone else into (or back into) onboarding. Testable without any OAuth
    network round-trip; the Google callback just feeds it a verified email."""
    email = email.strip().lower()
    client = session.exec(select(Client).where(Client.email == email)).first()
    if client is None:
        client = Client(email=email)  # no password_hash — Google is their sign-in
        session.add(client)
        session.commit()
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
            .where(Message.client_id == client.id, Message.customer_phone == PORTAL_TEST_THREAD)
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
            select(Job).where(Job.client_id == client.id).order_by(Job.created_at.desc())
        ).all()
        job_rows = [
            {
                "service_type": j.service_type,
                "urgency": j.urgency,
                "customer_name": j.customer_name,
                "callback_number": j.callback_number,
                "created_at": j.created_at,
                "is_test": j.customer_phone == PORTAL_TEST_THREAD,
            }
            for j in jobs
        ]
        real_job_count = sum(1 for j in job_rows if not j["is_test"])

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
                "show_source_banner": show_source_banner,
            },
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
        session.commit()
    return RedirectResponse("/dashboard", status_code=303)
