"""The department registry is the customer-facing unit of the product
(blueprint §1). These tests guard its own invariants and — critically — its
join with the employee registry: the two are linked by a bare string, and if
they drift, employees silently disappear from every department view."""
from departments import REGISTRY, Department, department_for_role
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


# --- department_for_role -----------------------------------------------------


def test_department_for_role_resolves_a_registry_key():
    assert department_for_role("frontdesk").key == "customer_service"
    assert department_for_role("quote_chaser").key == "sales"
    assert department_for_role("collections").key == "finance"


def test_department_for_role_resolves_the_legacy_retention_key():
    """R1: roles.ROLE_KEYS maps "Retention Manager" -> "retention", but the
    employee registry's key is "retention_manager". That spelling is already
    on real Employee rows (written by portal.py:53 and db.py:203), so without
    this alias those employees resolve to no department at all and vanish
    from every department view in Phases 4 and 5."""
    assert department_for_role("retention").key == "customer_success"


def test_reviews_resolves_natively_to_customer_service_without_an_alias():
    """The other half of R1 is fixed as a registry entry, not an alias (Task
    1): Reviews is a real Customer Service employee, so "reviews" resolves
    through the normal path. Asserting the department here — not just that it
    resolves — is what guards the founder's 2026-07-28 decision from being
    quietly undone by a future edit to the alias map."""
    assert department_for_role("reviews").key == "customer_service"


def test_department_for_role_returns_none_for_an_unknown_key():
    """R2: roles.role_key_for() slugifies arbitrary text as a fallback, and
    app.py's deploy route appends raw strings, so unknown keys are reachable
    from real data. Return None — never raise, or Phase 4/5 can't render a
    business that has one."""
    assert department_for_role("something_nobody_registered") is None


def test_department_for_role_handles_empty_input():
    assert department_for_role("") is None


def test_every_role_key_roles_py_can_emit_resolves_to_a_department():
    """The regression guard for R1: if anyone adds a mapping to
    roles.ROLE_KEYS without a matching employee or alias, this fails here
    instead of silently hiding an employee in production."""
    from roles import ROLE_KEYS

    for role_key in sorted(set(ROLE_KEYS.values())):
        assert department_for_role(role_key) is not None, (
            f"roles.ROLE_KEYS can emit {role_key!r}, which resolves to no department"
        )
