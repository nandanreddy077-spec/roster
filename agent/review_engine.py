"""Reviews' pure pieces: constants and the message template. No DB or
AgentEngine imports here — mirrors referral_engine.py's own shape, which
deliberately does not import from recovery_engine.py either, so each of
Frontdesk/Recovery/Referral/Reviews stays independent of the others'
module structure.
"""

# "Wait an appropriate amount of time" (2026-07-29 plan) before the first
# ask, rather than the instant send this replaces — a day lets the work
# settle before asking. One follow-up only, well after the first ask, never
# more than once (2026-07-30: negative-sentiment handling and the
# never-spam guarantee both depend on this staying a two-touch maximum).
REVIEW_DELAY_DAYS = 1
REVIEW_FOLLOWUP_DELAY_DAYS = 4

# Same wording the old synchronous send in app.py already used — preserved
# verbatim, not rewritten, since it was already-approved copy.
REVIEW_MESSAGE_TEMPLATE = (
    "Thanks for choosing {business_name}! If we did right by you, a quick "
    "review means a lot: {review_link}"
)


class _SafeDict(dict):
    def __missing__(self, key):
        return ""


def render_review_template(text: str, **variables) -> str:
    return text.format_map(_SafeDict(variables))
