"""Employee registry is documentation-as-code, not a live subsystem: these
tests only guard its own invariants (exactly one live entry, unique keys).
Nothing else in the app imports this module yet — see spec §4."""
from employees import REGISTRY


def test_exactly_one_live_employee_and_it_is_frontdesk():
    live = [e for e in REGISTRY if e.status == "live"]
    assert len(live) == 1
    assert live[0].key == "frontdesk"


def test_registry_keys_are_unique():
    keys = [e.key for e in REGISTRY]
    assert len(keys) == len(set(keys))


def test_quote_chaser_and_retention_manager_are_internal_not_live():
    by_key = {e.key: e for e in REGISTRY}
    assert by_key["quote_chaser"].status == "internal"
    assert by_key["retention_manager"].status == "internal"
