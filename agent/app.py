import asyncio
import base64
import json
import os
import secrets
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, func, select
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from channels import get_channel
from db import engine, init_db
from db_models import Client, Job, Message, RecoveryCampaign, RecoveryJob, ReferralLead
from portal import router as portal_router
from provisioning import ProvisioningError, attach_number_to_xai_trunk, buy_twilio_number, register_number_with_xai
from recovery_engine import FACE_DISPLAY_NAMES
from recovery_service import create_campaign, find_active_recovery_job, handle_recovery_reply
from referral_service import find_active_referral_ask, handle_referral_reply
from service import handle_customer_message
from xai_voice_adapter import (
    parse_incoming_call_webhook as parse_xai_incoming_call,
    run_call as run_xai_call,
    verify_webhook_signature as verify_xai_signature,
)

BASE_DIR = Path(__file__).parent
LANDING_DIR = BASE_DIR / "landing"
DASHBOARD_THREAD = "dashboard"

app = FastAPI(title="Roster")
# Customer-portal session cookie — separate from the founder's HTTP-Basic
# admin auth above. SESSION_SECRET_KEY signs the cookie; a dev fallback keeps
# local runs working without extra setup (unlike ADMIN_PASSWORD, a leaked
# portal session cookie only exposes one customer's own dashboard, not every
# client's data, so this doesn't need to fail closed).
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET_KEY", "dev-only-insecure-secret-change-in-production"),
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
sms_channel = get_channel()
app.include_router(portal_router)

init_db()


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
        decoded = base64.b64decode(header[len("Basic "):]).decode()
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
    return Response(content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>', media_type="application/xml")


# ---- Public landing page ---------------------------------------------------
# The marketing site is served by this same app (Railway's root directory is
# agent/, so files outside it never reach the deployed image). index.html links
# its stylesheet as a relative "styles.css", which resolves to /styles.css when
# served at the root — hence the dedicated route beside it.


@app.get("/")
def root():
    return FileResponse(LANDING_DIR / "index.html", media_type="text/html")


@app.get("/styles.css")
def landing_styles():
    return FileResponse(LANDING_DIR / "styles.css", media_type="text/css")


@app.get("/clients")
def list_clients(request: Request):
    with Session(engine) as session:
        clients = session.exec(select(Client).order_by(Client.created_at.desc())).all()
        count_rows = session.exec(
            select(RecoveryCampaign.client_id, func.count(RecoveryCampaign.id)).group_by(
                RecoveryCampaign.client_id
            )
        ).all()
        recovery_counts = {client_id: count for client_id, count in count_rows}
    return templates.TemplateResponse(
        request, "clients.html", {"clients": clients, "recovery_counts": recovery_counts}
    )


@app.get("/clients/new")
def new_client_form(request: Request):
    return templates.TemplateResponse(request, "new_client.html", {})


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
):
    service_list = [s.strip() for s in services.split(",") if s.strip()]
    client = Client(
        business_name=business_name,
        trade=trade,
        services_json=json.dumps(service_list),
        hours=hours,
        pricing_faq=pricing_faq,
        escalation_phone=escalation_phone,
        answer_mode=answer_mode,
        inbound_number=inbound_number.strip() or None,
    )
    with Session(engine) as session:
        session.add(client)
        session.commit()
        session.refresh(client)
    return RedirectResponse(f"/clients/{client.id}", status_code=303)


@app.post("/clients/{client_id}/referral-incentive")
def set_referral_incentive(client_id: int, referral_incentive: str = Form(...)):
    with Session(engine) as session:
        client = session.get(Client, client_id)
        client.referral_incentive = referral_incentive.strip() or None
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
        client = session.get(Client, client_id)
        try:
            purchase = buy_twilio_number(area_code.strip() or None)
            client.inbound_number = purchase["phone_number"]
            client.twilio_number_sid = purchase["sid"]
            session.add(client)
            session.commit()

            # Voice is best-effort: register with xAI first, and only route the
            # number's voice to xAI once that succeeds (see activation.py).
            try:
                xai_registration = register_number_with_xai(purchase["phone_number"])
                attach_number_to_xai_trunk(purchase["sid"], purchase["phone_number"])
                client.xai_phone_number = purchase["phone_number"]
                client.xai_signing_secret = xai_registration["signing_secret"]
                session.add(client)
                session.commit()
            except ProvisioningError as e:
                error = (
                    f"Number {purchase['phone_number']} bought and SMS-ready, but voice "
                    f"registration with xAI didn't complete: {e}"
                )
        except ProvisioningError as e:
            error = str(e)

    redirect_url = f"/clients/{client_id}"
    if error:
        from urllib.parse import quote

        redirect_url += f"?provision_error={quote(error)}"
    return RedirectResponse(redirect_url, status_code=303)


@app.post("/clients/{client_id}/review-link")
def set_review_link(client_id: int, review_link: str = Form(...)):
    with Session(engine) as session:
        client = session.get(Client, client_id)
        client.review_link = review_link.strip() or None
        session.add(client)
        session.commit()
    return RedirectResponse(f"/clients/{client_id}", status_code=303)


@app.post("/clients/{client_id}/jobs/{job_id}/complete")
def complete_job(client_id: int, job_id: int):
    """Marks a job done and, best-effort, texts a review-request link. The SMS
    send never blocks completion — a failed or skipped send still marks the job
    done, since the review text is a bonus, not the point of this action."""
    with Session(engine) as session:
        client = session.get(Client, client_id)
        job = session.get(Job, job_id)
        already_completed = job.completed_at is not None
        job.completed_at = job.completed_at or datetime.utcnow()
        session.add(job)
        session.commit()
        if not already_completed and client.review_link and job.callback_number:
            try:
                sms_channel.send(
                    from_number=client.inbound_number or "",
                    to_number=job.callback_number,
                    body=f"Thanks for choosing {client.business_name}! If we did right by you, a quick review means a lot: {client.review_link}",
                )
            except Exception as e:
                print(f"Reviews: failed to send review request for job {job_id}: {e}")
    return RedirectResponse(f"/clients/{client_id}", status_code=303)


@app.get("/clients/{client_id}/recovery/new")
def new_recovery_campaign_form(request: Request, client_id: int):
    with Session(engine) as session:
        client = session.get(Client, client_id)
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
        entry = {"phone": parts[0], "name": parts[1], "service_type": parts[2]}
        if len(parts) > 3 and parts[3]:
            if face == "quote":
                entry["estimate_amount"] = parts[3]
            elif face == "membership":
                try:
                    datetime.strptime(parts[3], "%Y-%m-%d")
                except ValueError:
                    raise HTTPException(
                        400, detail=f"Row {i}: '{parts[3]}' is not a valid renewal date (use YYYY-MM-DD)"
                    )
                entry["anchor_date"] = parts[3]
            else:
                entry["days_since"] = parts[3]
        customers.append(entry)

    with Session(engine) as session:
        client = session.get(Client, client_id)
        campaign = create_campaign(session, client, face, name, customers)
        campaign_id = campaign.id
    return RedirectResponse(f"/clients/{client_id}/recovery/{campaign_id}", status_code=303)


@app.get("/clients/{client_id}/recovery/{campaign_id}")
def recovery_campaign_detail(request: Request, client_id: int, campaign_id: int):
    with Session(engine) as session:
        client = session.get(Client, client_id)
        campaign = session.get(RecoveryCampaign, campaign_id)
        jobs = session.exec(
            select(RecoveryJob).where(RecoveryJob.campaign_id == campaign_id).order_by(RecoveryJob.id)
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
        client = session.get(Client, client_id)
        messages = session.exec(
            select(Message)
            .where(Message.client_id == client_id, Message.customer_phone == DASHBOARD_THREAD)
            .order_by(Message.id)
        ).all()
        jobs = session.exec(
            select(Job).where(Job.client_id == client_id).order_by(Job.created_at.desc())
        ).all()
        campaigns = session.exec(
            select(RecoveryCampaign).where(RecoveryCampaign.client_id == client_id).order_by(RecoveryCampaign.created_at.desc())
        ).all()
        referral_leads = session.exec(
            select(ReferralLead).where(ReferralLead.client_id == client_id).order_by(ReferralLead.created_at.desc())
        ).all()

    chat = [
        {"role": m.role, "text": extract_display_text(json.loads(m.content_json))}
        for m in messages
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
            "provision_error": request.query_params.get("provision_error"),
        },
    )


@app.post("/clients/{client_id}/chat")
def send_message(client_id: int, message: str = Form(...)):
    with Session(engine) as session:
        client = session.get(Client, client_id)
        handle_customer_message(session, client, DASHBOARD_THREAD, message)
    return RedirectResponse(f"/clients/{client_id}", status_code=303)


def _find_client_by_inbound(session: Session, to_number: str) -> Client | None:
    return session.exec(select(Client).where(Client.inbound_number == to_number)).first()


def _find_client_by_xai_number(session: Session, to_number: str) -> Client | None:
    return session.exec(select(Client).where(Client.xai_phone_number == to_number)).first()


@app.post("/webhook/sms")
async def inbound_sms(From: str = Form(...), To: str = Form(...), Body: str = Form(...)):
    """Twilio inbound SMS. Routes by the business line texted (To) and replies via
    TwiML — so the AI's response is sent with no outbound credentials required.
    An active Revenue Recovery conversation for this customer takes priority over
    Frontdesk, since Recovery started this thread; once it resolves (booked,
    declined, or no_response) future texts fall through to Frontdesk as before.
    An active referral ask (sent within the last few days, not yet replied to)
    is checked next — a one-shot capture with nothing time-sensitive about it,
    so it comes after Recovery's active negotiation but still ahead of Frontdesk."""
    reply = await run_in_threadpool(_process_inbound_sms, From, To, Body)
    if reply is None:
        return twiml_empty()
    return twiml_reply(reply)


def _process_inbound_sms(from_number: str, to_number: str, body: str) -> str | None:
    """The actual (blocking) work for an inbound SMS turn — runs in a worker
    thread so one slow Claude call doesn't stall every other concurrent call/text
    this process is handling (see run_in_threadpool call above)."""
    with Session(engine) as session:
        client = _find_client_by_inbound(session, to_number)
        if client is None:
            return "Sorry, this number isn't set up to receive messages."
        recovery_job = find_active_recovery_job(session, client.id, from_number)
        if recovery_job is not None:
            return handle_recovery_reply(session, client, recovery_job, body)
        referral_job = find_active_referral_ask(session, client.id, from_number)
        if referral_job is not None:
            return handle_referral_reply(session, client, referral_job, body)
        result = handle_customer_message(session, client, from_number, body)
        return result["reply"]


@app.post("/webhook/voice-status")
async def missed_call(From: str = Form(...), To: str = Form(...), CallStatus: str = Form(...)):
    """Twilio voice status callback. When a call goes unanswered, Roster texts the
    caller first — the missed-call text-back. Needs outbound credentials (or prints
    to console in dev)."""
    if CallStatus not in ("no-answer", "busy", "failed"):
        return Response(status_code=204)
    with Session(engine) as session:
        client = _find_client_by_inbound(session, To)
        if client is None:
            return Response(status_code=204)
        opener = (
            f"Hi! Sorry we missed your call to {client.business_name}. "
            "I can help right here over text — what do you need a hand with?"
        )
        sms_channel.send(from_number=To, to_number=From, body=opener)
        session.add(
            Message(
                client_id=client.id,
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
    parsed = parse_xai_incoming_call(json.loads(raw_body))
    if parsed is None:
        return Response(status_code=204)

    with Session(engine) as session:
        client = _find_client_by_xai_number(session, parsed["to"])
        if client is None:
            return Response(status_code=204)

        if client.xai_signing_secret:
            if not verify_xai_signature(
                request.headers.get("webhook-id"),
                request.headers.get("webhook-timestamp"),
                raw_body,
                request.headers.get("webhook-signature"),
                client.xai_signing_secret,
            ):
                return Response(status_code=401)

    # Must not block this webhook response on the call itself — the call
    # lives for minutes, xAI just wants a fast ack that we're handling it.
    asyncio.create_task(
        run_xai_call(parsed["call_id"], client, parsed["from"], lambda: Session(engine))
    )
    return Response(status_code=200)
