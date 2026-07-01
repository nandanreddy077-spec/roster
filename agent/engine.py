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
    def __init__(self, api_key: Optional[str] = None):
        self.client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))

    def respond(self, client_config: ClientConfig, history: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Run one customer turn as a bounded Think -> Act -> Observe loop.

        The model may call log_job mid-turn; we execute it, feed the result back,
        and let the model produce a natural closing reply to the customer. Loops
        until the model answers with no further tool call, or hits MAX_ITERS.

        Returns:
          reply         final text to send the customer
          jobs          captured log_job inputs (caller persists them)
          new_messages  serialized assistant/tool_result turns generated this
                        turn, ready for the caller to store as history
        """
        system = build_system_prompt(client_config)
        messages = list(history)  # working copy; never mutate the caller's list
        new_messages: List[Dict[str, Any]] = []
        captured_jobs: List[Dict[str, Any]] = []
        reply_text = ""

        for _ in range(MAX_ITERS):
            resp = self.client.messages.create(
                model=MODEL,
                max_tokens=512,
                system=system,
                tools=[LOG_JOB_TOOL],
                messages=messages,
            )

            assistant_content = serialize_content(resp.content)
            messages.append({"role": "assistant", "content": assistant_content})
            new_messages.append({"role": "assistant", "content": assistant_content})

            text_parts = [b.text for b in resp.content if b.type == "text"]
            tool_uses = [b for b in resp.content if b.type == "tool_use" and b.name == "log_job"]

            if not tool_uses:
                reply_text = " ".join(text_parts).strip()  # Done: clean final answer
                break

            # Act + Observe: capture each job and feed an acknowledgment back in.
            tool_results = []
            for tu in tool_uses:
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

        return {"reply": reply_text, "jobs": captured_jobs, "new_messages": new_messages}


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
