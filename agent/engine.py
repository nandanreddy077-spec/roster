import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import anthropic

from models import ClientConfig

MODEL = "claude-sonnet-4-6"
MAX_ITERS = 4  # safety cap: bound the Think->Act->Observe loop so a turn can't run away

LOG_JOB_TOOL = {
    "name": "log_job",
    "description": (
        "Log a captured job/lead once enough details are known from the customer "
        "conversation. Call this as soon as you have a service type and either an "
        "address or a clear callback need, even if other fields are still missing. "
        "Safe to call again later in the same conversation if new details come in."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "customer_name": {"type": "string"},
            "service_type": {
                "type": "string",
                "description": "What the customer needs, e.g. 'AC not cooling', 'burst pipe'",
            },
            "urgency": {
                "type": "string",
                "enum": ["emergency", "same_day", "routine"],
            },
            "address": {"type": "string"},
            "callback_number": {"type": "string"},
            "notes": {"type": "string"},
            "preferred_window": {
                "type": "string",
                "description": (
                    "The day/time the customer says works best for them, in their own "
                    "words (e.g. 'Thursday afternoon', 'mornings only'). A stated "
                    "preference only — never a confirmed appointment, since nothing "
                    "actually checks technician availability yet."
                ),
            },
        },
        "required": ["service_type", "urgency"],
    },
}

# Honest capability: we cannot redirect a live call today, so the tool's name
# and description promise exactly what happens — an urgent SMS to the owner.
# (Python identifier kept for compatibility; the model only sees `name`.)
TRANSFER_CALL_TOOL = {
    "name": "alert_owner",
    "description": (
        "Immediately send the business owner an urgent text about this call — "
        "with the caller's number and your reason — so the owner can call them "
        "back right away. Use for true emergencies, upset callers, complaints, "
        "or anything you can't confidently handle. This does NOT redirect or "
        "connect the call; it alerts the owner by text."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "One short phrase for why the owner is being alerted.",
            },
        },
        "required": ["reason"],
    },
}


# Per-trade triage guidance (2026-07-29, conversation-quality audit, Sprint 1):
# one generic prompt for all 10 supported trades meant no trade-specific
# diagnostic questions and no trade-specific emergency examples — Electrical
# in particular had no hazard example at all, since the hardcoded emergencies
# (gas leak, flooding, no heat) are HVAC/plumbing-specific. Plain copy, same
# shape as roles.py's RECEPTIONIST_TRADE_NAMES: a dict keyed on the
# normalized trade string, no new architecture.
TRADE_TRIAGE_NOTES: Dict[str, str] = {
    "hvac": (
        "For this trade: ask whether the system runs at all and what the "
        "thermostat is set to. Treat no heat in freezing weather, no AC in "
        "dangerous heat, or any burning smell from the unit as an emergency."
    ),
    "plumbing": (
        "For this trade: ask whether water is actively leaking right now and "
        "whether they know where the shutoff valve is. Treat an actively "
        "leaking pipe, a sewage backup, or no water at all as an emergency."
    ),
    "electrical": (
        "For this trade: ask whether there's any sparking, a burning smell, "
        "or a breaker that keeps tripping, and whether the power is still on. "
        "Treat sparking, a burning smell, or exposed wiring as an emergency — "
        "these are fire risks."
    ),
    "roofing": (
        "For this trade: ask whether it's water actively coming inside right "
        "now, and whether it's one section or the whole roof. Treat active "
        "leaking during a storm, or visible structural sagging, as an emergency."
    ),
    "landscaping": (
        "For this trade: ask whether this is routine work or storm/damage "
        "related, and whether a fallen tree or branch is on a structure, "
        "vehicle, or power line. Treat anything on a structure, car, or power "
        "line as an emergency."
    ),
    "cleaning": (
        "For this trade: ask whether this is a one-time or recurring visit, "
        "and if one-time, whether it's a move-in/move-out clean with a "
        "deadline. This trade rarely has true emergencies."
    ),
    "garage door": (
        "For this trade: ask whether the door is stuck open or stuck closed, "
        "and whether it's the spring, cables, or the opener. A door stuck "
        "open is a security concern and more urgent than one stuck closed."
    ),
    "pest control": (
        "For this trade: ask what kind of pest and how severe, and whether "
        "anyone has been stung or has a known allergy. Treat a bee or wasp "
        "swarm, or a sting on someone with a known allergy, as an emergency."
    ),
    "painting": (
        "For this trade: ask whether it's interior or exterior work and "
        "roughly when they'd like it done. This trade rarely has true "
        "emergencies."
    ),
    "pool service": (
        "For this trade: ask whether the water is green or cloudy (a "
        "chemistry issue) versus equipment not working, or a safety issue "
        "like a broken fence or gate. Treat a broken pool fence or gate, "
        "especially with children nearby, as an emergency."
    ),
}
DEFAULT_TRIAGE_NOTE = (
    "Ask enough questions to understand what's actually needed and how "
    "urgent it is before booking."
)


def _trade_triage_note(trade: str) -> str:
    """Same normalization as roles.py's receptionist_display_name — trade
    is free text from onboarding, so casing isn't reliable. Falls back to a
    safe generic note for a trade outside the 10 supported ones, same
    DEFAULT-fallback discipline as DEFAULT_RECEPTIONIST_NAME there."""
    return TRADE_TRIAGE_NOTES.get((trade or "").strip().lower(), DEFAULT_TRIAGE_NOTE)


def _format_now(now: Optional[datetime]) -> str:
    """The current date/time, formatted plainly for a prompt (2026-07-29,
    conversation-quality audit) — neither prompt injected this before, so
    the model had no way to reason about "after hours" even though the
    "backup" answer_mode exists specifically for missed/after-hours calls.
    Server local time: Business has no stored timezone field, so this is
    the best available without adding one (out of this sprint's scope)."""
    return (now or datetime.now()).strftime("%A, %B %d, %I:%M %p")


# Shared across both prompts (2026-07-29, Sprint 1): recognizing a
# reschedule/cancel call, and asking for (never promising) a preferred
# window. Frontdesk has no scheduling/dispatch system — that's the
# not-yet-built Operations department — so both instructions are careful to
# stay honest about what hasn't actually happened yet.
_RESCHEDULE_NOTE = (
    "If the caller is asking to change or cancel an appointment they already "
    "have, don't try to book it as a new job — gather what they want changed "
    "(and log_job it with a note), and tell them honestly that someone will "
    "call to confirm the change."
)
_PREFERRED_WINDOW_NOTE = (
    "Before ending the conversation, ask what day or time window generally "
    "works best for them, and pass it as preferred_window when you call "
    "log_job. This is only their stated preference — never confirm it as a "
    "booked appointment time, since nothing checks real availability yet."
)

# Shared across both prompts (2026-07-29, Sprint 2): distinguishing a repair
# from a replacement/estimate call. No new tool or field — "store estimate
# intent using existing fields" means service_type/notes, exactly like every
# other detail log_job already captures as free text.
_ESTIMATE_NOTE = (
    "If the caller is asking about a replacement or a price estimate rather "
    "than an active problem, treat it as a different kind of call: ask about "
    "the age or size of what's being replaced and their rough timeline, "
    "rather than treating it like an urgent repair. Mention in log_job's "
    "notes that it's an estimate/replacement request, and set urgency to "
    "'routine' unless they also describe an active problem."
)


# Shared across both prompts (2026-07-29, Sprint 3 — sales & conversation
# excellence). Conversation types get folded into one paragraph with
# _RESCHEDULE_NOTE/_ESTIMATE_NOTE below (fewer separate one-line paragraphs,
# same content) rather than stacked as their own blocks — the conversation-
# flow cleanup this sprint also asked for.
_MULTI_ISSUE_NOTE = (
    'If the caller mentions more than one separate problem in the same '
    'call — e.g. "my AC isn\'t cooling and I also have a leaking water '
    'heater" — treat them as separate issues: ask enough about each one, '
    "and call log_job once per issue with its own service_type, rather "
    "than merging unrelated problems into a single booking."
)

# Objection handling and pricing conduct (Sprint 3) — grouped together since
# both are about how to talk about money, not two unrelated topics.
_OBJECTION_NOTE = (
    'Price pushback — "that\'s too expensive," "another company quoted '
    'less," "I\'ll think about it," "I\'m just shopping around" — gets '
    "acknowledged honestly and met with the value of the work (quality, "
    "reliability, warranty), never a made-up discount. If they want to "
    "actually negotiate, offer an owner callback to talk pricing rather "
    "than guessing."
)
_PRICING_GUIDANCE_NOTE = (
    "Use the pricing/FAQ info only for what it actually answers — don't "
    "recite it if it doesn't fit the question asked. A diagnostic fee, "
    "repair pricing, a price estimate, and replacement pricing are "
    "different things; keep them distinct rather than blurring them "
    "together, and if you genuinely don't know a price, say so honestly "
    "instead of guessing or reusing an unrelated number. If the pricing/FAQ "
    "info states a standard diagnostic or service-call fee, mention it "
    "naturally once before the call ends — never more than once, and never "
    "invented if it isn't actually stated there."
)


def _service_area_note(service_area: str) -> str:
    """Only asserts a restriction when the owner actually configured one —
    unset means no service-area line at all, never a broken empty one
    (2026-07-29, Sprint 2)."""
    service_area = (service_area or "").strip()
    if not service_area:
        return ""
    return (
        f"Service area: {service_area}. Ask for their city or ZIP code early "
        "in the conversation if it isn't already clear. If they're outside "
        "this area, say so honestly and don't book the job — don't guess or "
        "assume everyone is in range."
    )


def build_system_prompt(client: ClientConfig, now: Optional[datetime] = None) -> str:
    return f"""You are the AI front desk for {client.business_name}, a {client.trade} business. \
Right now it's {_format_now(now)}.

Your job: text back customers who called and couldn't reach anyone, answer their
questions, and capture enough detail to book the job.

Services offered: {", ".join(client.services)}
Hours: {client.hours}
Pricing & FAQ info: {client.pricing_faq}
Tone: {client.tone}
{_service_area_note(client.service_area)}

{_trade_triage_note(client.trade)}

If the situation is a true emergency (e.g. gas leak, flooding, no heat in freezing
weather), tell the customer you're alerting someone immediately and mark
urgency='emergency' when you call log_job.

A few call shapes need different handling: {_RESCHEDULE_NOTE} {_ESTIMATE_NOTE} \
{_MULTI_ISSUE_NOTE}

{_OBJECTION_NOTE} {_PRICING_GUIDANCE_NOTE}

Keep replies short, warm, and text-message length (1-3 sentences). {_PREFERRED_WINDOW_NOTE}
Once you have a service type and contact info, call log_job to capture the lead, then keep
texting naturally."""


def build_voice_system_prompt(client: ClientConfig, now: Optional[datetime] = None) -> str:
    if client.answer_mode == "primary":
        greeting_note = "You are the first point of contact — answer warmly like a normal receptionist."
    else:
        greeting_note = (
            "The caller just had their call go unanswered — open by acknowledging "
            "that before helping them."
        )

    return f"""You are the AI receptionist for {client.business_name}, a {client.trade} business, \
speaking live on the phone with a caller. Right now it's {_format_now(now)}.

{greeting_note}

Services offered: {", ".join(client.services)}
Hours: {client.hours}
Pricing & FAQ info: {client.pricing_faq}
Tone: {client.tone}
{_service_area_note(client.service_area)}

{_trade_triage_note(client.trade)}

Speak naturally, in short sentences suited for a live conversation — this is a phone \
call, not a text message.

If the situation is a true emergency (e.g. gas leak, flooding, no heat in freezing \
weather): if anyone may be in danger, first tell the caller to hang up and dial 911. \
Then call alert_owner with a short reason — that sends the owner an urgent text with \
the caller's number right away. Be honest about what's happening: you cannot connect \
or redirect this call. Say the owner has been texted and give the caller the owner's \
direct number, {client.escalation_phone}, so they can call right now. If alert_owner \
reports the text failed, say so plainly and give them {client.escalation_phone} to \
call themselves — never claim help is coming when it isn't. The same tool handles an \
upset caller, a complaint, anything you can't confidently handle, or a direct request \
to speak to a person — comply immediately in that last case rather than trying to \
keep helping first.

A few call shapes need different handling: {_RESCHEDULE_NOTE} {_ESTIMATE_NOTE} \
{_MULTI_ISSUE_NOTE}

{_OBJECTION_NOTE} {_PRICING_GUIDANCE_NOTE}

{_PREFERRED_WINDOW_NOTE} Once you have a service type and contact info, call log_job \
to capture the lead before ending the call."""


class AgentEngine:
    def __init__(self, api_key: Optional[str] = None, client: Optional[Any] = None):
        self.client = client or anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))

    def respond(
        self,
        client_config: ClientConfig,
        history: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
        max_iters: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Run one customer turn as a bounded Think -> Act -> Observe loop.

        `log_job` is resolved internally (the loop feeds an acknowledgment back and
        keeps going). Any other tool call (e.g. `transfer_call`) is a passthrough:
        only the caller (e.g. the live-voice provider) can actually execute it, so the loop stops
        immediately and returns it as `pending_tool_call` instead of resolving it.

        Returns:
          reply             final text to send/speak to the customer
          jobs              captured log_job inputs (caller persists them)
          new_messages      serialized assistant/tool_result turns generated this
                            turn, ready for the caller to store as history
          pending_tool_call {"name": str, "input": dict} if a non-log_job tool was
                            called, else None
        """
        system = system_prompt or build_system_prompt(client_config)
        active_tools = tools or [LOG_JOB_TOOL]
        iters = max_iters if max_iters is not None else MAX_ITERS
        messages = list(history)  # working copy; never mutate the caller's list
        new_messages: List[Dict[str, Any]] = []
        captured_jobs: List[Dict[str, Any]] = []
        reply_text = ""
        pending_tool_call: Optional[Dict[str, Any]] = None

        for _ in range(iters):
            resp = self.client.messages.create(
                model=MODEL,
                max_tokens=512,
                system=system,
                tools=active_tools,
                messages=messages,
            )

            assistant_content = serialize_content(resp.content)
            messages.append({"role": "assistant", "content": assistant_content})
            new_messages.append({"role": "assistant", "content": assistant_content})

            text_parts = [b.text for b in resp.content if b.type == "text"]
            all_tool_uses = [b for b in resp.content if b.type == "tool_use"]
            log_job_uses = [tu for tu in all_tool_uses if tu.name == "log_job"]
            passthrough_uses = [tu for tu in all_tool_uses if tu.name != "log_job"]

            if passthrough_uses:
                tu = passthrough_uses[0]
                pending_tool_call = {"name": tu.name, "input": tu.input}
                # Capture any log_job calls made in this same turn (e.g. an
                # emergency: the model calls both log_job and transfer_call
                # together) so the lead isn't silently dropped. There's no
                # further model turn to consume tool_results here, so we just
                # record the jobs and skip the acknowledgment round-trip.
                for log_tu in log_job_uses:
                    captured_jobs.append({"id": log_tu.id, "input": log_tu.input})
                if text_parts:
                    reply_text = " ".join(text_parts).strip()
                break

            if not log_job_uses:
                reply_text = " ".join(text_parts).strip()  # Done: clean final answer
                break

            # Act + Observe: capture each job and feed an acknowledgment back in.
            tool_results = []
            for tu in log_job_uses:
                captured_jobs.append({"id": tu.id, "input": tu.input})
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": "Logged. The job is captured for the team.",
                    }
                )
            tr_message = {"role": "user", "content": tool_results}
            messages.append(tr_message)
            new_messages.append(tr_message)
            if text_parts:  # keep any text said alongside the tool call as a fallback
                reply_text = " ".join(text_parts).strip()
        else:
            if not reply_text:  # hit the cap without a clean finish
                reply_text = "Thanks! I've got your details and someone will text you shortly."

        return {
            "reply": reply_text,
            "jobs": captured_jobs,
            "new_messages": new_messages,
            "pending_tool_call": pending_tool_call,
        }


def serialize_content(content) -> List[Dict[str, Any]]:
    return [block.model_dump() if hasattr(block, "model_dump") else block for block in content]


def merge_consecutive_roles(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse consecutive same-role messages into one, since Anthropic's API
    requires strict user/assistant alternation but a single logical turn (e.g. a
    tool_result followed later by the next customer text) may be stored as
    separate rows."""
    merged: List[Dict[str, Any]] = []
    for m in messages:
        content = m["content"]
        if not isinstance(content, list):
            content = [{"type": "text", "text": content}]
        if merged and merged[-1]["role"] == m["role"]:
            merged[-1]["content"].extend(content)
        else:
            merged.append({"role": m["role"], "content": list(content)})
    return merged
