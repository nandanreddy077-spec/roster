from referral_engine import (
    RECORD_REFERRAL_TOOL,
    REFERRAL_DELAY_DAYS,
    REFERRAL_MESSAGE_TEMPLATE,
    REFERRAL_REPLY_WINDOW_DAYS,
    build_referral_reply_prompt,
    render_referral_template,
)


def test_render_referral_template_substitutes_known_variables():
    text = render_referral_template(
        REFERRAL_MESSAGE_TEMPLATE,
        customer_name="Mike", service_type="AC repair", incentive="$25 off your next service",
    )
    assert "Mike" in text
    assert "AC repair" in text
    assert "$25 off your next service" in text


def test_render_referral_template_leaves_missing_variables_blank():
    text = render_referral_template("Hi {customer_name}, {incentive}", customer_name="Mike")
    assert text == "Hi Mike, "


def test_referral_delay_and_window_are_positive_integers():
    assert REFERRAL_DELAY_DAYS > 0
    assert REFERRAL_REPLY_WINDOW_DAYS > 0


def test_record_referral_tool_has_no_required_fields():
    assert RECORD_REFERRAL_TOOL["input_schema"].get("required", []) == []
    assert "referred_name" in RECORD_REFERRAL_TOOL["input_schema"]["properties"]
    assert "referred_phone" in RECORD_REFERRAL_TOOL["input_schema"]["properties"]


def test_build_referral_reply_prompt_mentions_record_referral():
    prompt = build_referral_reply_prompt()
    assert "record_referral" in prompt
