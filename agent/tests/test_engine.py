from engine import LOG_JOB_TOOL, TRANSFER_CALL_TOOL, AgentEngine, build_system_prompt, build_voice_system_prompt
from models import ClientConfig


def make_client_config(**overrides):
    defaults = dict(
        client_id="1",
        business_name="Test Co",
        trade="HVAC",
        services=["AC repair"],
        hours="9-5",
        pricing_faq="n/a",
        escalation_phone="+15550000000",
    )
    defaults.update(overrides)
    return ClientConfig(**defaults)


class FakeBlock:
    def __init__(self, type, text=None, name=None, input=None, id=None):
        self.type = type
        self.text = text
        self.name = name
        self.input = input
        self.id = id

    def model_dump(self):
        d = {"type": self.type}
        if self.text is not None:
            d["text"] = self.text
        if self.name is not None:
            d["name"] = self.name
            d["input"] = self.input
            d["id"] = self.id
        return d


class FakeResponse:
    def __init__(self, content):
        self.content = content


class FakeMessagesAPI:
    def __init__(self, responses):
        self._responses = list(responses)

    def create(self, **kwargs):
        return self._responses.pop(0)


class FakeAnthropicClient:
    def __init__(self, responses):
        self.messages = FakeMessagesAPI(responses)


def test_respond_returns_pending_tool_call_for_transfer():
    responses = [
        FakeResponse([
            FakeBlock("text", text="Let me get someone on the line for you."),
            FakeBlock("tool_use", name="transfer_call", input={"destination": "+15550000000"}, id="tu_1"),
        ]),
    ]
    agent = AgentEngine(client=FakeAnthropicClient(responses))

    result = agent.respond(
        make_client_config(),
        [{"role": "user", "content": [{"type": "text", "text": "I want to file a complaint"}]}],
        tools=[LOG_JOB_TOOL, TRANSFER_CALL_TOOL],
    )

    assert result["pending_tool_call"] == {"name": "transfer_call", "input": {"destination": "+15550000000"}}
    assert result["reply"] == "Let me get someone on the line for you."


def test_respond_captures_log_job_from_same_turn_as_transfer():
    responses = [
        FakeResponse([
            FakeBlock("text", text="I'm getting someone right now."),
            FakeBlock(
                "tool_use",
                name="log_job",
                input={"service_type": "gas leak", "urgency": "emergency"},
                id="tu_1",
            ),
            FakeBlock(
                "tool_use",
                name="transfer_call",
                input={"destination": "+15550000000"},
                id="tu_2",
            ),
        ]),
    ]
    agent = AgentEngine(client=FakeAnthropicClient(responses))

    result = agent.respond(
        make_client_config(),
        [{"role": "user", "content": [{"type": "text", "text": "I smell gas in my house"}]}],
        tools=[LOG_JOB_TOOL, TRANSFER_CALL_TOOL],
    )

    assert result["pending_tool_call"]["name"] == "transfer_call"
    assert len(result["jobs"]) == 1
    assert result["jobs"][0]["input"]["service_type"] == "gas leak"


def test_respond_log_job_still_resolved_internally():
    responses = [
        FakeResponse([
            FakeBlock("tool_use", name="log_job", input={"service_type": "AC repair", "urgency": "routine"}, id="tu_1"),
        ]),
        FakeResponse([
            FakeBlock("text", text="Got it, someone will reach out."),
        ]),
    ]
    agent = AgentEngine(client=FakeAnthropicClient(responses))

    result = agent.respond(
        make_client_config(),
        [{"role": "user", "content": [{"type": "text", "text": "AC is broken"}]}],
    )

    assert result["pending_tool_call"] is None
    assert len(result["jobs"]) == 1
    assert result["reply"] == "Got it, someone will reach out."


def test_respond_respects_max_iters_override():
    responses = [
        FakeResponse([
            FakeBlock("tool_use", name="log_job", input={"service_type": "AC repair", "urgency": "routine"}, id="tu_1"),
        ]),
    ]
    agent = AgentEngine(client=FakeAnthropicClient(responses))

    result = agent.respond(
        make_client_config(),
        [{"role": "user", "content": [{"type": "text", "text": "AC is broken"}]}],
        max_iters=1,
    )

    assert result["reply"] == "Thanks! I've got your details and someone will text you shortly."
    assert len(result["jobs"]) == 1


def test_voice_prompt_backup_acknowledges_missed_call():
    config = make_client_config(answer_mode="backup")
    prompt = build_voice_system_prompt(config)
    assert "unanswered" in prompt
    assert config.escalation_phone in prompt


def test_voice_prompt_primary_skips_missed_call_language():
    config = make_client_config(answer_mode="primary")
    prompt = build_voice_system_prompt(config)
    assert "unanswered" not in prompt
    assert "first point of contact" in prompt


def test_voice_prompt_includes_tone():
    config = make_client_config(tone="upbeat and casual")
    prompt = build_voice_system_prompt(config)
    assert "upbeat and casual" in prompt


def test_sms_prompt_includes_tone():
    config = make_client_config(tone="upbeat and casual")
    prompt = build_system_prompt(config)
    assert "upbeat and casual" in prompt


def test_client_config_tone_defaults_to_professional_and_friendly():
    config = make_client_config()
    assert config.tone == "professional and friendly"


# ---- Sprint 1: conversation-quality audit fixes -----------------------------
# (docs/superpowers/specs/2026-07-29-frontdesk-conversation-quality-audit.md)

import datetime as _datetime

FIXED_NOW = _datetime.datetime(2026, 7, 29, 21, 47)


def test_sms_prompt_includes_current_date_and_time():
    config = make_client_config()
    prompt = build_system_prompt(config, now=FIXED_NOW)
    assert "Wednesday, July 29" in prompt
    assert "09:47 PM" in prompt


def test_voice_prompt_includes_current_date_and_time():
    config = make_client_config()
    prompt = build_voice_system_prompt(config, now=FIXED_NOW)
    assert "Wednesday, July 29" in prompt
    assert "09:47 PM" in prompt


def test_sms_prompt_defaults_now_to_the_real_current_time_when_omitted():
    """The injectable `now` is for tests — production calls it with nothing,
    which must fall back to the real clock, not raise or produce a blank."""
    import re

    config = make_client_config()
    prompt = build_system_prompt(config)
    assert re.search(r"\d{2}:\d{2} (AM|PM)", prompt)


TRADE_TRIAGE_CASES = {
    "HVAC": ["thermostat", "burning smell"],
    "Plumbing": ["shutoff", "leaking"],
    "Electrical": ["sparking", "breaker"],
    "Roofing": ["water actively coming inside", "section"],
    "Landscaping": ["fallen tree", "power line"],
    "Cleaning": ["one-time", "recurring"],
    "Garage Door": ["stuck open", "stuck closed"],
    "Pest Control": ["stung", "allergy"],
    "Painting": ["interior", "exterior"],
    "Pool Service": ["cloudy", "fence"],
}


def test_every_supported_trade_gets_its_own_triage_guidance_sms():
    for trade, expected_fragments in TRADE_TRIAGE_CASES.items():
        prompt = build_system_prompt(make_client_config(trade=trade), now=FIXED_NOW)
        for fragment in expected_fragments:
            assert fragment.lower() in prompt.lower(), (
                f"{trade}'s triage guidance missing {fragment!r} from the SMS prompt"
            )


def test_every_supported_trade_gets_its_own_triage_guidance_voice():
    for trade, expected_fragments in TRADE_TRIAGE_CASES.items():
        prompt = build_voice_system_prompt(make_client_config(trade=trade), now=FIXED_NOW)
        for fragment in expected_fragments:
            assert fragment.lower() in prompt.lower(), (
                f"{trade}'s triage guidance missing {fragment!r} from the voice prompt"
            )


def test_trade_matching_is_case_insensitive_like_receptionist_display_name():
    """Mirrors roles.py's own `.strip().lower()` normalization — trade values
    arrive with inconsistent casing (onboarding is free text)."""
    lower_prompt = build_system_prompt(make_client_config(trade="hvac"), now=FIXED_NOW)
    upper_prompt = build_system_prompt(make_client_config(trade="HVAC"), now=FIXED_NOW)
    assert "thermostat" in lower_prompt.lower()
    assert "thermostat" in upper_prompt.lower()


def test_an_unrecognized_trade_still_gets_a_safe_default_note():
    """Never crash or omit triage guidance entirely for a trade string
    outside the 10 supported ones — same DEFAULT-fallback discipline as
    roles.py's DEFAULT_RECEPTIONIST_NAME."""
    prompt = build_system_prompt(make_client_config(trade="Fencing"), now=FIXED_NOW)
    assert "log_job" in prompt   # built fine, no crash
    # And it must not accidentally pick up another trade's specific guidance.
    assert "thermostat" not in prompt.lower()


def test_hvac_triage_does_not_leak_into_a_plumbing_prompt():
    prompt = build_system_prompt(make_client_config(trade="Plumbing"), now=FIXED_NOW)
    assert "thermostat" not in prompt.lower()


def test_voice_prompt_tells_the_model_to_honor_a_direct_request_for_a_human():
    prompt = build_voice_system_prompt(make_client_config(), now=FIXED_NOW)
    lower = prompt.lower()
    assert "speak to a person" in lower or "talk to a person" in lower
    assert "alert_owner" in lower  # the instruction routes through the existing tool, not a new one


def test_both_prompts_instruct_recognizing_reschedule_or_cancel_intent():
    sms = build_system_prompt(make_client_config(), now=FIXED_NOW).lower()
    voice = build_voice_system_prompt(make_client_config(), now=FIXED_NOW).lower()
    for prompt in (sms, voice):
        assert "reschedule" in prompt or "cancel" in prompt
        assert "someone will" in prompt or "confirm" in prompt


def test_both_prompts_ask_for_a_preferred_window_without_promising_availability():
    sms = build_system_prompt(make_client_config(), now=FIXED_NOW).lower()
    voice = build_voice_system_prompt(make_client_config(), now=FIXED_NOW).lower()
    for prompt in (sms, voice):
        assert "preferred_window" in prompt
        assert "never" in prompt or "not a confirmed" in prompt or "not confirm" in prompt


def test_log_job_tool_schema_has_preferred_window():
    props = LOG_JOB_TOOL["input_schema"]["properties"]
    assert "preferred_window" in props
    assert props["preferred_window"]["type"] == "string"
    # Still optional — a caller who won't commit to a window shouldn't block
    # the booking itself.
    assert "preferred_window" not in LOG_JOB_TOOL["input_schema"]["required"]
