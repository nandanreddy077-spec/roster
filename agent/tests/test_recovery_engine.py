from recovery_engine import SEQUENCE_DAYS, TEMPLATES, build_recovery_reply_prompt, render_template


class FakeRecoveryJob:
    def __init__(self, customer_name="Mike", service_type="AC install"):
        self.customer_name = customer_name
        self.service_type = service_type


def test_render_template_substitutes_known_variables():
    text = render_template(
        "Hi {customer_name}, about your {service_type}",
        customer_name="Mike",
        service_type="AC repair",
    )
    assert text == "Hi Mike, about your AC repair"


def test_render_template_leaves_missing_variables_blank():
    text = render_template("Hi {customer_name}, {estimate_amount}", customer_name="Mike")
    assert text == "Hi Mike, "


def test_templates_cover_every_sequence_day_for_both_faces():
    for face in ("quote", "reactivation"):
        for day in SEQUENCE_DAYS:
            assert day in TEMPLATES[face]
            assert (
                "{service_type}" in TEMPLATES[face][day]
                or "{customer_name}" in TEMPLATES[face][day]
            )


def test_prompt_without_slots_asks_for_intent_tool():
    prompt = build_recovery_reply_prompt(FakeRecoveryJob())
    assert "record_response" in prompt


def test_prompt_with_slots_asks_for_confirm_slot_tool():
    prompt = build_recovery_reply_prompt(
        FakeRecoveryJob(), offered_slots=["Monday morning", "Tuesday afternoon"]
    )
    assert "confirm_slot" in prompt
    assert "Monday morning" in prompt


from recovery_engine import FACE_DISPLAY_NAMES, MEMBERSHIP_OFFSETS


def test_face_display_names_covers_every_known_face():
    assert FACE_DISPLAY_NAMES["quote"] == "Chaser"
    assert FACE_DISPLAY_NAMES["reactivation"] == "Rebooker"
    assert FACE_DISPLAY_NAMES["membership"] == "Renewals"


def test_membership_templates_cover_every_offset():
    for offset in MEMBERSHIP_OFFSETS:
        assert offset in TEMPLATES["membership"]
        text = TEMPLATES["membership"][offset]
        assert "{service_type}" in text or "{customer_name}" in text


def test_membership_offsets_are_ascending():
    assert MEMBERSHIP_OFFSETS == sorted(MEMBERSHIP_OFFSETS)


# ---- PR #2: escalation -------------------------------------------------------

from recovery_engine import ESCALATE_TOOL


def test_escalate_tool_requires_a_reason():
    props = ESCALATE_TOOL["input_schema"]["properties"]
    assert "reason" in props
    assert props["reason"]["type"] == "string"
    assert "reason" in ESCALATE_TOOL["input_schema"]["required"]


def test_prompt_without_slots_instructs_escalation_for_negotiation_and_complaints():
    prompt = build_recovery_reply_prompt(FakeRecoveryJob()).lower()
    assert "escalate_to_owner" in prompt
    assert "negotiat" in prompt or "discount" in prompt
    assert "reschedule" in prompt
    assert "complaint" in prompt or "frustrat" in prompt
    assert "don't negotiate" in prompt or "do not negotiate" in prompt


def test_prompt_with_slots_also_instructs_escalation():
    prompt = build_recovery_reply_prompt(
        FakeRecoveryJob(), offered_slots=["Monday morning", "Tuesday afternoon"]
    ).lower()
    assert "escalate_to_owner" in prompt
