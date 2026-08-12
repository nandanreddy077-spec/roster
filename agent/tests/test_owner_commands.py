"""Parsing what an owner texts back.

The dangerous failure here is not a missed command — it is a MISREAD one.
Anything this module calls a "proposed window" gets texted verbatim to a real
customer, so text that isn't a time must never reach that path.
"""

import pytest
from owner_commands import CONFIRM, PROPOSE, REJECT, UNKNOWN, parse_owner_command


@pytest.mark.parametrize(
    "text,job_id",
    [
        ("Y412", 412),
        ("y412", 412),
        ("Y 412", 412),
        ("Y #412", 412),
        ("yes 412", 412),
        ("confirm 412", 412),
        ("Y", None),
        ("yes", None),
        ("ok", None),
    ],
)
def test_confirmations_are_recognised(text, job_id):
    command = parse_owner_command(text)
    assert command.action == CONFIRM
    assert command.job_id == job_id


@pytest.mark.parametrize(
    "text,job_id",
    [
        ("N412", 412),
        ("n 412", 412),
        ("no 412", 412),
        ("decline 412", 412),
        ("N", None),
        ("no", None),
    ],
)
def test_rejections_are_recognised(text, job_id):
    command = parse_owner_command(text)
    assert command.action == REJECT
    assert command.job_id == job_id


@pytest.mark.parametrize(
    "text,job_id,window",
    [
        ("412 Friday 8am-12pm", 412, "Friday 8am-12pm"),
        ("#412 Friday 8am-12pm", 412, "Friday 8am-12pm"),
        ("Friday 8am-12pm", None, "Friday 8am-12pm"),
        ("tomorrow morning", None, "tomorrow morning"),
        ("Thursday afternoon instead", None, "Thursday afternoon instead"),
    ],
)
def test_arrival_windows_are_recognised(text, job_id, window):
    command = parse_owner_command(text)
    assert command.action == PROPOSE
    assert command.job_id == job_id
    assert command.window == window


@pytest.mark.parametrize(
    "text",
    [
        "no thanks",
        "not this week",
        "call me",
        "who is this",
        "sorry what",
        "",
        "   ",
    ],
)
def test_text_that_is_not_a_time_is_never_treated_as_a_window(text):
    """The bug this guards: an owner replying "no thanks" had that phrase
    proposed to their customer as an arrival window. Anything we can't read as
    a time must fall through to the help text instead of being broadcast."""
    assert parse_owner_command(text).action == UNKNOWN
