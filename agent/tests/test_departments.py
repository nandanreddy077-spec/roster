"""The department registry is the customer-facing unit of the product
(blueprint §1). These tests guard its own invariants and — critically — its
join with the employee registry: the two are linked by a bare string, and if
they drift, employees silently disappear from every department view."""
from departments import REGISTRY, Department
from employees import REGISTRY as EMPLOYEE_REGISTRY

CANONICAL_ORDER = [
    "customer_service", "sales", "operations",
    "finance", "customer_success", "marketing", "leadership",
]


def test_registry_holds_the_seven_departments_in_display_order():
    assert [d.key for d in REGISTRY] == CANONICAL_ORDER


def test_department_keys_are_unique():
    keys = [d.key for d in REGISTRY]
    assert len(keys) == len(set(keys))


def test_leadership_is_the_only_department_that_is_not_hireable():
    """Blueprint: Leadership is included automatically with any active
    department, never sold or hired separately."""
    assert [d.key for d in REGISTRY if not d.hireable] == ["leadership"]


def test_every_department_has_complete_customer_facing_copy():
    """Phase 5 renders these fields directly onto inactive department cards
    (blueprint §7). A blank field is a visible content bug, not a cosmetic
    one, so an empty string fails here rather than in a template."""
    for d in REGISTRY:
        for field in ("display_name", "mission", "problem", "outcome", "why_adopt"):
            assert getattr(d, field).strip(), f"{d.key}.{field} is empty"


def test_every_employee_department_tag_resolves_to_a_real_department():
    """R3: the registries are joined by a bare string. Fail here, not in a
    view that silently renders one department short."""
    department_keys = {d.key for d in REGISTRY}
    for e in EMPLOYEE_REGISTRY:
        assert e.department in department_keys, (
            f"employee {e.key!r} is tagged with unknown department {e.department!r}"
        )


def test_every_hireable_department_has_at_least_one_employee_behind_it():
    """A hireable department with nobody in it would be sellable and
    unstaffable — the one inconsistency this registry must never ship."""
    tagged = {e.department for e in EMPLOYEE_REGISTRY}
    for d in REGISTRY:
        if d.hireable:
            assert d.key in tagged, f"{d.key} is hireable but has no employees"
