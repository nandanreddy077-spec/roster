"""The Expansion route and template — nested under the Department Workspace,
not a separate destination (founder, 2026-07-29). Renders one
ExpansionWorkspace (workspace.py); the route never touches DepartmentInterest
or deployment state beyond calling expansion.record_interest."""
from sqlmodel import Session, select
from starlette.testclient import TestClient

import app as app_module
import portal
from auth import hash_password
from db_models import Business, DepartmentInterest
from deployment import deploy_department, deploy_role

_EMAIL = iter(f"xw-{n}@test.io" for n in range(1000))


def _client_for(test_engine, monkeypatch, deploy=None):
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
        s.commit()
    client = TestClient(app_module.app)
    client.post("/login", data={"email": email, "password": "pw12345"})
    return client, bid


def test_unknown_department_404s(test_engine, monkeypatch):
    client, _ = _client_for(test_engine, monkeypatch)

    r = client.get("/v2/dashboard/departments/not_a_department/expand")

    assert r.status_code == 404


def test_a_fully_staffed_department_404s(test_engine, monkeypatch):
    """Nothing left to expand into."""
    client, _ = _client_for(test_engine, monkeypatch, deploy="customer_service")

    r = client.get("/v2/dashboard/departments/customer_service/expand")

    assert r.status_code == 404


def test_a_department_with_nothing_deployable_404s(test_engine, monkeypatch):
    client, _ = _client_for(test_engine, monkeypatch)

    r = client.get("/v2/dashboard/departments/operations/expand")

    assert r.status_code == 404


def test_the_fully_inactive_entry_point_renders(test_engine, monkeypatch):
    """Reached from the Departments grid's educational card — no Department
    Workspace exists to nest under, so this must work standalone."""
    client, _ = _client_for(test_engine, monkeypatch)

    r = client.get("/v2/dashboard/departments/customer_service/expand")

    assert r.status_code == 200
    assert "isn't staffed" in r.text
    assert "Frontdesk" in r.text
    assert "Reviews" in r.text


def test_the_partial_entry_point_shows_what_is_already_covered(test_engine, monkeypatch):
    """Reached from within an active Department Workspace."""
    client, _ = _client_for(test_engine, monkeypatch, deploy="partial")

    r = client.get("/v2/dashboard/departments/customer_service/expand")

    assert "already working here" in r.text
    assert "Frontdesk" in r.text


def test_the_narrative_order_is_covered_then_available_then_outcomes_then_cta(
    test_engine, monkeypatch
):
    """The product requirement, in DOM order: what's covered -> what's
    available -> what it unlocks -> only then the CTA."""
    client, _ = _client_for(test_engine, monkeypatch, deploy="partial")

    body = client.get("/v2/dashboard/departments/customer_service/expand").text

    covered = body.index("Currently covered")
    available = body.index("Who you could add")
    unlocks = body.index("What this unlocks")
    cta = body.index("Ask us about Customer Service")
    assert covered < available < unlocks < cta


def test_no_upgrade_or_pricing_language_appears(test_engine, monkeypatch):
    """Founder requirement: feels like hiring, not a plan comparison."""
    client, _ = _client_for(test_engine, monkeypatch)

    body = client.get("/v2/dashboard/departments/customer_service/expand").text

    for word in ("upgrade", "plan", "$", "price", "billing"):
        assert word.lower() not in body.lower(), f"{word!r} makes this feel like a pricing page"


def test_requesting_expansion_records_interest_and_redirects_back(test_engine, monkeypatch):
    client, bid = _client_for(test_engine, monkeypatch)

    r = client.post("/v2/dashboard/departments/sales/expand", follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"] == "/v2/dashboard/departments/sales/expand?requested=true"
    with Session(test_engine) as s:
        rows = s.exec(
            select(DepartmentInterest).where(DepartmentInterest.business_id == bid)
        ).all()
        assert len(rows) == 1
        assert rows[0].department_key == "sales"


def test_a_double_submit_records_exactly_one_interest(test_engine, monkeypatch):
    """Phase 3's idempotency, exercised through this route."""
    client, bid = _client_for(test_engine, monkeypatch)

    client.post("/v2/dashboard/departments/sales/expand")
    client.post("/v2/dashboard/departments/sales/expand")

    with Session(test_engine) as s:
        rows = s.exec(
            select(DepartmentInterest).where(DepartmentInterest.business_id == bid)
        ).all()
        assert len(rows) == 1


def test_following_the_redirect_shows_the_thank_you_message(test_engine, monkeypatch):
    client, _ = _client_for(test_engine, monkeypatch)

    posted = client.post("/v2/dashboard/departments/sales/expand", follow_redirects=False)
    landed = client.get(posted.headers["location"])

    assert "we'll be in touch" in landed.text.lower()


def test_leadership_cannot_be_requested(test_engine, monkeypatch):
    """build_expansion_workspace's None-check (not hireable) fires before
    record_interest is ever called, so this 404s the same way every other
    'nothing here' case does — consistent with the GET route."""
    client, _ = _client_for(test_engine, monkeypatch)

    r = client.post("/v2/dashboard/departments/leadership/expand")

    assert r.status_code == 404


# --- the Department Workspace's "Expand" link ---------------------------------


def test_a_partial_department_workspace_offers_to_expand(test_engine, monkeypatch):
    client, _ = _client_for(test_engine, monkeypatch, deploy="partial")

    body = client.get("/v2/dashboard/departments/customer_service").text

    assert 'href="/v2/dashboard/departments/customer_service/expand"' in body


def test_a_fully_staffed_department_workspace_does_not_offer_to_expand(test_engine, monkeypatch):
    """Nothing left to hire in this department — no dead link."""
    client, _ = _client_for(test_engine, monkeypatch, deploy="customer_service")

    body = client.get("/v2/dashboard/departments/customer_service").text

    assert "/expand" not in body
