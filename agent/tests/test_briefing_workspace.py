"""BriefingWorkspace — a synthesis read model over the workspaces already
built (founder, 2026-07-29), not an independent report. build_briefing_workspace
consumes build_department_workspace and build_expansion_workspace rather than
querying metrics/EMPLOYEE_RECORDS itself; the Briefing never calls
expansion.record_interest — a growth nudge is a suggestion to read, not an
action taken on the owner's behalf."""
from datetime import datetime

from sqlmodel import Session

from db_models import Business, Job, OwnerNotification
from deployment import deploy_role
from workspace import BriefingWorkspace, build_briefing_workspace


def _business(session, email):
    b = Business(business_name="B", trade="hvac", email=email)
    session.add(b)
    session.commit()
    session.refresh(b)
    return b


def test_the_view_model_has_exactly_its_five_fields():
    assert set(BriefingWorkspace.__dataclass_fields__) == {
        "generated_at", "summary", "highlights", "departments", "notifications",
    }


def test_a_brand_new_business_gets_an_honest_empty_briefing(session):
    b = _business(session, "br1@test.io")

    ws = build_briefing_workspace(session, b.id)

    assert isinstance(ws.generated_at, datetime)
    assert ws.departments == []
    assert ws.notifications == []


def test_growth_nudges_appear_for_departments_with_room(session):
    """Reuses the same is-not-None gate as the Departments grid's can_expand —
    customer_service, sales and customer_success all have at least one
    deployable-but-undeployed employee on a fresh business."""
    b = _business(session, "br2@test.io")

    ws = build_briefing_workspace(session, b.id)

    hrefs = {h.href for h in ws.highlights}
    assert "/v2/dashboard/departments/customer_service/expand" in hrefs
    assert "/v2/dashboard/departments/sales/expand" in hrefs


def test_no_growth_nudge_for_a_department_with_nothing_ever_deployable(session):
    """Operations/Finance/Marketing: build_expansion_workspace correctly
    returns None for these — a nudge would be a dead control, same as an
    unconditional link would be on the Departments grid."""
    b = _business(session, "br3@test.io")

    ws = build_briefing_workspace(session, b.id)

    hrefs = {h.href for h in ws.highlights}
    assert "/v2/dashboard/departments/operations/expand" not in hrefs
    assert "/v2/dashboard/departments/finance/expand" not in hrefs
    assert "/v2/dashboard/departments/marketing/expand" not in hrefs


def test_an_active_department_appears_in_departments_with_its_headline(session):
    b = _business(session, "br4@test.io")
    deploy_role(session, b.id, "frontdesk")
    session.add(Job(business_id=b.id, customer_phone="+15125550001",
                     service_type="Drain cleaning", urgency="routine"))
    session.commit()

    ws = build_briefing_workspace(session, b.id)

    row = next(r for r in ws.departments if r.department.key == "customer_service")
    assert row.href == "/v2/dashboard/departments/customer_service"
    assert row.headline == ("Jobs booked", 1)


def test_a_fully_staffed_department_has_no_growth_nudge(session):
    b = _business(session, "br5@test.io")
    deploy_role(session, b.id, "frontdesk")
    deploy_role(session, b.id, "reviews")

    ws = build_briefing_workspace(session, b.id)

    hrefs = {h.href for h in ws.highlights}
    assert "/v2/dashboard/departments/customer_service/expand" not in hrefs


def test_an_inactive_department_never_appears_in_departments(session):
    """`departments` mirrors Overview's gateway cards (active only) — the
    Departments page is already the place for the full inactive roster."""
    b = _business(session, "br6@test.io")

    ws = build_briefing_workspace(session, b.id)

    assert ws.departments == []


def test_escalations_surface_as_an_attention_highlight(session):
    """Escalations are read straight from DepartmentWorkspace's nested
    EmployeeView — the Briefing never re-derives this from metrics.py
    directly."""
    b = _business(session, "br7@test.io")
    deploy_role(session, b.id, "frontdesk")
    session.add(OwnerNotification(business_id=b.id, kind="escalation", source="alert_owner",
                                   message="URGENT — caller needs you"))
    session.commit()

    ws = build_briefing_workspace(session, b.id)

    attention = [h for h in ws.highlights if h.kind == "attention"]
    assert any("Frontdesk" in h.text for h in attention)
    assert attention[0].href == "/v2/dashboard/departments/customer_service/employees/frontdesk"


def test_highlights_are_ordered_attention_then_working_well_then_growth(session):
    b = _business(session, "br8@test.io")
    deploy_role(session, b.id, "frontdesk")
    session.add(Job(business_id=b.id, customer_phone="+15125550002",
                     service_type="Drain cleaning", urgency="routine"))
    session.add(OwnerNotification(business_id=b.id, kind="escalation", source="alert_owner",
                                   message="URGENT — caller needs you"))
    session.commit()

    ws = build_briefing_workspace(session, b.id)

    kinds = [h.kind for h in ws.highlights]
    assert kinds.index("attention") < kinds.index("working_well") < kinds.index("growth")


def test_highlights_are_capped_at_five(session):
    b = _business(session, "br9@test.io")

    ws = build_briefing_workspace(session, b.id)

    assert len(ws.highlights) <= 5


def test_notifications_are_read_straight_from_recent_notifications(session):
    b = _business(session, "br10@test.io")
    session.add(OwnerNotification(business_id=b.id, kind="job_booked", source="sms_booking",
                                   message="Frontdesk just booked a job"))
    session.commit()

    ws = build_briefing_workspace(session, b.id)

    assert len(ws.notifications) == 1
    assert ws.notifications[0].message == "Frontdesk just booked a job"


def test_workspace_never_crosses_businesses(session):
    a = _business(session, "br11a@test.io")
    b = _business(session, "br11b@test.io")
    deploy_role(session, a.id, "frontdesk")
    session.add(OwnerNotification(business_id=a.id, kind="job_booked", source="sms_booking",
                                   message="a's job"))
    session.commit()

    ws = build_briefing_workspace(session, b.id)

    assert ws.departments == []
    assert ws.notifications == []


def test_build_briefing_workspace_never_records_interest(session):
    """The whole point of a 'nudge': reading the Briefing must never itself
    create a DepartmentInterest row (Phase 3's guard against auto-recording
    interest merely by rendering a recommendation)."""
    from db_models import DepartmentInterest
    from sqlmodel import select

    b = _business(session, "br12@test.io")

    build_briefing_workspace(session, b.id)

    assert session.exec(select(DepartmentInterest)).all() == []
