from roles import (
    ROSTER_DESCRIPTIONS,
    ROSTER_HIRE_ORDER,
    coming_later_after,
    next_hire,
    receptionist_display_name,
)


def test_receptionist_name_uses_trade_mapping():
    assert receptionist_display_name("HVAC") == "CSR"
    assert receptionist_display_name("hvac") == "CSR"
    assert receptionist_display_name("Plumbing") == "Receptionist"
    assert receptionist_display_name("Electrical") == "Office Manager"
    assert receptionist_display_name("Roofing") == "Office Coordinator"


def test_receptionist_name_defaults_for_unmapped_trade():
    assert receptionist_display_name("Landscaping") == "Receptionist"
    assert receptionist_display_name("") == "Receptionist"


def test_roster_order_is_chaser_then_retention():
    assert ROSTER_HIRE_ORDER == ["Quote Chaser", "Retention Manager"]
    assert set(ROSTER_DESCRIPTIONS) == set(ROSTER_HIRE_ORDER)


def test_next_hire_is_quote_chaser_when_nothing_requested():
    assert next_hire([]) == "Quote Chaser"


def test_next_hire_is_retention_manager_after_chaser_requested():
    assert next_hire(["Quote Chaser"]) == "Retention Manager"


def test_next_hire_is_none_once_full_roster_requested():
    assert next_hire(["Quote Chaser", "Retention Manager"]) is None


def test_coming_later_follows_next_hire():
    assert coming_later_after("Quote Chaser") == "Retention Manager"
    assert coming_later_after("Retention Manager") is None
    assert coming_later_after(None) is None
