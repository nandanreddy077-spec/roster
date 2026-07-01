# AI Receptionist (Vapi voice agent) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a live-voice AI receptionist (via Vapi) that reuses the existing `engine.py` Claude brain, so a caller can have a real phone conversation, get a job booked, or get transferred to the owner — same `Client`/`Job` data model and dashboard as the existing SMS agent.

**Architecture:** Vapi answers the call and handles speech-to-text/text-to-speech; Vapi is configured with a Custom LLM pointing at a new `POST /voice/chat/completions` endpoint in `app.py`. That endpoint resolves the `Client` by the number called, runs `AgentEngine.respond()` (extended with a `transfer_call` tool and a voice-specific system prompt), and streams the reply back in OpenAI-compatible SSE format.

**Tech Stack:** Python, FastAPI, SQLModel/SQLite, Anthropic SDK, Pydantic, pytest.

**Spec:** `docs/superpowers/specs/2026-07-01-ai-receptionist-design.md`

## Global Constraints

- All commands below assume the working directory is `/Users/nandanreddyavanaganti/new_idea/agent`.
- One shared Vapi assistant serves every client — no per-client Vapi configuration (per spec's "Confirmed decisions").
- The voice receptionist replaces the SMS text-back agent for missed calls; the SMS agent's code path is untouched and still used for direct inbound texts (per spec's "Relationship to the existing SMS text-back agent").
- `log_job` stays an internal tool resolved inside `AgentEngine.respond()`; `transfer_call` is a passthrough tool that must stop the loop and bubble up to the HTTP layer untouched, since only Vapi can execute a real transfer.
- Tool loop is bounded to 2 iterations for voice (vs. the SMS agent's existing 4), per spec's error-handling section.
- No placeholder/TBD code — every step below is complete, runnable code.

---

### Task 1: `answer_mode` field + test infrastructure

**Files:**
- Modify: `models.py:5-13` (add field to `ClientConfig`)
- Modify: `db_models.py:10-34` (add field to `Client`, update `to_config()`)
- Modify: `seed.py:43-54` (seed demo with explicit `answer_mode`)
- Modify: `app.py:56-80` (`create_client` accepts the new form field)
- Modify: `templates/new_client.html` (add the form control)
- Modify: `requirements.txt` (add `pytest`)
- Create: `conftest.py` (project-root, so pytest puts `agent/` on `sys.path`)
- Create: `tests/test_models.py`

**Interfaces:**
- Produces: `ClientConfig.answer_mode: str` (default `"backup"`), `Client.answer_mode: str` (default `"backup"`), `Client.to_config()` now includes it. Every later task that builds a `ClientConfig`/`Client` in tests can rely on this field existing.
- Produces (test infra): `session` fixture (isolated in-memory `Session`) and `test_engine` fixture (isolated in-memory SQLAlchemy engine), both in `conftest.py`, available to every subsequent test file.

- [ ] **Step 1: Add pytest and create the test infrastructure**

Append to `requirements.txt`:
```
pytest>=8.0.0
```

Create `conftest.py`:
```python
import pytest
from sqlmodel import Session, SQLModel, create_engine

import db_models  # noqa: F401  (registers tables with SQLModel.metadata)


@pytest.fixture
def test_engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(test_engine):
    with Session(test_engine) as s:
        yield s
```

Run: `pip install -r requirements.txt`

- [ ] **Step 2: Write the failing test**

Create `tests/test_models.py`:
```python
import json

from db_models import Client


def test_client_to_config_includes_answer_mode(session):
    client = Client(
        business_name="Test Co",
        trade="HVAC",
        services_json=json.dumps(["AC repair"]),
        hours="9-5",
        pricing_faq="n/a",
        escalation_phone="+15550000000",
        answer_mode="primary",
    )
    session.add(client)
    session.commit()
    session.refresh(client)

    config = client.to_config()

    assert config.answer_mode == "primary"


def test_client_answer_mode_defaults_to_backup(session):
    client = Client(
        business_name="Test Co",
        trade="HVAC",
        services_json=json.dumps(["AC repair"]),
        hours="9-5",
        pricing_faq="n/a",
        escalation_phone="+15550000000",
    )
    session.add(client)
    session.commit()
    session.refresh(client)

    assert client.answer_mode == "backup"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL — `TypeError: 'answer_mode' is an invalid keyword argument for Client`

- [ ] **Step 4: Implement — add the field**

In `models.py`, replace the whole file with:
```python
from dataclasses import dataclass
from typing import List


@dataclass
class ClientConfig:
    client_id: str
    business_name: str
    trade: str
    services: List[str]
    hours: str
    pricing_faq: str
    escalation_phone: str
    answer_mode: str = "backup"  # "primary" (AI answers every call) or "backup" (AI answers only unanswered calls)
```

In `db_models.py`, replace lines 10-34 (the `Client` class) with:
```python
class Client(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    business_name: str
    trade: str
    services_json: str  # JSON-encoded list[str]
    hours: str
    pricing_faq: str
    escalation_phone: str
    answer_mode: str = Field(default="backup")  # "primary" or "backup" - set during onboarding
    inbound_number: Optional[str] = None  # the business line customers text/call; routes inbound SMS
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def services(self) -> List[str]:
        return json.loads(self.services_json)

    def to_config(self) -> ClientConfig:
        return ClientConfig(
            client_id=str(self.id),
            business_name=self.business_name,
            trade=self.trade,
            services=self.services,
            hours=self.hours,
            pricing_faq=self.pricing_faq,
            escalation_phone=self.escalation_phone,
            answer_mode=self.answer_mode,
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_models.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Propagate the field through seed data, the create form, and the template**

In `seed.py`, in the `Client(...)` construction (around line 43-54), add the field:
```python
        client = Client(
            business_name=DEMO_NAME,
            trade="HVAC",
            services_json=json.dumps(
                ["AC repair", "AC installation", "Furnace repair", "Maintenance plans"]
            ),
            hours="Mon-Sat 7am-7pm, 24/7 emergency",
            pricing_faq="Diagnostic visit $89 (waived if repaired same day). Same-day "
                        "emergency call-out +$50. No exact quotes over text — tech confirms on site.",
            escalation_phone="+19014038929",
            answer_mode="backup",
            inbound_number="+19014038929",
        )
```

In `app.py`, replace the `create_client` function (lines 56-80) with:
```python
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
```

In `templates/new_client.html`, insert this new label right before the submit button (before the `<button class="btn btn-primary" type="submit">Create client</button>` line):
```html
  <label>Answer mode
    <select name="answer_mode">
      <option value="backup" selected>Backup — AI answers only if no one picks up</option>
      <option value="primary">Primary — AI answers every call</option>
    </select>
  </label>
```

- [ ] **Step 7: Commit**

```bash
git add requirements.txt conftest.py tests/test_models.py models.py db_models.py seed.py app.py templates/new_client.html
git commit -m "Add answer_mode field to Client/ClientConfig, set up pytest infra"
```

---

### Task 2: Engine — injectable client, `transfer_call` tool, passthrough tool loop

**Files:**
- Modify: `engine.py` (whole file rewritten below)
- Create: `tests/test_engine.py`

**Interfaces:**
- Consumes: `ClientConfig` from `models.py` (Task 1).
- Produces: `TRANSFER_CALL_TOOL: dict` (tool schema), `AgentEngine(api_key=None, client=None)` (now accepts an injectable client for testing), `AgentEngine.respond(client_config, history, tools=None, system_prompt=None, max_iters=None) -> dict` where the returned dict now always includes a `"pending_tool_call"` key: `None`, or `{"name": str, "input": dict}` when the model called any tool other than `log_job`. Later tasks (voice adapter) rely on exactly this key and shape.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_engine.py`:
```python
from engine import LOG_JOB_TOOL, TRANSFER_CALL_TOOL, AgentEngine
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_engine.py -v`
Expected: FAIL — `TypeError: AgentEngine.__init__() got an unexpected keyword argument 'client'` (and `TRANSFER_CALL_TOOL` import error)

- [ ] **Step 3: Implement — rewrite `engine.py`**

Replace the whole file with:
```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_engine.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `pytest -v`
Expected: PASS (all tests from Task 1 and Task 2)

- [ ] **Step 6: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "Add transfer_call passthrough tool and injectable client to AgentEngine"
```

---

### Task 3: Voice-specific system prompt

**Files:**
- Modify: `engine.py` (add one function)
- Modify: `tests/test_engine.py` (append tests)

**Interfaces:**
- Consumes: `ClientConfig` (with `answer_mode`, `escalation_phone` from Task 1).
- Produces: `build_voice_system_prompt(client: ClientConfig) -> str`. The voice adapter (Task 4) calls this directly.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_engine.py`:
```python
from engine import build_voice_system_prompt


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_engine.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_voice_system_prompt'`

- [ ] **Step 3: Implement — add the function to `engine.py`**

Add this function right after `build_system_prompt` in `engine.py`:
```python
def build_voice_system_prompt(client: ClientConfig) -> str:
    if client.answer_mode == "primary":
        greeting_note = "You are the first point of contact — answer warmly like a normal receptionist."
    else:
        greeting_note = (
            "The caller just had their call go unanswered — open by acknowledging "
            "that before helping them."
        )

    return f"""You are the AI receptionist for {client.business_name}, a {client.trade} business, \
speaking live on the phone with a caller.

{greeting_note}

Services offered: {", ".join(client.services)}
Hours: {client.hours}
Pricing & FAQ info: {client.pricing_faq}

Speak naturally, in short sentences suited for a live conversation — this is a phone \
call, not a text message. Never make up a price or appointment time you don't \
actually know.

If the situation is a true emergency (e.g. gas leak, flooding, no heat in freezing \
weather), tell the caller you're getting someone right now and call transfer_call \
with destination "{client.escalation_phone}".

If the caller is upset, has a complaint, or asks for something you can't confidently \
handle, call transfer_call with destination "{client.escalation_phone}" rather than \
guessing.

Once you have a service type and contact info, call log_job to capture the lead \
before ending the call."""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_engine.py -v`
Expected: PASS (5 tests total in this file)

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "Add voice-specific system prompt with answer_mode framing"
```

---

### Task 4: Voice adapter — Vapi schema + `handle_voice_turn`

**Files:**
- Create: `voice_adapter.py`
- Modify: `conftest.py` (add `StubAgent` test helper)
- Create: `tests/test_voice_adapter.py`

**Interfaces:**
- Consumes: `AgentEngine.respond(...)` shape from Task 2 (`reply`, `jobs`, `new_messages`, `pending_tool_call`), `build_voice_system_prompt` from Task 3, `Client`/`Job`/`Message` from `db_models.py`.
- Produces: `VapiMessage`, `VapiCustomer`, `VapiPhoneNumber`, `VapiCall`, `VapiChatRequest` (Pydantic models), `handle_voice_turn(session, agent, client, request) -> {"reply": str, "pending_tool_call": dict | None}`. Task 5 (the HTTP endpoint) imports and calls this directly.

- [ ] **Step 1: Write the failing tests**

Add to `conftest.py` (append, after the existing fixtures):
```python
class StubAgent:
    """Test double for AgentEngine — returns a canned respond() result."""

    def __init__(self, result):
        self._result = result

    def respond(self, client_config, history, tools=None, system_prompt=None, max_iters=None):
        return self._result
```

Create `tests/test_voice_adapter.py`:
```python
import json

from sqlmodel import select

from conftest import StubAgent
from db_models import Client, Job, Message


def make_client(session) -> Client:
    client = Client(
        business_name="Test Co",
        trade="HVAC",
        services_json=json.dumps(["AC repair"]),
        hours="9-5",
        pricing_faq="n/a",
        escalation_phone="+15550000000",
    )
    session.add(client)
    session.commit()
    session.refresh(client)
    return client


def test_to_engine_history_drops_system_message():
    from voice_adapter import VapiMessage, _to_engine_history

    messages = [
        VapiMessage(role="system", content="you are an assistant"),
        VapiMessage(role="user", content="hi"),
    ]
    history = _to_engine_history(messages)

    assert len(history) == 1
    assert history[0]["role"] == "user"
    assert history[0]["content"][0]["text"] == "hi"


def test_handle_voice_turn_persists_jobs_and_messages(session):
    from voice_adapter import VapiCall, VapiChatRequest, VapiCustomer, VapiMessage, VapiPhoneNumber, handle_voice_turn

    client = make_client(session)
    stub_result = {
        "reply": "Got it, I've logged that.",
        "jobs": [{"id": "tu_1", "input": {"service_type": "AC repair", "urgency": "routine"}}],
        "new_messages": [{"role": "assistant", "content": [{"type": "text", "text": "Got it, I've logged that."}]}],
        "pending_tool_call": None,
    }
    agent = StubAgent(stub_result)
    request = VapiChatRequest(
        model="claude-sonnet-4-6",
        messages=[VapiMessage(role="user", content="My AC is broken")],
        call=VapiCall(
            id="call_1",
            phoneNumber=VapiPhoneNumber(number="+19998887777"),
            customer=VapiCustomer(number="+15551234567"),
        ),
    )

    result = handle_voice_turn(session, agent, client, request)

    assert result["reply"] == "Got it, I've logged that."
    assert result["pending_tool_call"] is None

    jobs = session.exec(select(Job).where(Job.client_id == client.id)).all()
    assert len(jobs) == 1
    assert jobs[0].callback_number == "+15551234567"

    messages = session.exec(select(Message).where(Message.client_id == client.id)).all()
    roles = [m.role for m in messages]
    assert "user" in roles
    assert "assistant" in roles
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_voice_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_adapter'`

- [ ] **Step 3: Implement — create `voice_adapter.py`**

```python
"""Adapter that lets Vapi's Custom LLM integration drive the same AgentEngine
that answers SMS. Vapi speaks an OpenAI-compatible chat format; this module
translates between that and AgentEngine.respond().
"""
import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel
from sqlmodel import Session

from db_models import Client, Job, Message
from engine import LOG_JOB_TOOL, TRANSFER_CALL_TOOL, build_voice_system_prompt

VOICE_THREAD_PREFIX = "voice:"
VOICE_MAX_ITERS = 2  # tighter than the SMS agent's 4 - every round-trip adds live latency


class VapiMessage(BaseModel):
    role: str
    content: str = ""


class VapiCustomer(BaseModel):
    number: Optional[str] = None


class VapiPhoneNumber(BaseModel):
    number: Optional[str] = None


class VapiCall(BaseModel):
    id: str
    phoneNumber: Optional[VapiPhoneNumber] = None
    customer: Optional[VapiCustomer] = None


class VapiChatRequest(BaseModel):
    model: str
    messages: List[VapiMessage]
    call: VapiCall
    customer: Optional[VapiCustomer] = None


def _thread_id(caller_number: str) -> str:
    return f"{VOICE_THREAD_PREFIX}{caller_number}"


def _to_engine_history(vapi_messages: List[VapiMessage]) -> List[Dict[str, Any]]:
    """Vapi resends the full transcript each turn as plain {role, content} pairs;
    convert to the content-block shape AgentEngine expects, dropping the system
    message (we build our own system prompt from the Client config instead)."""
    history = []
    for m in vapi_messages:
        if m.role == "system":
            continue
        history.append({"role": m.role, "content": [{"type": "text", "text": m.content}]})
    return history


def handle_voice_turn(session: Session, agent: Any, client: Client, request: VapiChatRequest) -> Dict[str, Any]:
    """Run one voice turn through the agent and persist it like any other channel.

    Returns {"reply": str, "pending_tool_call": dict | None}.
    """
    caller_number = (request.call.customer.number if request.call.customer else None) or "unknown"
    thread = _thread_id(caller_number)

    history = _to_engine_history(request.messages)
    system_prompt = build_voice_system_prompt(client.to_config())

    if history and history[-1]["role"] == "user":
        session.add(
            Message(
                client_id=client.id,
                customer_phone=thread,
                role="user",
                content_json=json.dumps(history[-1]["content"][0]["text"]),
            )
        )

    result = agent.respond(
        client.to_config(),
        history,
        tools=[LOG_JOB_TOOL, TRANSFER_CALL_TOOL],
        system_prompt=system_prompt,
        max_iters=VOICE_MAX_ITERS,
    )

    for nm in result["new_messages"]:
        session.add(
            Message(
                client_id=client.id,
                customer_phone=thread,
                role=nm["role"],
                content_json=json.dumps(nm["content"]),
            )
        )

    for call in result["jobs"]:
        ji = call["input"]
        session.add(
            Job(
                client_id=client.id,
                customer_phone=thread,
                customer_name=ji.get("customer_name"),
                service_type=ji["service_type"],
                urgency=ji["urgency"],
                address=ji.get("address"),
                callback_number=ji.get("callback_number") or caller_number,
                notes=ji.get("notes"),
            )
        )

    session.commit()
    return {"reply": result["reply"], "pending_tool_call": result["pending_tool_call"]}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_voice_adapter.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `pytest -v`
Expected: PASS (all tests so far)

- [ ] **Step 6: Commit**

```bash
git add voice_adapter.py conftest.py tests/test_voice_adapter.py
git commit -m "Add voice_adapter: Vapi Custom LLM schema + handle_voice_turn"
```

---

### Task 5: `POST /voice/chat/completions` endpoint (happy path)

**Files:**
- Modify: `app.py` (add imports, SSE helpers, and the endpoint)
- Modify: `requirements.txt` (add `httpx`, needed by FastAPI's `TestClient`)
- Create: `tests/test_voice_endpoint.py`

**Interfaces:**
- Consumes: `handle_voice_turn` (Task 4), `agent` instance from `service.py`, `_find_client_by_inbound` (already in `app.py`).
- Produces: the live `POST /voice/chat/completions` route. Task 6 wraps this same function body with error handling — no new interface for later tasks.

- [ ] **Step 1: Add `httpx` for the test client**

Append to `requirements.txt`:
```
httpx>=0.27.0
```

Run: `pip install -r requirements.txt`

- [ ] **Step 2: Write the failing tests**

Create `tests/test_voice_endpoint.py`:
```python
import json

from fastapi.testclient import TestClient
from sqlmodel import Session

import app as app_module
from db_models import Client


def test_voice_endpoint_unknown_number_returns_fallback(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client = TestClient(app_module.app)

    payload = {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "hello"}],
        "call": {
            "id": "call_1",
            "phoneNumber": {"number": "+10000000000"},
            "customer": {"number": "+15551234567"},
        },
    }
    response = client.post("/voice/chat/completions", json=payload)

    assert response.status_code == 200
    assert "isn't set up yet" in response.text


def test_voice_endpoint_known_client_streams_reply(monkeypatch, test_engine):
    with Session(test_engine) as session:
        session.add(
            Client(
                business_name="Test Co",
                trade="HVAC",
                services_json=json.dumps(["AC repair"]),
                hours="9-5",
                pricing_faq="n/a",
                escalation_phone="+15550000000",
                inbound_number="+10000000000",
            )
        )
        session.commit()

    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(
        app_module,
        "shared_agent",
        app_module.StubAgent(
            {
                "reply": "Sure, what's the issue?",
                "jobs": [],
                "new_messages": [
                    {"role": "assistant", "content": [{"type": "text", "text": "Sure, what's the issue?"}]}
                ],
                "pending_tool_call": None,
            }
        ),
    )

    test_client = TestClient(app_module.app)
    payload = {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "My AC broke"}],
        "call": {
            "id": "call_2",
            "phoneNumber": {"number": "+10000000000"},
            "customer": {"number": "+15551234567"},
        },
    }
    response = test_client.post("/voice/chat/completions", json=payload)

    assert response.status_code == 200
    assert "Sure, what's the issue?" in response.text
```

Note: this test references `app_module.StubAgent` — import it into `app.py` in the next step (re-exported from `conftest.py`'s test helper is not appropriate since `app.py` is production code; instead `app.py` will import the real `AgentEngine` type only, and the test replaces the module-level `shared_agent` object directly via `monkeypatch.setattr`, so `app_module.StubAgent` must resolve to the `StubAgent` class — add `from conftest import StubAgent` is wrong direction (production importing test code). Instead, reference `conftest.StubAgent` directly in the test:

Replace the `app_module.StubAgent(...)` line above with a direct import at the top of `tests/test_voice_endpoint.py`:
```python
from conftest import StubAgent
```
and use `StubAgent(...)` (not `app_module.StubAgent(...)`) in the test body.

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_voice_endpoint.py -v`
Expected: FAIL — `404 Not Found` for `/voice/chat/completions` (route doesn't exist yet)

- [ ] **Step 4: Implement — add the endpoint to `app.py`**

Add these imports at the top of `app.py`, alongside the existing ones:
```python
import time
import uuid

from fastapi.responses import StreamingResponse

from service import agent as shared_agent
from voice_adapter import VapiChatRequest, handle_voice_turn
```

Add these helpers and the route at the end of `app.py`:
```python
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

        result = handle_voice_turn(session, shared_agent, client, payload)

    return StreamingResponse(
        _voice_stream(request_id, payload.model, result["reply"], result["pending_tool_call"]),
        media_type="text/event-stream",
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_voice_endpoint.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Run the full suite to confirm no regressions**

Run: `pytest -v`
Expected: PASS (all tests so far)

- [ ] **Step 7: Commit**

```bash
git add app.py requirements.txt tests/test_voice_endpoint.py
git commit -m "Add POST /voice/chat/completions endpoint (happy path)"
```

---

### Task 6: Error handling — fallback to transfer on engine failure

**Files:**
- Modify: `app.py` (wrap the endpoint's engine call in try/except)
- Modify: `tests/test_voice_endpoint.py` (append test)

**Interfaces:**
- Consumes: same as Task 5 — no new interface, this hardens existing behavior.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_voice_endpoint.py`:
```python
class RaisingAgent:
    def respond(self, *args, **kwargs):
        raise RuntimeError("boom")


def test_voice_endpoint_engine_error_falls_back_to_transfer(monkeypatch, test_engine):
    with Session(test_engine) as session:
        session.add(
            Client(
                business_name="Test Co",
                trade="HVAC",
                services_json=json.dumps(["AC repair"]),
                hours="9-5",
                pricing_faq="n/a",
                escalation_phone="+15559990000",
                inbound_number="+10000000000",
            )
        )
        session.commit()

    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(app_module, "shared_agent", RaisingAgent())

    test_client = TestClient(app_module.app)
    payload = {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "My AC broke"}],
        "call": {
            "id": "call_3",
            "phoneNumber": {"number": "+10000000000"},
            "customer": {"number": "+15551234567"},
        },
    }
    response = test_client.post("/voice/chat/completions", json=payload)

    assert response.status_code == 200
    assert "transfer_call" in response.text
    assert "+15559990000" in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_voice_endpoint.py -v`
Expected: FAIL — `RuntimeError: boom` propagates as an unhandled 500

- [ ] **Step 3: Implement — wrap the engine call**

In `app.py`, replace the body of `voice_chat_completions` (inside the `with Session(engine) as session:` block, the `client is None` branch stays the same) with:
```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_voice_endpoint.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `pytest -v`
Expected: PASS (all tests)

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_voice_endpoint.py
git commit -m "Fall back to transfer_call when the voice engine errors"
```

---

### Task 7: Manual voice simulator

**Files:**
- Create: `simulate_voice.py`

**Interfaces:**
- Consumes: `handle_voice_turn` (Task 4), `agent` from `service.py`, `Client` from `db_models.py`.
- Produces: a standalone CLI script. Nothing later depends on it — this is a manual/dev tool, mirroring the existing `simulate.py`.

- [ ] **Step 1: Implement the script**

Create `simulate_voice.py`:
```python
"""Manually exercise the voice receptionist without a live Vapi/Twilio call.

Feeds typed customer lines through the same handle_voice_turn() path the real
/voice/chat/completions endpoint uses, building up Vapi-shaped message history
exactly like Vapi would resend it each turn.
"""
import sys

from sqlmodel import Session

from db import engine, init_db
from db_models import Client
from service import agent
from voice_adapter import VapiCall, VapiChatRequest, VapiCustomer, VapiMessage, VapiPhoneNumber, handle_voice_turn


def main():
    if len(sys.argv) < 2:
        print("Usage: python simulate_voice.py <client_id>")
        sys.exit(1)

    client_id = int(sys.argv[1])
    init_db()

    with Session(engine) as session:
        client = session.get(Client, client_id)
        if client is None:
            print(f"No client with id {client_id}")
            sys.exit(1)

        print(f"Simulating a live voice call to {client.business_name} ({client.answer_mode}).")
        print("Type as the caller. Ctrl+C to quit.\n")

        vapi_messages = []
        while True:
            try:
                caller_line = input("Caller: ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nCall ended.")
                break

            if not caller_line:
                continue

            vapi_messages.append(VapiMessage(role="user", content=caller_line))
            request = VapiChatRequest(
                model="claude-sonnet-4-6",
                messages=vapi_messages,
                call=VapiCall(
                    id="sim-call",
                    phoneNumber=VapiPhoneNumber(number=client.inbound_number or "+10000000000"),
                    customer=VapiCustomer(number="+15550001234"),
                ),
            )

            result = handle_voice_turn(session, agent, client, request)
            vapi_messages.append(VapiMessage(role="assistant", content=result["reply"]))

            print(f"Roster: {result['reply']}")
            if result["pending_tool_call"]:
                print(f"  [tool call] {result['pending_tool_call']}")
                break  # a transfer ends the simulated call


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it manually and verify expected behavior**

Run: `python seed.py` (ensures client id `1` — Lou's Heating & Cooling — exists)
Run: `python simulate_voice.py 1`
Type: `my AC broke, house is freezing, I think the pilot light went out`
Expected: the AI responds in character; if you then type something like `this is unacceptable, I want to speak to someone right now`, expect a `[tool call] {'name': 'transfer_call', ...}` line to print and the simulated call to end. (Requires `ANTHROPIC_API_KEY` set in `.env` — this is the live LLM path.)

- [ ] **Step 3: Commit**

```bash
git add simulate_voice.py
git commit -m "Add simulate_voice.py manual test harness"
```

---

### Task 8: Documentation — Vapi setup + local testing via ngrok

**Files:**
- Modify: `README.md` (add a new section)

**Interfaces:**
- None — documentation only.

- [ ] **Step 1: Add the section**

Add this section to `README.md`, after the existing "Real phone line (Twilio)" section:
```markdown
## AI receptionist (Vapi, live voice)

The voice receptionist reuses the same engine as the text agent — see
`docs/superpowers/specs/2026-07-01-ai-receptionist-design.md` for the full design.

### One-time setup
1. Create a Vapi account and import each client's Twilio number
   (docs.vapi.ai/phone-numbers/import-twilio).
2. Create a single Vapi assistant (shared across all clients) with:
   - Model provider: Custom LLM
   - Custom LLM URL: `<your-public-url>/voice/chat/completions`
   - A `transferCall` tool with an empty `destinations` list — the destination is
     supplied dynamically by the agent per call, not configured here.
3. Point every imported client number at this one assistant.

### Local testing (no deployment yet)
Vapi needs a public URL to reach your local server:
```bash
./run.sh                 # starts the app on :8000
ngrok http 8000           # in a second terminal; gives you a public https URL
```
Use the `ngrok` URL (plus `/voice/chat/completions`) as the assistant's Custom LLM
URL while testing. Call the imported Twilio number from your phone to test live;
confirm the job shows up in `/clients/<id>` and that a deliberately hard question
("I want to speak to a manager right now") triggers a live transfer to the
`escalation_phone` on file.

### Per-client onboarding
When adding a client for this agent: ask whether the AI should answer every call
or only unanswered ones, set `answer_mode` accordingly (`primary`/`backup`) on the
new-client form, and have them set matching call forwarding on their existing
number (forward-all vs. forward-on-no-answer) to the Twilio number you imported
into Vapi.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "Document Vapi setup and local ngrok testing for the voice receptionist"
```

---

## Self-Review Notes

- **Spec coverage:** Architecture (Task 5), Components (Tasks 2-4), Data flow (Tasks 4-6), Error handling (Task 6), Testing (Tasks 1-7), Relationship to SMS agent (documented in Global Constraints — no code change needed since the SMS webhook path is simply not used for voice-enabled clients at the Vapi/forwarding-config level, not the app level), answer_mode onboarding (Task 1 + Task 8). All spec sections have a corresponding task.
- **Placeholder scan:** none found — every step has complete code or an exact command.
- **Type consistency:** `AgentEngine.respond(...)` signature (Task 2) — `tools`, `system_prompt`, `max_iters` — matches every call site in Task 4 (`voice_adapter.handle_voice_turn`) and every test double in `conftest.py`'s `StubAgent`. `pending_tool_call` key name matches across `engine.py`, `voice_adapter.py`, `app.py`, and all tests.
