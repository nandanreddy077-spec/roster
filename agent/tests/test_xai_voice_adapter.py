import base64
import hashlib
import hmac
import json

from db_models import Client
from xai_voice_adapter import (
    _extract_transcript,
    _translate_tool,
    build_session_update,
    parse_incoming_call_webhook,
    verify_webhook_signature,
)


def make_client(session) -> Client:
    client = Client(
        business_name="Test Co",
        trade="HVAC",
        services_json=json.dumps(["AC repair"]),
        hours="9-5",
        pricing_faq="n/a",
        escalation_phone="+15550000000",
        xai_phone_number="+19995550000",
    )
    session.add(client)
    session.commit()
    session.refresh(client)
    return client


def test_translate_tool_converts_anthropic_shape_to_function_shape():
    tool = {
        "name": "log_job",
        "description": "Log a job",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    }

    translated = _translate_tool(tool)

    assert translated["type"] == "function"
    assert translated["name"] == "log_job"
    assert translated["description"] == "Log a job"
    assert translated["parameters"] == tool["input_schema"]


def test_build_session_update_includes_both_tools_and_instructions(session):
    client = make_client(session)

    update = build_session_update(client)

    assert update["type"] == "session.update"
    tool_names = {t["name"] for t in update["session"]["tools"]}
    assert tool_names == {"log_job", "transfer_call"}
    assert "Test Co" in update["session"]["instructions"]


def _sign(webhook_id: str, timestamp: str, body: bytes, secret_b64: str) -> str:
    secret_bytes = base64.b64decode(secret_b64)
    signed_content = f"{webhook_id}.{timestamp}.".encode() + body
    sig = base64.b64encode(hmac.new(secret_bytes, signed_content, hashlib.sha256).digest()).decode()
    return f"v1,{sig}"


def test_verify_webhook_signature_accepts_correct_svix_style_signature():
    secret = base64.b64encode(b"shh-secret-bytes").decode()
    body = b'{"call_id": "abc"}'
    signature = _sign("msg_1", "1730000000", body, secret)

    assert verify_webhook_signature("msg_1", "1730000000", body, signature, secret) is True


def test_verify_webhook_signature_accepts_whsec_prefixed_secret():
    raw_secret = base64.b64encode(b"shh-secret-bytes").decode()
    body = b'{"call_id": "abc"}'
    signature = _sign("msg_1", "1730000000", body, raw_secret)

    assert verify_webhook_signature("msg_1", "1730000000", body, signature, f"whsec_{raw_secret}") is True


def test_verify_webhook_signature_rejects_wrong_signature():
    secret = base64.b64encode(b"shh-secret-bytes").decode()
    body = b'{"call_id": "abc"}'

    assert verify_webhook_signature("msg_1", "1730000000", body, "v1,wrong", secret) is False


def test_verify_webhook_signature_rejects_missing_header():
    secret = base64.b64encode(b"shh-secret-bytes").decode()
    assert verify_webhook_signature(None, "1730000000", b"{}", "v1,sig", secret) is False
    assert verify_webhook_signature("msg_1", None, b"{}", "v1,sig", secret) is False
    assert verify_webhook_signature("msg_1", "1730000000", b"{}", None, secret) is False


def test_parse_incoming_call_webhook_extracts_call_id_and_numbers():
    payload = {
        "type": "realtime.call.incoming",
        "data": {
            "call_id": "call_123",
            "sip_headers": [
                {"name": "From", "value": "+14155550100"},
                {"name": "To", "value": "+18005550199"},
            ],
        },
    }

    parsed = parse_incoming_call_webhook(payload)

    assert parsed == {"call_id": "call_123", "to": "+18005550199", "from": "+14155550100"}


def test_parse_incoming_call_webhook_ignores_other_event_types():
    assert parse_incoming_call_webhook({"type": "realtime.call.ended", "data": {}}) is None


def test_parse_incoming_call_webhook_handles_missing_fields():
    assert parse_incoming_call_webhook({"type": "realtime.call.incoming", "data": {}}) is None


def test_extract_transcript_pulls_text_from_response_done_output():
    event = {
        "response": {
            "output": [
                {"content": [{"transcript": "Sure, what's the issue?"}]},
            ]
        }
    }

    assert _extract_transcript(event) == "Sure, what's the issue?"


def test_extract_transcript_handles_missing_output():
    assert _extract_transcript({"response": {}}) == ""
