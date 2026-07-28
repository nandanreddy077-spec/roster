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


def test_department_tags_are_the_canonical_seven():
    """Department tags are the join key between employees.py and
    departments.py. 'leadership' is canonical — it's what the live /roster
    page and the approved product blueprint both use; 'intelligence' was an
    internal-only name that never appeared anywhere customer-facing."""
    expected = {
        "customer_service", "sales", "operations",
        "finance", "customer_success", "marketing", "leadership",
    }
    assert {e.department for e in REGISTRY} == expected


def test_reviews_is_registered_under_customer_service():
    """roles.ROLE_KEYS already emits "reviews" as an Employee.role_key, so
    the employee exists in data — it was just missing from the registry.
    Founder decision 2026-07-28: Reviews belongs to Customer Service (the
    department that owns the outcome), while Retention Manager stays in
    Customer Success. One engine may serve both; the department model
    follows the product architecture, not the implementation."""
    by_key = {e.key: e for e in REGISTRY}
    assert by_key["reviews"].department == "customer_service"
    assert by_key["reviews"].status == "internal"
    assert by_key["retention_manager"].department == "customer_success"
