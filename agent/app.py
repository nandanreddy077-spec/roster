import json
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from channels import get_channel
from db import engine, init_db
from db_models import Client, Job, Message
from service import handle_customer_message

BASE_DIR = Path(__file__).parent
DASHBOARD_THREAD = "dashboard"

app = FastAPI(title="Roster")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
sms_channel = get_channel()

init_db()


def extract_display_text(content) -> str:
    if isinstance(content, str):
        return content
    parts = [block["text"] for block in content if block.get("type") == "text"]
    return " ".join(parts).strip()


def twiml_reply(body: str) -> Response:
    safe = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    xml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{safe}</Message></Response>'
    return Response(content=xml, media_type="application/xml")


@app.get("/")
def root():
    return RedirectResponse("/clients")


@app.get("/clients")
def list_clients(request: Request):
    with Session(engine) as session:
        clients = session.exec(select(Client).order_by(Client.created_at.desc())).all()
    return templates.TemplateResponse(request, "clients.html", {"clients": clients})


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

    chat = [
        {"role": m.role, "text": extract_display_text(json.loads(m.content_json))}
        for m in messages
    ]
    chat = [c for c in chat if c["text"]]

    return templates.TemplateResponse(
        request,
        "client_detail.html",
        {"client": client, "chat": chat, "jobs": jobs},
    )


@app.post("/clients/{client_id}/chat")
def send_message(client_id: int, message: str = Form(...)):
    with Session(engine) as session:
        client = session.get(Client, client_id)
        handle_customer_message(session, client, DASHBOARD_THREAD, message)
    return RedirectResponse(f"/clients/{client_id}", status_code=303)


def _find_client_by_inbound(session: Session, to_number: str) -> Client | None:
    return session.exec(select(Client).where(Client.inbound_number == to_number)).first()


@app.post("/webhook/sms")
async def inbound_sms(From: str = Form(...), To: str = Form(...), Body: str = Form(...)):
    """Twilio inbound SMS. Routes by the business line texted (To) and replies via
    TwiML — so the AI's response is sent with no outbound credentials required."""
    with Session(engine) as session:
        client = _find_client_by_inbound(session, To)
        if client is None:
            return twiml_reply("Sorry, this number isn't set up to receive messages.")
        result = handle_customer_message(session, client, From, Body)
    return twiml_reply(result["reply"])


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
