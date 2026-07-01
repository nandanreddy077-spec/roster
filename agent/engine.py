import os
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
        },
        "required": ["service_type", "urgency"],
    },
}

TRANSFER_CALL_TOOL = {
    "name": "transfer_call",
    "description": (
        "Transfer the live phone call to the business owner. Call this when the "
        "caller is upset, has a complaint, or needs something you can't confidently "
        "handle yourself. Always pass the exact destination number given to you in "
        "the system prompt."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "destination": {
                "type": "string",
                "description": "The phone number to transfer to, exactly as given in the system prompt.",
            },
            "reason": {
                "type": "string",
                "description": "One short phrase for why the call is being transferred.",
            },
        },
        "required": ["destination"],
    },
}


def build_system_prompt(client: ClientConfig) -> str:
    return f"""You are the AI front desk for {client.business_name}, a {client.trade} business.

Your job: text back customers who called and couldn't reach anyone, answer their
questions, and capture enough detail to book the job.

Services offered: {", ".join(client.services)}
Hours: {client.hours}
Pricing & FAQ info: {client.pricing_faq}

If the situation is a true emergency (e.g. gas leak, flooding, no heat in freezing
weather), tell the customer you're alerting someone immediately and mark
urgency='emergency' when you call log_job.

Keep replies short, warm, and text-message length (1-3 sentences). Never make up a
price or appointment time you don't actually know. Once you have a service type and
contact info, call log_job to capture the lead, then keep texting naturally."""


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
        only the caller (e.g. Vapi) can actually execute it, so the loop stops
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
        iters = max_iters or MAX_ITERS
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
