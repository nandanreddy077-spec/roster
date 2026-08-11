"""ExpansionWorkspace — the third view model (founder, 2026-07-29). Not a
separate destination: an action nested under the Department Workspace,
covering both directions of growth (a partial department with room left, and
a fully-inactive one with nothing deployed at all). Both share one builder
over the same department_status_for call DepartmentWorkspace already uses —
two view models, one shared computation."""

from sqlmodel import Session

from db_models import Business
from deployment import deploy_department, deploy_role
from workspace import ExpansionWorkspace, build_expansion_workspace


def _business(session, email):
    b = Business(business_name="B", trade="hvac", email=email)
    session.add(b)
    session.commit()
    session.refresh(b)
    return b


def test_the_view_model_has_exactly_its_five_fields():
    """The same structural guard every workspace in this migration has: this
    page stays educate-then-ask, not a growing feature surface."""
    assert set(ExpansionWorkspace.__dataclass_fields__) == {
        "department",
        "problem",
        "current_state",
        "available_employees",
        "expected_outcomes",
    }


def test_unknown_department_returns_none(session):
    b = _business(session, "xw1@test.io")

    assert build_expansion_workspace(session, b.id, "not_a_department") is None


def test_leadership_is_never_expandable(session):
    """Not hireable, included automatically — there is nothing to request."""
    b = _business(session, "xw2@test.io")

    assert build_expansion_workspace(session, b.id, "leadership") is None


def test_a_department_with_nothing_ever_deployable_returns_none(session):
    """Finance/Marketing: every role is still `planned`. Operations used to
    be a third example here too, until Dispatcher gained a real engine
    (2026-07-30, Critical Finding #2 fix)."""
    b = _business(session, "xw3@test.io")

    assert build_expansion_workspace(session, b.id, "finance") is None


def test_a_fully_staffed_department_returns_none(session):
    """Nothing left to expand into — the workspace already offers everything
    the registry has for this department."""
    b = _business(session, "xw4@test.io")
    deploy_department(session, b.id, "customer_service")

    assert build_expansion_workspace(session, b.id, "customer_service") is None


def test_a_fully_inactive_department_assembles_with_empty_current_state(session):
    """The entry point from the Departments grid's educational card — no
    Department Workspace exists to nest under (build_department_workspace
    404s for it), so this must assemble independently."""
    b = _business(session, "xw5@test.io")

    ws = build_expansion_workspace(session, b.id, "customer_service")

    assert ws.department.key == "customer_service"
    assert ws.problem == ws.department.problem
    assert ws.current_state == []
    assert {e.key for e in ws.available_employees} == {"frontdesk", "reviews"}


def test_a_partially_staffed_department_shows_what_is_already_covered(session):
    """The entry point from within an active Department Workspace."""
    b = _business(session, "xw6@test.io")
    deploy_role(session, b.id, "frontdesk")

    ws = build_expansion_workspace(session, b.id, "customer_service")

    assert {e.role_key for e in ws.current_state} == {"frontdesk"}
    assert {e.key for e in ws.available_employees} == {"reviews"}


def test_expected_outcomes_are_metric_labels_not_fabricated_numbers(session):
    """Honesty: the available employees haven't done any work yet, so this is
    a list of capability NAMES, never invented zero-metrics."""
    b = _business(session, "xw7@test.io")

    ws = build_expansion_workspace(session, b.id, "customer_service")

    assert "Review requests sent" in ws.expected_outcomes
    assert "Jobs booked" in ws.expected_outcomes


def test_expected_outcomes_only_reflect_available_employees(session):
    """A department that already has Frontdesk must not claim 'Jobs booked'
    as something expansion would unlock — it's already happening. Reviews
    (still available, not yet hired) now exposes 4 metric labels rather than
    1 — PR #3 (2026-07-30) added follow-up/response/self-reported/negative
    tracking alongside the original review-requests-sent metric."""
    b = _business(session, "xw8@test.io")
    deploy_role(session, b.id, "frontdesk")

    ws = build_expansion_workspace(session, b.id, "customer_service")

    assert ws.expected_outcomes == [
        "Review requests sent",
        "Follow-up reminders sent",
        "Customers who replied",
        "Told us they left a review",
        "Unhappy replies flagged to you",
    ]


def test_workspace_never_crosses_businesses(session):
    a = _business(session, "xw9a@test.io")
    b = _business(session, "xw9b@test.io")
    deploy_role(session, a.id, "frontdesk")

    ws = build_expansion_workspace(session, b.id, "customer_service")

    assert ws.current_state == []
