from review_engine import (
    REVIEW_DELAY_DAYS,
    REVIEW_MESSAGE_TEMPLATE,
    render_review_template,
)


def test_render_review_template_substitutes_known_variables():
    text = render_review_template(
        REVIEW_MESSAGE_TEMPLATE,
        business_name="Ridgeline Plumbing",
        review_link="https://g.page/r/test",
    )
    assert "Ridgeline Plumbing" in text
    assert "https://g.page/r/test" in text


def test_render_review_template_leaves_missing_variables_blank():
    text = render_review_template("Hi {business_name}, {review_link}", business_name="Ridgeline")
    assert text == "Hi Ridgeline, "


def test_review_delay_days_is_a_positive_integer():
    """ "Wait an appropriate amount of time" (2026-07-29 plan) — the delay
    must be a real, positive wait, not the instant send it replaces."""
    assert REVIEW_DELAY_DAYS > 0
