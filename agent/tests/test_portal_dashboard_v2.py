"""Overview and Departments — the customer's first two department-first pages.

Deployment state comes from departments.department_status_for over Employee
rows, never from requested_roster or a hardcoded badge (the blueprint's
state-derivation invariant). Numbers come from metrics.py, the same module the
founder console uses."""
from sqlmodel import Session
from starlette.testclient import TestClient

import app as app_module
import portal
from auth import hash_password
from db_models import Business, Job
from deployment import deploy_department, deploy_role

_EMAIL = iter(f"v2-{n}@test.io" for n in range(1000))


def _client_for(test_engine, monkeypatch, deploy=None, jobs=0):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(portal, "engine", test_engine)
    email = next(_EMAIL)
    with Session(test_engine) as s:
        b = Business(business_name="Ridgeline Plumbing", trade="Plumbing",
                     email=email, password_hash=hash_password("pw12345"))
        s.add(b)
        s.commit()
        s.refresh(b)
        bid = b.id
        if deploy == "customer_service":
            deploy_department(s, bid, "customer_service")
        elif deploy == "partial":
            deploy_role(s, bid, "frontdesk")
        for i in range(jobs):
            s.add(Job(business_id=bid, customer_phone=f"+1512555{i:04d}",
                      service_type="Drain cleaning", urgency="routine"))
        s.commit()
    client = TestClient(app_module.app)
    client.post("/login", data={"email": email, "password": "pw12345"})
    return client


def _page(test_engine, monkeypatch, path="/v2/dashboard", **kw):
    r = _client_for(test_engine, monkeypatch, **kw).get(path)
    assert r.status_code == 200, r.text[:400]
    return r.text


# --- Overview -----------------------------------------------------------------


def test_overview_is_honest_when_nothing_is_deployed(test_engine, monkeypatch):
    body = _page(test_engine, monkeypatch)

    assert "being set up" in body
    assert "Jobs booked" not in body


def test_overview_shows_outcomes_for_an_active_department(test_engine, monkeypatch):
    body = _page(test_engine, monkeypatch, deploy="customer_service", jobs=3)

    assert "Jobs booked" in body
    assert "3" in body


def test_overview_shows_no_outcomes_for_departments_that_are_not_staffed(test_engine, monkeypatch):
    """Blueprint §4: the strip covers 'only for departments actually active'.
    A Sales number on a business with no Sales department would be an
    assertion about work nobody is doing."""
    body = _page(test_engine, monkeypatch, deploy="customer_service", jobs=1)

    assert "Estimates followed up" not in body


# --- Departments --------------------------------------------------------------

DEPARTMENTS = "/v2/dashboard/departments"


def test_the_grid_lists_every_hireable_department(test_engine, monkeypatch):
    body = _page(test_engine, monkeypatch, path=DEPARTMENTS)

    for name in ("Customer Service", "Sales", "Operations",
                 "Finance", "Customer Success", "Marketing"):
        assert name in body, f"{name} missing from the departments grid"


def test_leadership_is_never_a_department_card(test_engine, monkeypatch):
    """It isn't hireable and comes with every workforce — it lives in the nav
    as the executive view, not as something to acquire (blueprint §4a)."""
    body = _page(test_engine, monkeypatch, path=DEPARTMENTS)

    card_region = body.split('class="portal-dept-grid"')[1]
    assert "Leadership" not in card_region


def test_a_staffed_department_reads_as_working(test_engine, monkeypatch):
    body = _page(test_engine, monkeypatch, path=DEPARTMENTS, deploy="customer_service")

    assert "Working" in body


def test_a_partially_staffed_department_also_reads_as_working(test_engine, monkeypatch):
    """C7: a half-staffed department IS doing work. Completing it is Roster's
    operational problem, not something to worry the owner with — the founder
    console is where 'partial' is visible."""
    body = _page(test_engine, monkeypatch, path=DEPARTMENTS, deploy="partial")

    assert "Working" in body
    assert "Partially staffed" not in body


def test_an_inactive_department_educates_rather_than_just_reporting_absence(
    test_engine, monkeypatch
):
    """Blueprint §7: every inactive card states the problem, the outcome, and
    why an owner eventually wants it — copy the Phase 1 registry already
    carries."""
    from markupsafe import escape

    from departments import get_department

    body = _page(test_engine, monkeypatch, path=DEPARTMENTS)
    finance = get_department("finance")

    # Compared escaped: the registry copy contains apostrophes, which Jinja
    # autoescapes. Asserting on the raw text would fail on correct output.
    assert str(escape(finance.problem)) in body
    assert str(escape(finance.outcome)) in body
    assert str(escape(finance.why_adopt)) in body
    assert "Not yet part of your workforce" in body


def test_no_ai_internal_metric_appears_anywhere(test_engine, monkeypatch):
    """Blueprint §2: the headline is always a business outcome. If a screen
    talks about tokens, models or 'agents active', it is talking about
    Roster's machinery instead of the owner's business."""
    body = _page(test_engine, monkeypatch, path=DEPARTMENTS, deploy="customer_service", jobs=2)

    for word in ("token", "model", "prompt", "LLM", "agent"):
        assert word.lower() not in body.lower(), f"AI-internal term {word!r} is customer-visible"


def test_metric_labels_are_centralized(test_engine, monkeypatch):
    """Presentation lives in one place, so renaming a metric's customer-facing
    wording never means editing templates."""
    assert portal.templates.env.globals["metric_labels"] == portal.METRIC_LABELS
    from metrics import JOBS_BOOKED

    assert portal.METRIC_LABELS[JOBS_BOOKED] == "Jobs booked"


def test_every_metric_a_customer_can_see_has_a_label(test_engine, monkeypatch):
    import metrics as metrics_module

    for key in metrics_module.METRIC_RECORDS:
        assert key in portal.METRIC_LABELS, f"{key!r} would render with no label"
