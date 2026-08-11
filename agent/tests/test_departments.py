"""The department registry is the customer-facing unit of the product
(blueprint §1). These tests guard its own invariants and — critically — its
join with the employee registry: the two are linked by a bare string, and if
they drift, employees silently disappear from every department view."""

from departments import (
    REGISTRY,
    Department,
    active_departments_for,
    department_for_role,
    hireable_departments,
)
from employees import REGISTRY as EMPLOYEE_REGISTRY

CANONICAL_ORDER = [
    "customer_service",
    "sales",
    "operations",
    "finance",
    "customer_success",
    "marketing",
    "leadership",
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


# --- active_departments_for / hireable_departments ---------------------------


class FakeEmployee:
    """departments.py duck-types on .role_key/.status so it never imports
    db_models — which also means these tests need no database fixture."""

    def __init__(self, role_key, status="active"):
        self.role_key = role_key
        self.status = status


def test_active_departments_for_returns_the_departments_that_are_staffed():
    staffed = active_departments_for(
        [
            FakeEmployee("frontdesk"),
            FakeEmployee("quote_chaser"),
        ]
    )
    assert [d.key for d in staffed] == ["customer_service", "sales"]


def test_active_departments_for_returns_display_order_not_input_order():
    """The dashboard renders these in a fixed, learnable order — it must not
    depend on the order rows came back from the database."""
    staffed = active_departments_for(
        [
            FakeEmployee("quote_chaser"),  # sales
            FakeEmployee("frontdesk"),  # customer_service
        ]
    )
    assert [d.key for d in staffed] == ["customer_service", "sales"]


def test_active_departments_for_deduplicates_two_employees_in_one_department():
    staffed = active_departments_for(
        [
            FakeEmployee("quote_chaser"),
            FakeEmployee("lead_qualifier"),
        ]
    )
    assert [d.key for d in staffed] == ["sales"]


def test_a_fired_employee_does_not_keep_a_department_staffed():
    assert active_departments_for([FakeEmployee("frontdesk", status="fired")]) == []


def test_a_paused_employee_keeps_its_department_staffed():
    """Paused is 'muted', not 'gone' — the department is still on the roster
    and must still appear on the dashboard."""
    staffed = active_departments_for([FakeEmployee("frontdesk", status="paused")])
    assert [d.key for d in staffed] == ["customer_service"]


def test_an_unknown_role_key_is_skipped_rather_than_crashing():
    """R2 again, at the aggregate level: a business carrying one unrecognized
    role_key must still render its other departments."""
    staffed = active_departments_for(
        [
            FakeEmployee("mystery_role"),
            FakeEmployee("frontdesk"),
        ]
    )
    assert [d.key for d in staffed] == ["customer_service"]


def test_active_departments_for_handles_an_empty_roster():
    assert active_departments_for([]) == []


def test_hireable_departments_is_the_six_sellable_ones():
    keys = [d.key for d in hireable_departments()]
    assert "leadership" not in keys
    assert len(keys) == 6


# --- department_status_for: the ONE computation both surfaces share -----------

from departments import DepartmentStatus, department_status_for


def _status_by_key(employees):
    return {s.department.key: s for s in department_status_for(employees)}


def test_department_status_covers_every_department_in_display_order():
    assert [s.department.key for s in department_status_for([])] == CANONICAL_ORDER


def test_a_department_with_nothing_deployed_is_empty():
    s = _status_by_key([])["customer_service"]
    assert s.state == "empty"
    assert s.deployed_count == 0
    assert s.deployable_count == 2  # frontdesk + reviews
    assert s.staffed == []


def test_a_fully_deployed_department_is_staffed():
    s = _status_by_key(
        [
            FakeEmployee("frontdesk"),
            FakeEmployee("reviews"),
        ]
    )["customer_service"]
    assert s.state == "staffed"
    assert s.deployed_count == 2
    assert s.deployable_count == 2
    assert {e.key for e in s.staffed} == {"frontdesk", "reviews"}


def test_a_half_deployed_department_is_partial():
    s = _status_by_key([FakeEmployee("frontdesk")])["customer_service"]
    assert s.state == "partial"
    assert s.deployed_count == 1
    assert s.deployable_count == 2


def test_a_department_with_no_deployable_employees_is_unavailable():
    """Finance/Marketing are hireable but every role in them is still
    `planned` — there is nothing to deploy yet. Operations used to be a
    third example here too, until Dispatcher (2026-07-30, Critical Finding
    #2 fix) gave it a deployable employee."""
    s = _status_by_key([])["finance"]
    assert s.state == "unavailable"
    assert s.deployable_count == 0


def test_a_fired_employee_does_not_count_as_staffed():
    s = _status_by_key([FakeEmployee("frontdesk", status="fired")])["customer_service"]
    assert s.state == "empty"
    assert s.deployed_count == 0


def test_the_legacy_retention_key_counts_toward_its_department():
    """Real rows carry "retention"; the registry says "retention_manager".
    Without normalizing, a deployed Retention Manager would read as unstaffed."""
    s = _status_by_key([FakeEmployee("retention")])["customer_success"]
    assert s.state == "staffed"
    assert s.deployed_count == 1


def test_department_status_carries_no_presentation_wording():
    """THE guard on the founder's architectural rule (2026-07-29): facts only.

    Both surfaces derive their own labels from these facts — the founder says
    'Not staffed', the customer says 'Not yet part of your workforce'. A
    wording field here would leak one audience's voice onto the other's screen
    the first time either changed."""
    assert set(DepartmentStatus.__dataclass_fields__) == {
        "department",
        "deployable",
        "staffed",
        "deployed_count",
        "deployable_count",
        "state",
    }


def test_app_does_not_define_its_own_department_state():
    """C3: one implementation. If the founder console grows a private copy,
    the two surfaces can drift — which is the whole failure this move
    prevents."""
    import app

    assert not hasattr(app, "_department_rows"), (
        "app.py must consume departments.department_status_for, not reimplement it"
    )
