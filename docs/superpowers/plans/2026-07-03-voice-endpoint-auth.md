# Voice Endpoint Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the known, documented security gap on `POST /voice/chat/completions` by requiring a shared-secret bearer token, checked in constant time, once an operator configures `VAPI_SHARED_SECRET` — and leave the endpoint's current unauthenticated behavior fully intact when that variable is unset.

**Architecture:** One check inserted at the very top of the existing `voice_chat_completions` route, before any DB session or Claude call. `os.environ.get("VAPI_SHARED_SECRET")` follows this codebase's existing pattern for optional credentials (`ANTHROPIC_API_KEY`, `TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN`); `hmac.compare_digest` does the actual comparison to avoid leaking timing information about a partial match.

**Tech Stack:** Same as the rest of `agent/` — FastAPI, pytest, no new dependencies (`hmac` and `os` are Python stdlib).

## Global Constraints

- Single shared secret, not per-client — Vapi's own setup uses one shared assistant across every client; client identity is resolved after this check, from the phone number in the call payload.
- Fail closed once `VAPI_SHARED_SECRET` is set: a missing or mismatched `Authorization: Bearer <secret>` header must return `401` immediately, with no DB session opened and no Claude call made.
- Fail open when `VAPI_SHARED_SECRET` is unset: the endpoint must behave exactly as it does today — this is what keeps the 4 existing voice tests passing unmodified.
- Use `hmac.compare_digest` for the comparison, not `==` — a plain string comparison leaks timing information about how many leading characters matched.
- No authentication added to any other endpoint (`/webhook/sms`, `/webhook/voice-status`) — those are Twilio webhooks with a separate, unrelated trust model and were explicitly out of scope for this gap.
- The `.env`/`.env.example` changes and the actual `VAPI_SHARED_SECRET` value are already done — this plan does not touch either file.

---

### Task 1: Authenticate `/voice/chat/completions`

**Files:**
- Modify: `agent/app.py`
- Modify: `agent/README.md`
- Test: `agent/tests/test_voice_endpoint.py`

**Interfaces:**
- Modifies: `voice_chat_completions(payload: VapiChatRequest)` → `voice_chat_completions(payload: VapiChatRequest, request: Request)` — the route gains a `Request` parameter to read the `Authorization` header. `Request` is already imported in `agent/app.py`.

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_voice_endpoint.py`:
```python
def _voice_payload(call_id: str) -> dict:
    return {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "hello"}],
        "call": {
            "id": call_id,
            "phoneNumber": {"number": "+10000000000"},
            "customer": {"number": "+15551234567"},
        },
    }


def test_voice_endpoint_rejects_missing_header_when_secret_configured(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setenv("VAPI_SHARED_SECRET", "test-secret-value")

    test_client = TestClient(app_module.app)
    response = test_client.post("/voice/chat/completions", json=_voice_payload("call_auth_1"))

    assert response.status_code == 401


def test_voice_endpoint_rejects_wrong_header_when_secret_configured(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setenv("VAPI_SHARED_SECRET", "test-secret-value")

    test_client = TestClient(app_module.app)
    response = test_client.post(
        "/voice/chat/completions",
        json=_voice_payload("call_auth_2"),
        headers={"Authorization": "Bearer wrong-value"},
    )

    assert response.status_code == 401


def test_voice_endpoint_accepts_correct_header_when_secret_configured(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setenv("VAPI_SHARED_SECRET", "test-secret-value")

    test_client = TestClient(app_module.app)
    response = test_client.post(
        "/voice/chat/completions",
        json=_voice_payload("call_auth_3"),
        headers={"Authorization": "Bearer test-secret-value"},
    )

    assert response.status_code == 200
    assert "isn't set up yet" in response.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent && .venv/bin/python -m pytest tests/test_voice_endpoint.py -v`
Expected: FAIL — the two `401` tests currently get `200` (no auth check exists yet), and the "correct header" test would also pass by coincidence today but for the wrong reason (no check exists) rather than because the header was validated. All three new tests should be run and confirmed to fail or be meaningless before the fix, per Step 2's purpose — specifically, the two `401`-expecting tests will show `assert 200 == 401` failures.

- [ ] **Step 3: Add the auth check to `agent/app.py`**

Add `hmac` and `os` to the top of the import block (alongside the existing `import json`, `import time`, `import uuid`):
```python
import hmac
import json
import os
import time
import uuid
```

Replace the `voice_chat_completions` route:
```python
@app.post("/voice/chat/completions")
async def voice_chat_completions(payload: VapiChatRequest, request: Request):
    shared_secret = os.environ.get("VAPI_SHARED_SECRET")
    if shared_secret:
        expected = f"Bearer {shared_secret}"
        provided = request.headers.get("authorization", "")
        if not hmac.compare_digest(provided, expected):
            return Response(status_code=401)

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

Run: `cd agent && .venv/bin/python -m pytest tests/test_voice_endpoint.py -v`
Expected: PASS (7 tests total in this file — the original 4 plus the 3 new ones)

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `cd agent && .venv/bin/python -m pytest -v`
Expected: PASS — every test in the whole suite green, including the original 4 voice tests unmodified (none of them set `VAPI_SHARED_SECRET`, so the auth check stays a no-op for them, proving the fail-open path is intact)

- [ ] **Step 6: Update `agent/README.md`**

In the `### One-time setup` numbered list, replace step 2's bullet list to add the header line:
```markdown
2. Create a single Vapi assistant (shared across all clients) with:
   - Model provider: Custom LLM
   - Custom LLM URL: `<your-public-url>/voice/chat/completions`
   - A custom header: `Authorization: Bearer <VAPI_SHARED_SECRET value>` — required
     once you've set `VAPI_SHARED_SECRET`; the endpoint accepts any request until
     you do, so this step is what actually locks it down.
   - A `transferCall` tool with an empty `destinations` list — the destination is
     supplied dynamically by the agent per call, not configured here.
```

Replace the `### Security note (deferred — do before going public)` section:
```markdown
### Authentication

`/voice/chat/completions` requires a shared-secret bearer token once
`VAPI_SHARED_SECRET` is set in `.env` — a request with a missing or wrong
`Authorization: Bearer <secret>` header gets `401` immediately, before any Claude
call or DB lookup. **Until `VAPI_SHARED_SECRET` is set, the endpoint accepts any
request** — this is the one operator action that actually closes the gap; do it
before pointing a real client's number at this in production.
```

- [ ] **Step 7: Commit**

```bash
git add agent/app.py agent/README.md agent/tests/test_voice_endpoint.py
git commit -m "Authenticate /voice/chat/completions with a shared-secret bearer token"
```

---

## Manual smoke test (after the task)

1. Confirm `VAPI_SHARED_SECRET` is set in `agent/.env` (already done separately from this plan).
2. `cd agent && ./run.sh`
3. `curl -X POST http://localhost:8000/voice/chat/completions -H "Content-Type: application/json" -d '{"model":"x","messages":[],"call":{"id":"smoke1"}}'` — confirm this returns `401` (no `Authorization` header sent).
4. Repeat with `-H "Authorization: Bearer <the value from .env>"` — confirm it now returns a normal streaming response instead of `401`.
