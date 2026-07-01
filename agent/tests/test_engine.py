from engine import LOG_JOB_TOOL, TRANSFER_CALL_TOOL, AgentEngine, build_voice_system_prompt
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
