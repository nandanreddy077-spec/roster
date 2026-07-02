"""Revenue Recovery's Claude-facing pieces: message templates, tool schemas, and
system prompts. Pure — no DB or AgentEngine imports here; recovery_service.py
wires this into AgentEngine.respond() the same way engine.py's own tools do.
"""
from typing import List, Optional

SEQUENCE_DAYS = [1, 3, 7, 14, 21, 28]

# Days relative to a customer's own anchor_date, not to campaign.started_at.
# A separate constant from SEQUENCE_DAYS on purpose: it can be negative
# (before the renewal date) and is anchored per-customer, not per-campaign.
MEMBERSHIP_OFFSETS = [-30, -14, -7, 0, 7]

FACE_DISPLAY_NAMES = {
    "quote": "Chaser",
    "reactivation": "Rebooker",
    "membership": "Renewals",
}

TEMPLATES = {
    "quote": {
        1: "Hi {customer_name}, just following up on that {service_type} estimate. Still interested? Let me know!",
        3: "{customer_name}, spots are filling up for {service_type} work — want to lock in a time?",
        7: "Quick reminder: your {service_type} quote won't hold forever. Reply YES to book now.",
        14: "{customer_name}, anything holding you back on the {service_type} quote? Happy to answer questions.",
        21: "Your {service_type} quote is expiring soon. Ready to move forward — yes or no?",
        28: "Last chance: ready to book your {service_type}? Reply YES or let me know.",
    },
    "reactivation": {
        1: "Hi {customer_name}, it's been a while since your last {service_type} service. Time for a check-up! Want to book?",
        3: "{customer_name}, regular {service_type} maintenance keeps things running smooth. Let's get you scheduled.",
        7: "Heads up: {service_type} appointments are filling fast this season. Ready to book?",
        14: "{customer_name}, been a while! Ready for your {service_type} maintenance?",
        21: "Last call for {service_type} before the rush. Lock in your appointment today?",
        28: "{customer_name}, your system could use some attention. Book your {service_type} today?",
    },
    "membership": {
        -30: "Hi {customer_name}, your {service_type} plan renews on {renewal_date} — want to get your visit on the books before then?",
        -14: "{customer_name}, your {service_type} renewal is coming up on {renewal_date}. Ready to schedule?",
        -7: "One week left before your {service_type} plan renews on {renewal_date} — lock in your visit now?",
        0: "Today's the day — your {service_type} plan renews. Book your visit now to keep your member pricing and priority scheduling.",
        7: "{customer_name}, your {service_type} plan lapsed last week. Still want to keep your member pricing? Reply YES to renew.",
    },
}

RECORD_RESPONSE_TOOL = {
    "name": "record_response",
    "description": (
        "Call this once the customer's reply makes their intent clear: are they "
        "interested in booking, not interested, or asking to stop messages entirely."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "enum": ["interested", "not_interested", "unsubscribe"],
            },
        },
        "required": ["intent"],
    },
}

CONFIRM_SLOT_TOOL = {
    "name": "confirm_slot",
    "description": (
        "Call this once the customer has clearly picked one of the offered time "
        "slots. Match their reply to the closest offered slot by index."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "slot_index": {
                "type": "integer",
                "description": "0-based index into the offered slots list that best matches the customer's choice.",
            },
        },
        "required": ["slot_index"],
    },
}


class _SafeDict(dict):
    def __missing__(self, key):
        return ""


def render_template(text: str, **variables) -> str:
    return text.format_map(_SafeDict(variables))


def build_recovery_reply_prompt(recovery_job, offered_slots: Optional[List[str]] = None) -> str:
    base = (
        f"You are following up with {recovery_job.customer_name or 'a customer'} about "
        f"{recovery_job.service_type} on behalf of the business. This is an outbound "
        "follow-up conversation over text, not a fresh inquiry.\n\n"
    )
    if offered_slots:
        slot_list = "\n".join(f"{i}: {s}" for i, s in enumerate(offered_slots))
        return base + (
            f"You already offered these time slots:\n{slot_list}\n\n"
            "Figure out which slot the customer's reply matches and call confirm_slot "
            "with its index. If their reply doesn't clearly match any slot, ask a short "
            "clarifying question instead of guessing. If instead the customer says they're "
            "no longer interested or asks to stop being contacted, call record_response with "
            "intent 'not_interested' or 'unsubscribe' instead of confirm_slot."
        )
    return base + (
        "Read the customer's reply and call record_response with their intent: "
        "'interested' if they want to book, 'not_interested' if they're declining, or "
        "'unsubscribe' if they're asking to stop texts. Keep any spoken reply short and warm."
    )
