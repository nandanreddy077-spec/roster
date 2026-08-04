"""Employee registry is documentation-as-code, not a live subsystem: these
tests only guard its own invariants (exactly one live entry, unique keys).
Nothing else in the app imports this module yet — see spec §4."""
from employees import REGISTRY


def test_the_live_employees_are_frontdesk_reviews_and_quote_chaser():
    """Reviews and Quote Chaser both graduated internal -> live on 2026-08-04
    (registry governance rule: "internal" is not a resting state), each on the
    same two conditions: the whole journey verified end to end against real
    Claude, and its tick worker gated on the Employee row — without which
    `live` would mean a business gets worked without hiring anyone.

    Deliberately an EXACT set, not a lower bound: graduation is a decision, so
    a new one must fail here and be argued for rather than drift in."""
    live = {e.key for e in REGISTRY if e.status == "live"}
    assert live == {"frontdesk", "reviews", "quote_chaser"}


def test_registry_keys_are_unique():
    keys = [e.key for e in REGISTRY]
    assert len(keys) == len(set(keys))


def test_retention_manager_is_still_internal_not_live():
    """Quote Chaser left this list on 2026-08-04 by earning it. Retention
    Manager has NOT been verified end to end and has no gated tick worker of
    its own, so it stays internal — the founder deploys it per business on
    request rather than it being a standing offer."""
    by_key = {e.key: e for e in REGISTRY}
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
    assert by_key["reviews"].status == "live"
    assert by_key["retention_manager"].department == "customer_success"
