from recovery_engine import SEQUENCE_DAYS, TEMPLATES, build_recovery_reply_prompt, render_template


class FakeRecoveryJob:
    def __init__(self, customer_name="Mike", service_type="AC install"):
        self.customer_name = customer_name
        self.service_type = service_type


def test_render_template_substitutes_known_variables():
    text = render_template(
        "Hi {customer_name}, about your {service_type}", customer_name="Mike", service_type="AC repair"
    )
    assert text == "Hi Mike, about your AC repair"


def test_render_template_leaves_missing_variables_blank():
    text = render_template("Hi {customer_name}, {estimate_amount}", customer_name="Mike")
    assert text == "Hi Mike, "


def test_templates_cover_every_sequence_day_for_both_faces():
    for face in ("quote", "reactivation"):
        for day in SEQUENCE_DAYS:
            assert day in TEMPLATES[face]
            assert "{service_type}" in TEMPLATES[face][day] or "{customer_name}" in TEMPLATES[face][day]


def test_prompt_without_slots_asks_for_intent_tool():
    prompt = build_recovery_reply_prompt(FakeRecoveryJob())
    assert "record_response" in prompt


def test_prompt_with_slots_asks_for_confirm_slot_tool():
    prompt = build_recovery_reply_prompt(FakeRecoveryJob(), offered_slots=["Monday morning", "Tuesday afternoon"])
    assert "confirm_slot" in prompt
    assert "Monday morning" in prompt
