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


def test_log_job_tool_schema_has_is_estimate():
    """Quote Chaser PR #1: the structured signal that lets recovery_service
    auto-detect an outstanding estimate — replaces the old free-text-only
    "mention it in notes" approach."""
    props = LOG_JOB_TOOL["input_schema"]["properties"]
    assert "is_estimate" in props
    assert props["is_estimate"]["type"] == "boolean"
    assert "is_estimate" not in LOG_JOB_TOOL["input_schema"]["required"]


def test_both_prompts_instruct_flagging_estimate_calls_as_is_estimate():
    sms = build_system_prompt(make_client_config(), now=FIXED_NOW).lower()
    voice = build_voice_system_prompt(make_client_config(), now=FIXED_NOW).lower()
    for prompt in (sms, voice):
        assert "is_estimate" in prompt


# ---- Sprint 2: customer context & qualification -----------------------------
# (docs/superpowers/specs/2026-07-29-frontdesk-conversation-quality-audit.md,
# items 5 and 13)

def test_sms_prompt_mentions_service_area_when_set():
    config = make_client_config(service_area="within 20 miles of Austin, TX")
    prompt = build_system_prompt(config, now=FIXED_NOW)
    assert "within 20 miles of Austin, TX" in prompt
    assert "outside" in prompt.lower()
    assert "don't book" in prompt.lower() or "do not book" in prompt.lower()


def test_voice_prompt_mentions_service_area_when_set():
    config = make_client_config(service_area="within 20 miles of Austin, TX")
    prompt = build_voice_system_prompt(config, now=FIXED_NOW)
    assert "within 20 miles of Austin, TX" in prompt
    assert "outside" in prompt.lower()


def test_prompt_omits_service_area_language_when_not_configured():
    """No restriction is asserted when the owner never set one — must not
    render a broken 'Service area: .' line or imply a limit that doesn't
    exist."""
    config = make_client_config(service_area="")
    prompt = build_system_prompt(config, now=FIXED_NOW)
    assert "Service area:" not in prompt


def test_client_config_service_area_defaults_to_empty():
    config = make_client_config()
    assert config.service_area == ""


def test_both_prompts_distinguish_repair_replacement_and_estimate_calls():
    sms = build_system_prompt(make_client_config(), now=FIXED_NOW).lower()
    voice = build_voice_system_prompt(make_client_config(), now=FIXED_NOW).lower()
    for prompt in (sms, voice):
        assert "replacement" in prompt
        assert "estimate" in prompt
        assert "routine" in prompt


# ---- Sprint 3: sales & conversation excellence ------------------------------
# (docs/superpowers/specs/2026-07-29-frontdesk-conversation-quality-audit.md,
# items 8, 12, 14, 16 + a conversation-flow cleanup pass)

OBJECTION_FRAGMENTS = [
    "too expensive", "quoted less", "think about it", "shopping around",
    "acknowledg",  # matches "acknowledge"/"acknowledged"
    "value", "discount", "owner callback",
]


def test_both_prompts_cover_every_named_objection():
    sms = build_system_prompt(make_client_config(), now=FIXED_NOW).lower()
    voice = build_voice_system_prompt(make_client_config(), now=FIXED_NOW).lower()
    for prompt in (sms, voice):
        for fragment in OBJECTION_FRAGMENTS:
            assert fragment in prompt, f"missing objection-handling fragment {fragment!r}"


def test_both_prompts_never_invent_a_discount():
    sms = build_system_prompt(make_client_config(), now=FIXED_NOW).lower()
    voice = build_voice_system_prompt(make_client_config(), now=FIXED_NOW).lower()
    for prompt in (sms, voice):
        assert "never" in prompt and "discount" in prompt


PRICING_GUIDANCE_FRAGMENTS = ["diagnostic", "repair pricing", "estimate", "replacement pricing", "honestly"]


def test_both_prompts_distinguish_fee_types_and_are_honest_about_unknown_pricing():
    sms = build_system_prompt(make_client_config(), now=FIXED_NOW).lower()
    voice = build_voice_system_prompt(make_client_config(), now=FIXED_NOW).lower()
    for prompt in (sms, voice):
        for fragment in PRICING_GUIDANCE_FRAGMENTS:
            assert fragment in prompt, f"missing pricing-guidance fragment {fragment!r}"


def test_both_prompts_disclose_the_service_call_fee_naturally_and_only_once():
    sms = build_system_prompt(make_client_config(), now=FIXED_NOW).lower()
    voice = build_voice_system_prompt(make_client_config(), now=FIXED_NOW).lower()
    for prompt in (sms, voice):
        assert "service-call fee" in prompt or "service call fee" in prompt
        assert "once" in prompt


def test_both_prompts_recognize_multi_problem_calls():
    sms = build_system_prompt(make_client_config(), now=FIXED_NOW).lower()
    voice = build_voice_system_prompt(make_client_config(), now=FIXED_NOW).lower()
    for prompt in (sms, voice):
        assert "my ac isn't cooling and i also have a leaking water heater" in prompt
        assert "separate issues" in prompt
        assert "once per issue" in prompt


# ---- Regression: every Sprint 1/2 fixture must still hold after the
# conversation-flow cleanup pass --------------------------------------------

def test_regression_date_time_trade_triage_and_preferred_window_still_present():
    prompt = build_system_prompt(make_client_config(trade="Electrical"), now=FIXED_NOW)
    assert "Wednesday, July 29" in prompt
    assert "sparking" in prompt.lower()
    assert "preferred_window" in prompt.lower()


def test_regression_voice_prompt_still_honest_about_escalation():
    """Mirrors test_voice_loop_integration.py's own
    test_voice_prompt_is_honest_about_escalation — the cleanup pass must not
    reintroduce a false "transfer" promise or drop 911/alert_owner/the
    escalation number."""
    config = make_client_config(escalation_phone="512-555-0148")
    prompt = build_voice_system_prompt(config, now=FIXED_NOW)
    lower = prompt.lower()
    assert "transfer" not in lower
    assert "911" in prompt
    assert "512-555-0148" in prompt
    assert "alert_owner" in prompt


def test_voice_prompt_does_not_volunteer_the_escalation_number_for_a_plain_emergency():
    """2026-08-10 fix: a real test call showed the AI reciting the owner's
    personal cell for every emergency, on top of booking the job — which read
    to the caller as a punt even though the booking also happened. The number
    is now a fallback for an alert_owner failure or an explicit request, not
    the default script; the owner's escalation stays invisible to the caller."""
    config = make_client_config(escalation_phone="512-555-0148")
    prompt = build_voice_system_prompt(config, now=FIXED_NOW).lower()
    assert "never" in prompt and "512-555-0148" in prompt
    assert "on-call technician" in prompt or "on-call" in prompt
    assert "failed" in prompt  # the number is a fallback for a failed alert, not the default


def test_voice_prompt_instructs_a_spoken_filler_before_a_tool_call():
    """2026-08-10: real calls showed several seconds of dead air while log_job
    or alert_owner ran, since the model's tool-only turns carried no spoken
    acknowledgment. The prompt now asks for a short line in the same reply as
    the tool call so the caller always hears something first."""
    prompt = build_voice_system_prompt(make_client_config(), now=FIXED_NOW).lower()
    assert "log_job or alert_owner" in prompt
    assert "same reply" in prompt
    assert "dead air" in prompt


def test_regression_service_area_and_estimate_language_still_present():
    config = make_client_config(service_area="within 20 miles of Austin, TX")
    prompt = build_system_prompt(config, now=FIXED_NOW)
    assert "within 20 miles of Austin, TX" in prompt
    assert "replacement" in prompt.lower()


# ---- sounding like a person on the phone ----------------------------------

def _voice(**overrides):
    fields = dict(client_id="1", business_name="Ridgeline Plumbing", trade="plumbing",
                  services=["drain cleaning"], hours="9-5", pricing_faq="",
                  escalation_phone="+15125550149")
    fields.update(overrides)
    return build_voice_system_prompt(ClientConfig(**fields))


def test_the_voice_prompt_bans_the_call_center_tells():
    """What gives an AI away on the phone is phrasing, not the voice."""
    prompt = _voice().lower()
    for tell in ["i'd be happy to assist", "anything else i can help",
                 "thank you for your patience", "absolutely"]:
        assert tell in prompt, f"the prompt no longer names {tell!r} as a phrase to avoid"
    assert "contractions" in prompt
    assert "one thought per turn" in prompt


def test_the_voice_prompt_tells_it_to_stop_when_interrupted():
    assert "interrupt" in _voice().lower()


def test_the_greeting_is_capped_at_one_sentence():
    """Measured on a real call: the opening ran 4.5 seconds of speech before the
    caller could say a word."""
    for mode in ("primary", "backup"):
        prompt = _voice(answer_mode=mode)
        assert "ONE short sentence" in prompt


def test_it_never_claims_to_be_human_but_stays_warm():
    """The line between 'sounds human' and 'lies about being human'. Sounding
    natural is the goal; denying it when asked is a legal and trust problem
    (bot-disclosure statutes), and it is worse for the caller than an honest
    answer they didn't mind."""
    prompt = _voice()
    assert "Never claim to be a human being." in prompt
    assert "AI assistant for" in prompt
    assert "carry straight on helping" in prompt
