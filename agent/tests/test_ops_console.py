"""The founder ops console.

Before Phase 4b the 'Agent roster' on this page was a fiction: Frontdesk was
hardcoded active for every business, and the other tiles derived 'active' from
unrelated config fields (a review link being set, a campaign existing). The
founder has never had a real deployment view — see audit B1."""
from sqlalchemy import event
from sqlmodel import Session, select
from starlette.testclient import TestClient

import app as app_module
from conftest import DASH_AUTH
from db_models import Business, Employee
from deployment import deploy_department, deploy_role


def _business(session, name="Test Co"):
    b = Business(business_name=name, trade="HVAC")
    session.add(b)
    session.commit()
    session.refresh(b)
    return b


def test_a_business_with_nothing_deployed_shows_no_staffed_departments(test_engine, monkeypatch):
    """B1: the page used to claim Frontdesk was active for every business,
    deployed or not."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id

    body = TestClient(app_module.app, headers=DASH_AUTH).get(f"/clients/{bid}").text

    assert "Not staffed" in body
    assert "Customer Service" in body  # the department is listed, just not staffed


def test_a_deployed_department_is_shown_as_staffed(test_engine, monkeypatch):
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id
        deploy_department(s, bid, "customer_service")

    body = TestClient(app_module.app, headers=DASH_AUTH).get(f"/clients/{bid}").text

    assert "Working: Frontdesk, Reviews" in body


def test_a_partially_staffed_department_offers_completion_not_a_fresh_deploy(test_engine, monkeypatch):
    """B9: the affordance must tell the truth about what the click will do."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id
        deploy_role(s, bid, "frontdesk")  # half of Customer Service

    body = TestClient(app_module.app, headers=DASH_AUTH).get(f"/clients/{bid}").text

    assert "Complete deployment" in body
    assert "Partially staffed" in body


def test_a_department_with_nothing_deployable_is_explained_not_offered(test_engine, monkeypatch):
    """B10: Operations/Finance/Marketing have zero live-or-internal employees
    today. Rendering a button that always errors would be a lie."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id

    body = TestClient(app_module.app, headers=DASH_AUTH).get(f"/clients/{bid}").text

    assert "No employees built yet" in body


def test_the_clients_list_shows_real_staffed_departments(test_engine, monkeypatch):
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        staffed = _business(s, "Staffed Co").id
        _business(s, "Empty Co")
        deploy_department(s, staffed, "customer_service")

    body = TestClient(app_module.app, headers=DASH_AUTH).get("/clients").text

    assert "Staffed Co" in body and "Empty Co" in body
    assert "Customer Service" in body


def test_the_clients_list_does_not_query_employees_per_business(test_engine, monkeypatch):
    """B6: one grouped query for every business's employees, matching the
    pattern the recovery counts already use. Asserting the query COUNT is what
    stops this regressing into an N+1 as the client list grows."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        for i in range(5):
            bid = _business(s, f"Co {i}").id
            deploy_role(s, bid, "frontdesk")

    statements = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(test_engine, "before_cursor_execute", _record)
    try:
        TestClient(app_module.app, headers=DASH_AUTH).get("/clients")
    finally:
        event.remove(test_engine, "before_cursor_execute", _record)

    employee_queries = [s_ for s_ in statements if "FROM employee" in s_]
    assert len(employee_queries) == 1, (
        f"expected ONE grouped employee query, got {len(employee_queries)} "
        "— this is an N+1 over the client list"
    )


# --- deploy by department ------------------------------------------------------


def _post_deploy(bid, **data):
    return TestClient(app_module.app, headers=DASH_AUTH).post(
        f"/clients/{bid}/employees/deploy", data=data, follow_redirects=False
    )


def test_deploying_a_department_creates_every_deployable_role(test_engine, monkeypatch):
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id

    r = _post_deploy(bid, department_key="customer_service")

    assert r.status_code == 303
    with Session(test_engine) as s:
        keys = {e.role_key for e in s.exec(
            select(Employee).where(Employee.business_id == bid)).all()}
        assert keys == {"frontdesk", "reviews"}


def test_deploying_the_same_department_twice_is_a_no_op(test_engine, monkeypatch):
    """B9: a double-submit must reach the same end state, not error and not
    duplicate."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id

    first = _post_deploy(bid, department_key="customer_service")
    second = _post_deploy(bid, department_key="customer_service")

    assert first.status_code == 303 and second.status_code == 303
    with Session(test_engine) as s:
        assert len(s.exec(select(Employee).where(Employee.business_id == bid)).all()) == 2


def test_deploying_a_department_with_nothing_deployable_does_not_500(test_engine, monkeypatch):
    """B10: deploy_department raises for these. The route must surface it as a
    message on the page, never as a stack trace. (Operations used to be this
    example, until Dispatcher gained a real engine — 2026-07-30, Critical
    Finding #2 fix; Finance still has zero live/internal employees.)"""
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id

    r = _post_deploy(bid, department_key="finance")

    assert r.status_code == 303
    assert "deploy_error" in r.headers["location"]
    with Session(test_engine) as s:
        assert s.exec(select(Employee).where(Employee.business_id == bid)).all() == []


def test_the_legacy_role_key_form_still_works(test_engine, monkeypatch):
    """role_key predates this and is still how a single employee is deployed;
    it must keep working (test_employee_deploy.py covers it too)."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id

    _post_deploy(bid, role_key="quote_chaser")

    with Session(test_engine) as s:
        assert [e.role_key for e in s.exec(
            select(Employee).where(Employee.business_id == bid)).all()] == ["quote_chaser"]


def test_deploying_a_department_then_following_the_redirect_shows_it_staffed(
    test_engine, monkeypatch
):
    """The founder's actual workflow, end to end: submit the form, follow the
    303, and see the result. Route-level and template-level tests both passing
    would still permit a page that renders stale data after a write — this is
    what catches that."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id
    client = TestClient(app_module.app, headers=DASH_AUTH)

    posted = client.post(
        f"/clients/{bid}/employees/deploy",
        data={"department_key": "customer_service"},
        follow_redirects=False,
    )
    assert posted.status_code == 303

    landed = client.get(posted.headers["location"])

    assert landed.status_code == 200
    assert "Working: Frontdesk, Reviews" in landed.text
    # and the deploy affordance is gone for that department, not offered again
    assert "Complete deployment" not in landed.text


# --- provisioning pipeline stage (B7) -----------------------------------------


def _post_stage(bid, stage):
    return TestClient(app_module.app, headers=DASH_AUTH).post(
        f"/clients/{bid}/pipeline-stage", data={"stage": stage}, follow_redirects=False
    )


def test_a_new_business_starts_at_lead(test_engine, monkeypatch):
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        b = _business(s)
        assert b.pipeline_stage == "lead"


def test_setting_a_stage_moves_the_business(test_engine, monkeypatch):
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id

    r = _post_stage(bid, "provisioning")

    assert r.status_code == 303
    with Session(test_engine) as s:
        assert s.get(Business, bid).pipeline_stage == "provisioning"


def test_setting_the_same_stage_twice_is_idempotent(test_engine, monkeypatch):
    """B7: the route takes an explicit TARGET, never 'advance' — so a
    double-submit or a stale tab re-posting lands on the same stage instead of
    silently skipping one."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id

    first = _post_stage(bid, "qa")
    second = _post_stage(bid, "qa")

    assert first.status_code == 303 and second.status_code == 303
    with Session(test_engine) as s:
        assert s.get(Business, bid).pipeline_stage == "qa"


def test_re_posting_the_current_stage_is_a_no_op_success_not_an_error(test_engine, monkeypatch):
    """B7: a stale tab re-submitting the stage the business is already on is
    harmless and must not show the founder an error page."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id
    _post_stage(bid, "discovery")

    r = _post_stage(bid, "discovery")

    assert r.status_code == 303
    assert "stage_error" not in r.headers["location"]


def test_an_unknown_stage_is_rejected(test_engine, monkeypatch):
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id

    r = _post_stage(bid, "not_a_stage")

    assert r.status_code == 303
    assert "stage_error" in r.headers["location"]
    with Session(test_engine) as s:
        assert s.get(Business, bid).pipeline_stage == "lead"


def test_a_stage_can_move_backward_to_correct_a_mistake(test_engine, monkeypatch):
    """Ops mis-clicks. Refusing to go back would mean the only fix is a
    database edit."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id
    _post_stage(bid, "live")

    _post_stage(bid, "provisioning")

    with Session(test_engine) as s:
        assert s.get(Business, bid).pipeline_stage == "provisioning"


def test_existing_live_businesses_backfill_to_the_live_stage(test_engine, monkeypatch):
    """A business already running before this column existed must not appear
    stuck at 'lead' in the founder's pipeline."""
    import db

    monkeypatch.setattr(db, "engine", test_engine)
    with Session(test_engine) as s:
        b = Business(business_name="Already Live", trade="hvac", frontdesk_live=True)
        b.pipeline_stage = "lead"
        s.add(b)
        s.commit()
        s.refresh(b)
        bid = b.id

    db._backfill_pipeline_stage(test_engine)

    with Session(test_engine) as s:
        assert s.get(Business, bid).pipeline_stage == "live"


# --- expansion requests (B8) --------------------------------------------------


def test_open_expansion_requests_are_shown_oldest_first(test_engine, monkeypatch):
    """Asserts on a marker only the expansion section renders — every
    department name already appears in the department grid, so matching on
    'Finance' alone would pass without this section existing at all."""
    from expansion import record_interest

    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id
        record_interest(s, bid, "marketing")   # asked first
        record_interest(s, bid, "finance")     # asked second

    body = TestClient(app_module.app, headers=DASH_AUTH).get(f"/clients/{bid}").text

    assert "Asked for Marketing" in body
    assert "Asked for Finance" in body
    # oldest first — a work queue, not a feed
    assert body.index("Asked for Marketing") < body.index("Asked for Finance")


def test_a_business_with_no_requests_shows_none(test_engine, monkeypatch):
    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id

    body = TestClient(app_module.app, headers=DASH_AUTH).get(f"/clients/{bid}").text

    assert "Asked for" not in body


def test_actioning_a_request_removes_it_from_the_open_list(test_engine, monkeypatch):
    from db_models import DepartmentInterest
    from expansion import record_interest

    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id
        interest_id = record_interest(s, bid, "finance").id

    r = TestClient(app_module.app, headers=DASH_AUTH).post(
        f"/clients/{bid}/interests/{interest_id}/actioned", follow_redirects=False
    )

    assert r.status_code == 303
    with Session(test_engine) as s:
        assert s.get(DepartmentInterest, interest_id).actioned_at is not None


def test_actioning_the_same_request_twice_is_harmless(test_engine, monkeypatch):
    """B8: mark_actioned is already idempotent — the route needs only PRG."""
    from db_models import DepartmentInterest
    from expansion import record_interest

    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        bid = _business(s).id
        interest_id = record_interest(s, bid, "finance").id
    client = TestClient(app_module.app, headers=DASH_AUTH)

    client.post(f"/clients/{bid}/interests/{interest_id}/actioned")
    with Session(test_engine) as s:
        first_time = s.get(DepartmentInterest, interest_id).actioned_at

    second = client.post(f"/clients/{bid}/interests/{interest_id}/actioned",
                         follow_redirects=False)

    assert second.status_code == 303
    with Session(test_engine) as s:
        assert s.get(DepartmentInterest, interest_id).actioned_at == first_time


def test_one_business_cannot_action_anothers_request(test_engine, monkeypatch):
    """Business isolation at the ROUTE layer: the interest id is in the URL,
    so the route must verify it belongs to the business in the path."""
    from db_models import DepartmentInterest
    from expansion import record_interest

    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        a = _business(s, "A Co").id
        b = _business(s, "B Co").id
        theirs = record_interest(s, b, "finance").id

    r = TestClient(app_module.app, headers=DASH_AUTH).post(
        f"/clients/{a}/interests/{theirs}/actioned", follow_redirects=False
    )

    assert r.status_code == 404
    with Session(test_engine) as s:
        assert s.get(DepartmentInterest, theirs).actioned_at is None


# --- /clients/new becomes the single door -------------------------------------

WIZARD_FIELDS = {
    "business_name": "Ridgeline Plumbing",
    "trade": "Plumbing",
    "services": "Drain cleaning, Water heaters",
    "hours": "Mon-Sat 7am-7pm",
    "pricing_faq": "Diagnostic $89, waived if repaired same day.",
    "escalation_phone": "+15125550148",
    "answer_mode": "primary",
    "business_phone": "+15125550100",
}


def test_clients_new_captures_every_field_the_retired_wizard_collected(test_engine, monkeypatch):
    """After Phase 7 there is no other way to create a business, so a silently
    dropped field here becomes an unprovisionable customer. business_phone is
    the one the wizard collected that this form did not."""
    monkeypatch.setattr(app_module, "engine", test_engine)

    TestClient(app_module.app, headers=DASH_AUTH).post(
        "/clients/new", data=WIZARD_FIELDS, follow_redirects=False
    )

    with Session(test_engine) as s:
        b = s.exec(select(Business).where(
            Business.business_name == "Ridgeline Plumbing")).first()
        assert b.trade == "Plumbing"
        assert b.services == ["Drain cleaning", "Water heaters"]
        assert b.hours == "Mon-Sat 7am-7pm"
        assert b.pricing_faq.startswith("Diagnostic $89")
        assert b.escalation_phone == "+15125550148"
        assert b.answer_mode == "primary"
        assert b.business_phone == "+15125550100"


def test_clients_new_can_deploy_the_recommended_department_at_creation(test_engine, monkeypatch):
    """The discovery call decides the department; creating the business and
    staffing it shouldn't be two trips through the console."""
    monkeypatch.setattr(app_module, "engine", test_engine)

    TestClient(app_module.app, headers=DASH_AUTH).post(
        "/clients/new",
        data={**WIZARD_FIELDS, "department_key": "customer_service"},
        follow_redirects=False,
    )

    with Session(test_engine) as s:
        b = s.exec(select(Business).where(
            Business.business_name == "Ridgeline Plumbing")).first()
        keys = {e.role_key for e in s.exec(
            select(Employee).where(Employee.business_id == b.id)).all()}
        assert keys == {"frontdesk", "reviews"}


def test_clients_new_without_a_department_deploys_nothing(test_engine, monkeypatch):
    monkeypatch.setattr(app_module, "engine", test_engine)

    TestClient(app_module.app, headers=DASH_AUTH).post(
        "/clients/new", data=WIZARD_FIELDS, follow_redirects=False
    )

    with Session(test_engine) as s:
        b = s.exec(select(Business).where(
            Business.business_name == "Ridgeline Plumbing")).first()
        assert s.exec(select(Employee).where(Employee.business_id == b.id)).all() == []


def test_a_bad_department_choice_does_not_lose_the_business(test_engine, monkeypatch):
    """The business is the valuable thing on this form. A department that
    can't be staffed yet must not take the whole submission down with it.
    (Operations used to be this example, until Dispatcher gained a real
    engine — 2026-07-30, Critical Finding #2 fix; Finance still has zero
    live/internal employees.)"""
    monkeypatch.setattr(app_module, "engine", test_engine)

    r = TestClient(app_module.app, headers=DASH_AUTH).post(
        "/clients/new",
        data={**WIZARD_FIELDS, "department_key": "finance"},
        follow_redirects=False,
    )

    assert r.status_code == 303
    with Session(test_engine) as s:
        b = s.exec(select(Business).where(
            Business.business_name == "Ridgeline Plumbing")).first()
        assert b is not None
        assert s.exec(select(Employee).where(Employee.business_id == b.id)).all() == []


# --- provision-number idempotency (B5 — pre-existing money risk) ---------------


def test_provisioning_a_number_twice_does_not_buy_a_second_one(test_engine, monkeypatch):
    """B5: this route always bought a FRESH Twilio number. POST-redirect-GET
    protects against refresh-after-response, but not against a double-click
    while the first request is still in flight — two POSTs, two numbers
    bought, and the spare is never released. Real money, every time."""
    monkeypatch.setattr(app_module, "engine", test_engine)
    purchases = []

    def _fake_buy(area_code=None):
        purchases.append(area_code)
        return {"phone_number": f"+1512555{len(purchases):04d}", "sid": f"PN{len(purchases)}"}

    monkeypatch.setattr(app_module, "buy_twilio_number", _fake_buy)
    monkeypatch.setattr(app_module, "register_number_with_xai",
                        lambda n: {"signing_secret": "sec"})
    monkeypatch.setattr(app_module, "attach_number_to_xai_trunk", lambda sid, n: None)
    with Session(test_engine) as s:
        bid = _business(s).id
    client = TestClient(app_module.app, headers=DASH_AUTH)

    client.post(f"/clients/{bid}/provision-number", data={"area_code": "512"})
    second = client.post(f"/clients/{bid}/provision-number", data={"area_code": "512"},
                         follow_redirects=False)

    assert len(purchases) == 1, f"bought {len(purchases)} numbers — each one costs money"
    assert second.status_code == 303
    assert "provision_error" in second.headers["location"]
    with Session(test_engine) as s:
        assert s.get(Business, bid).twilio_number_sid == "PN1"
