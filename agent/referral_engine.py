"""Referrals' Claude-facing pieces: constants and the extraction tool schema.
Pure — no DB or AgentEngine imports here. Deliberately does not import from
recovery_engine.py (a tiny render_template equivalent is duplicated below
instead) so Referrals stays independent of Recovery's module structure — the
same spirit as Frontdesk and Recovery not importing each other.
"""

REFERRAL_DELAY_DAYS = 4
REFERRAL_REPLY_WINDOW_DAYS = 3

REFERRAL_MESSAGE_TEMPLATE = (
    "Hey {customer_name}, glad we could help with your {service_type}! Know "
    "anyone else who could use us? {incentive} — just reply with their name "
    "and number and we'll take it from there."
)

RECORD_REFERRAL_TOOL = {
    "name": "record_referral",
    "description": (
        "Call this after reading the customer's reply to a referral request. "
        "Extract the referred person's name and/or phone number if present. "
        "If the reply doesn't contain a clear name or phone number, call this "
        "with both fields omitted rather than guessing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "referred_name": {
                "type": "string",
                "description": "The referred person's name, if the customer mentioned one.",
            },
            "referred_phone": {
                "type": "string",
                "description": "The referred person's phone number, if the customer mentioned one.",
            },
        },
    },
}


class _SafeDict(dict):
    def __missing__(self, key):
        return ""


def render_referral_template(text: str, **variables) -> str:
    return text.format_map(_SafeDict(variables))


def build_referral_reply_prompt() -> str:
    return (
        "You just sent this customer a referral request after completing their "
        "service. Read their reply and call record_referral with the referred "
        "person's name and/or phone number if they gave one. If their reply "
        "doesn't contain a referral (e.g. they declined, asked a question, or "
        "said something unrelated), call record_referral with both fields "
        "omitted — do not guess or invent a name or number. Keep any spoken "
        "reply short and warm."
    )
