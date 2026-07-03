# Voice Endpoint Authentication — Design Spec

**Date:** 2026-07-03
**Status:** Design approved, ready for implementation plan

---

## Executive Summary

`POST /voice/chat/completions` (the Custom LLM endpoint Vapi calls to drive the live
voice receptionist) currently has zero authentication — anyone who discovers the
deployed/ngrok URL can drive it directly, triggering real paid Claude API calls and
real `transfer_call` telephony actions. This has been a known, explicitly deferred gap
since the voice feature shipped (`agent/README.md`'s own "Security note (deferred — do
before going public)" section names the exact fix).

This closes it with a single shared-secret bearer token check, gated so it activates
only once the operator actually configures a secret — the endpoint stays unlocked and
behaves exactly as it does today until then.

---

## Design

### The check

One new environment variable, `VAPI_SHARED_SECRET`, read via `os.environ.get(...)` —
the same pattern already used for `TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN`
(`channels.py`) and `ANTHROPIC_API_KEY` (`engine.py`).

`voice_chat_completions` gains one check at the very top, before any Claude call,
session lookup, or business logic runs:

- If `VAPI_SHARED_SECRET` is set: the request's `Authorization: Bearer <secret>`
  header must match exactly, or the endpoint returns `401` immediately with no
  further processing (no DB session opened, no Claude call made). The comparison
  uses `hmac.compare_digest` (constant-time), not `==` — a plain string comparison
  on a secret leaks timing information about how many leading characters matched,
  which is exactly the kind of thing worth doing right the first time on a security
  check, not a hypothetical concern to defer.
- If `VAPI_SHARED_SECRET` is not set: the check is skipped entirely — the endpoint
  behaves exactly as it does today. This matches the codebase's existing convention
  for optional credentials (Twilio creds missing → falls back to console-print,
  rather than erroring) and means nothing breaks for local dev or before the
  operator has configured Vapi's header.

### Single shared secret, not per-client

Vapi's own setup (documented in `agent/README.md`) uses **one shared Vapi assistant
across every client** — client identity is resolved *after* this check, from the
phone number in the call payload, not from the auth mechanism. A single shared secret
is therefore the only sensible design; there is no per-client secret to manage.

### Configuration

`agent/README.md`'s existing Vapi setup steps gain one line: configure a custom
header (`Authorization: Bearer <secret>`) on the assistant's Custom LLM connection, so
Vapi sends it on every call. The "Security note (deferred...)" section is updated to
reflect that this is now implemented, with the one remaining operator action being to
actually set `VAPI_SHARED_SECRET` (which the user has stated they'll do on their own
timeline, separately from this code shipping).

---

## Testing

- The 4 existing voice endpoint tests need no changes — none of them set
  `VAPI_SHARED_SECRET`, so auth stays skipped for them, proving the
  skip-when-unconfigured path stays intact.
- New test: with `VAPI_SHARED_SECRET` set, a request with no `Authorization` header
  is rejected with `401`.
- New test: with `VAPI_SHARED_SECRET` set, a request with the wrong bearer value is
  rejected with `401`.
- New test: with `VAPI_SHARED_SECRET` set, a request with the correct
  `Authorization: Bearer <secret>` header succeeds exactly as the existing tests
  expect (reuses the same happy-path assertions, just with the header present).

---

## Constraints & Scope

### In Scope
- The single auth check on `/voice/chat/completions`, fail-closed when configured,
  fail-open when not.
- README documentation of the new env var and the Vapi-side header configuration.

### Out of Scope
- Per-client secrets or any rotation/revocation mechanism — not asked for, and the
  single-shared-assistant architecture doesn't call for it.
- Authentication on any other endpoint (`/webhook/sms`, `/webhook/voice-status`) —
  those are Twilio webhooks with a different trust model (Twilio's request signing is
  a separate, unrelated concern) and were not part of this gap.

---

**Design approved by:** User
**Ready for:** Implementation planning
