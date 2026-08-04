"""Parsers pinned against payloads captured from a LIVE xAI realtime session.

Everything here is verbatim from a real session on 2026-08-04, not a guess at
the shape. That distinction matters: the previous fixtures encoded the same
assumption as the code they tested, so both were wrong together and the suite
stayed green. Only real payloads can catch that class of error.

What a live session proves: the assistant transcript path and token usage.
What it still cannot prove — because it carries no inbound caller audio — is
the CALLER transcript event shape (see USER_TRANSCRIPT_COMPLETED). That
remains unconfirmed until a real phone call is captured.
"""
from xai_voice_adapter import _extract_response_tokens, _extract_transcript

# Verbatim response.done from a live xAI realtime session, 2026-08-04.
LIVE_RESPONSE_DONE = {
    "type": "response.done",
    "event_id": "ee2d493c-49e1-4ada-9324-1095dfc0b371",
    "response": {
        "id": "c05db86f-9cb2-404d-99ed-1888c7f017ea",
        "object": "realtime.response",
        "output": [{
            "id": "9e271007-a1f2-453b-a677-658e80bc1665",
            "object": "realtime.item",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{
                "type": "audio",
                "transcript": "Hello, how may I assist you today? I'm here to help with any inquiries or requests.",
            }],
        }],
        "status": "completed",
        "status_details": "unimplemented",
        "usage": {},          # <- empty; the real numbers are top-level
    },
    "response_id": "c05db86f-9cb2-404d-99ed-1888c7f017ea",
    "usage": {
        "input_tokens": 10,
        "input_token_details": {"text_tokens": 10, "audio_tokens": 0, "grok_tokens": 0},
        "output_tokens": 277,
        "output_token_details": {"text_tokens": 19, "audio_tokens": 258, "grok_tokens": 0},
        "total_tokens": 287,
        "output_audio_seconds": 5.134,
        "billable_audio_seconds": 3,
    },
    "previous_item_id": None,
}


def test_the_assistant_transcript_is_read_from_the_real_payload():
    assert _extract_transcript(LIVE_RESPONSE_DONE) == (
        "Hello, how may I assist you today? I'm here to help with any inquiries or requests."
    )


def test_token_usage_is_read_from_the_real_payload():
    """THE regression. usage lives at the top level; response.usage is {}.
    Reading only the nested one returned 0 every turn, so the per-call token
    budget never incremented and MAX_CALL_TOKEN_BUDGET could never fire."""
    assert _extract_response_tokens(LIVE_RESPONSE_DONE) == 287


def test_a_nested_usage_field_still_works_if_xai_ever_populates_it():
    """The originally-assumed location stays supported, so a future version
    that fills it in doesn't silently go back to counting zero."""
    event = {"response": {"usage": {"total_tokens": 42}}}
    assert _extract_response_tokens(event) == 42


def test_an_empty_nested_usage_does_not_mask_real_top_level_usage():
    event = {"response": {"usage": {}}, "usage": {"input_tokens": 5, "output_tokens": 7}}
    assert _extract_response_tokens(event) == 12


def test_missing_usage_anywhere_is_zero_not_a_crash():
    """A usage-reporting hiccup must never break the call the budget protects."""
    assert _extract_response_tokens({"type": "response.done", "response": {}}) == 0
    assert _extract_response_tokens({}) == 0
    assert _extract_response_tokens({"usage": None, "response": None}) == 0
    assert _extract_response_tokens({"usage": {"total_tokens": "many"}}) == 0


def test_the_budget_actually_accumulates_across_real_turns():
    """Three live-shaped turns must exceed a 500-token budget. Under the old
    parser this summed to 0 and the cap never fired."""
    spent = sum(_extract_response_tokens(LIVE_RESPONSE_DONE) for _ in range(3))
    assert spent == 861
    assert spent > 500
