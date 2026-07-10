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
from sqlmodel import Session, func, select

from activation import activate_frontdesk
from auth import hash_password, verify_password
from db import engine
from db_models import Client, Job, Message
from roles import ROSTER_DESCRIPTIONS, coming_later_after, next_hire, receptionist_display_name

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
