# AI Receptionist (Vapi voice agent) — Design

## Context

Roster's first agent (Frontdesk) handles missed calls via SMS text-back: `engine.py`
runs a Claude-powered Think→Act→Observe loop that answers customer questions and
calls `log_job` to capture the lead. This spec adds a second agent — a live voice
receptionist — that answers the phone in real time instead of texting back after a
miss. Home-services business owners have no in-house tech team, so onboarding a new
client (adding their number and business details) is done for them, not self-serve.

This is the first of several planned agents (quote follow-up, reviews, reactivation,
etc.). The goal is to establish the pattern once — one Claude "brain" reused across
channels, config-driven per client — so each future agent is a new prompt + tool, not
a new system.

## Goal

A caller can call a client business's number, have a real, natural-sounding voice
conversation with the AI, get their questions answered, have the job captured
(same `log_job` path as SMS), and — if the AI can't handle something — get
transferred live to the business owner's cell.

## Relationship to the existing SMS text-back agent

Both agents trigger on the same event — a missed/unanswered call (forward-on-no-answer).
A live voice pickup is strictly better for the customer than a text, so **the voice
receptionist replaces the text-back agent for missed calls** on any client that has
this agent enabled. The text-back agent isn't removed from the codebase — it still
handles customers who text the business number directly — but the call-status
webhook path (`/webhook/voice-status`) that currently triggers an SMS on a miss
should no longer fire for clients who have the voice receptionist active, since
Vapi now answers those calls before they ever go missed.

## Non-goals

- No self-serve onboarding UI (owner details/number are entered by Roster, not the
  client).
- No building of a custom voice pipeline (STT/TTS/telephony) — using Vapi.
- No other new agents in this spec (quote follow-up, reviews, etc. are separate,
  future specs using this same pattern).

## Architecture

Twilio keeps ownership of the phone number. The number is imported into Vapi, which
becomes the live voice layer (answering, speech-to-text, text-to-speech, call
transfer). Vapi is configured with a **Custom LLM** model provider pointing at a new
endpoint on the existing FastAPI app (`app.py`). That endpoint is a thin adapter: it
resolves which `Client` owns the call, forwards the conversation to the existing
`AgentEngine.respond()`, and returns the reply (and any tool calls) in the
OpenAI-compatible streaming format Vapi expects.

```
Customer calls business number
        │
     Twilio (number ownership, unchanged)
        │
      Vapi (answers, STT, TTS, call transfer)
        │  POST /voice/chat/completions (OpenAI-compatible, streamed SSE)
   New adapter endpoint in app.py
        │
   AgentEngine.respond()  (existing engine.py, voice-aware system prompt)
        │
   log_job / transfer_call tool calls
        │
   Same Client/Job DB → same dashboard
```

No changes to `db_models.py`, `service.py`, or the dashboard — a voice call is just
another conversation thread landing in the same `Client`/`Job` tables the SMS agent
already uses.

## Components

- **`Client` config**: add one new field, `answer_mode` (`"primary"` or
  `"backup"`), set during onboarding based on how the client configured their call
  forwarding (forward-all vs forward-on-no-answer). Asked as a setup question, not
  inferred.
- **`engine.py`**: add `TRANSFER_CALL_TOOL` schema alongside the existing
  `LOG_JOB_TOOL`, and a voice-specific variant of `build_system_prompt` (same
  business facts as the SMS prompt, phrased for speech — short sentences, no
  "text-message length" framing, explicit instructions on when to call
  `transfer_call` — e.g. an angry customer, a complaint, or a request the AI can't
  confidently answer). The prompt's greeting/framing branches on `answer_mode`:
  a normal greeting when `"primary"` ("Thanks for calling..."), a
  missed-call-acknowledging greeting when `"backup"` ("Sorry we missed your
  call...").
- **New adapter module** (e.g. `voice_adapter.py`), wired into `app.py` as
  `POST /voice/chat/completions`: receives Vapi's request (see schema below),
  resolves the `Client` via `phoneNumber.number` matching the existing
  `inbound_number` field, calls `AgentEngine.respond()`, and streams the response
  back as OpenAI-format SSE chunks.
- **Vapi assistant config** (external, not code): a single shared Vapi assistant
  reused across every client, with Custom LLM URL pointing at the deployed adapter
  endpoint. The `transferCall` tool is configured with an empty static destination
  list; Claude supplies the destination number dynamically per call (see below), so
  no per-client Vapi config is needed.

### Vapi Custom LLM request shape (confirmed from Vapi's reference implementation)

Vapi POSTs a body containing: `model`, `messages` (conversation so far), `tools`,
`stream`, `max_tokens`, and a `call` object that includes `phoneNumber.number` (the
business number that was called) and `customer.number` (the caller's number, used as
`callback_number` if `log_job` is called). The endpoint must stream its reply back as
`text/event-stream` SSE chunks, ending with `data: [DONE]`.

## Data flow

1. Customer calls the business's Twilio number (imported into Vapi).
2. Vapi answers, transcribes speech, and POSTs to `/voice/chat/completions` with the
   conversation history plus `phoneNumber.number` and `customer.number`.
3. The adapter looks up the matching `Client` by `inbound_number`, calls
   `AgentEngine.respond()` with the voice system prompt, `LOG_JOB_TOOL`, and
   `TRANSFER_CALL_TOOL`.
4. If Claude calls `log_job`, it's persisted exactly like the SMS flow — visible on
   the dashboard immediately.
5. If Claude calls `transfer_call`, it supplies the owner's cell number directly as
   the `destination` argument (known from the resolved `Client`'s config); Vapi
   executes the actual transfer, the adapter itself never dials anything, it just
   returns the tool call.
6. The adapter streams the reply back in OpenAI SSE format; Vapi converts it to
   speech for the caller.

## Error handling

- If `AgentEngine.respond()` raises (Claude API error/timeout) or no `Client` matches
  the called number, the adapter returns a short fallback line plus a forced
  `transfer_call` — failures always degrade to a human handoff, never dead air or a
  crash.
- The tool loop is bounded tighter for voice than SMS (2 iterations instead of the
  SMS agent's 4), since every round-trip adds latency the caller feels live on the
  call.

## Testing

- `simulate_voice.py`: extends the existing `simulate.py` pattern — POSTs
  Vapi-shaped payloads (matching the schema above) at the new endpoint without a
  live call, asserting on replies and tool calls (including a scripted case that
  should trigger `transfer_call`).
- One real end-to-end test: import a Twilio number into Vapi pointed at the deployed
  adapter, call it live, confirm the job lands in the dashboard, and confirm a
  deliberately hard question triggers a live transfer.

## Open questions / follow-ups (not blocking this spec)

- Deployment target for `app.py` so Vapi can reach it publicly (currently runs
  local-only via `run.sh`) — needed before any live test, tracked as a prerequisite
  in the implementation plan, not part of this design.

## Confirmed decisions (resolved during design)

- **One shared Vapi assistant serves every client** — no per-client Vapi
  configuration needed. Vapi's `transferCall` tool supports a dynamic destination:
  the LLM supplies the actual number as a tool argument at call time instead of it
  being fixed in the assistant config. Since the adapter already resolves the
  `Client` (and therefore the owner's cell number) before calling
  `AgentEngine.respond()`, that number is already a known fact in the system prompt,
  and Claude passes it directly when it calls `transfer_call`. Onboarding a new
  client for this agent is therefore: (1) add their `Client` record, asking them
  whether they want the AI to pick up every call or only unanswered ones and
  setting `answer_mode` accordingly, (2) have them set up matching call forwarding
  on their existing number (forward-all or forward-on-no-answer), (3) import their
  Twilio number into Vapi pointed at the one existing shared assistant.
