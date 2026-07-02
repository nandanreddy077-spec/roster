import json
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from channels import get_channel
from db import engine, init_db
from db_models import Client, Job, Message, RecoveryCampaign, RecoveryJob
from recovery_service import create_campaign, find_active_recovery_job, handle_recovery_reply
from service import agent as shared_agent
from service import handle_customer_message
from voice_adapter import VapiChatRequest, handle_voice_turn

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


@app.get("/clients/{client_id}/recovery/new")
def new_recovery_campaign_form(request: Request, client_id: int):
    with Session(engine) as session:
        client = session.get(Client, client_id)
    return templates.TemplateResponse(request, "recovery_new.html", {"client": client})


@app.post("/clients/{client_id}/recovery/new")
def create_recovery_campaign(
    client_id: int,
    face: str = Form(...),
    name: str = Form(...),
    customers_raw: str = Form(...),
):
    """`customers_raw` is one customer per line: phone,name,service_type,amount_or_days_since"""
    customers = []
    for line in customers_raw.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        entry = {"phone": parts[0], "name": parts[1], "service_type": parts[2]}
        if len(parts) > 3 and parts[3]:
            if face == "quote":
                entry["estimate_amount"] = parts[3]
            else:
                entry["days_since"] = parts[3]
        customers.append(entry)

    with Session(engine) as session:
        client = session.get(Client, client_id)
        campaign = create_campaign(session, client, face, name, customers)
        campaign_id = campaign.id
    return RedirectResponse(f"/clients/{client_id}/recovery/{campaign_id}", status_code=303)


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


def _sse_chunk(request_id: str, model: str, delta: dict, finish_reason: str | None = None) -> str:
    chunk = {
        "id": request_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(chunk)}\n\n"


def _voice_stream(request_id: str, model: str, reply: str, pending_tool_call: dict | None):
    if pending_tool_call:
        if reply:
            yield _sse_chunk(request_id, model, {"role": "assistant", "content": reply})
        tool_call_id = f"call_{uuid.uuid4().hex[:24]}"
        yield _sse_chunk(
            request_id,
            model,
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": tool_call_id,
                        "type": "function",
                        "function": {
                            "name": pending_tool_call["name"],
                            "arguments": json.dumps(pending_tool_call["input"]),
                        },
                    }
                ],
            },
        )
        yield _sse_chunk(request_id, model, {}, finish_reason="tool_calls")
    else:
        yield _sse_chunk(request_id, model, {"role": "assistant", "content": reply})
        yield _sse_chunk(request_id, model, {}, finish_reason="stop")
    yield "data: [DONE]\n\n"


@app.post("/voice/chat/completions")
async def voice_chat_completions(payload: VapiChatRequest):
    request_id = f"chatcmpl-{payload.call.id}"
    called_number = payload.call.phoneNumber.number if payload.call.phoneNumber else None

    with Session(engine) as session:
        client = _find_client_by_inbound(session, called_number) if called_number else None
        if client is None:
            reply = "Sorry, this number isn't set up yet. Let me get someone on the line."
            return StreamingResponse(
                _voice_stream(request_id, payload.model, reply, None),
                media_type="text/event-stream",
            )

        try:
            result = handle_voice_turn(session, shared_agent, client, payload)
        except Exception:
            fallback = "Sorry, I'm having trouble right now — let me get you a person."
            pending = {"name": "transfer_call", "input": {"destination": client.escalation_phone}}
            return StreamingResponse(
                _voice_stream(request_id, payload.model, fallback, pending),
                media_type="text/event-stream",
            )

    return StreamingResponse(
        _voice_stream(request_id, payload.model, result["reply"], result["pending_tool_call"]),
        media_type="text/event-stream",
    )
