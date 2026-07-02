import json

from fastapi.testclient import TestClient
from sqlmodel import Session, select

import app as app_module
from conftest import StubAgent
from db_models import Client, RecoveryCampaign, RecoveryJob
import recovery_service


def make_client(test_engine) -> int:
    with Session(test_engine) as session:
        client = Client(
            business_name="Test Co", trade="HVAC", services_json=json.dumps(["AC repair"]),
            hours="9-5", pricing_faq="n/a", escalation_phone="+15550000000",
            inbound_number="+15559990000",
        )
        session.add(client)
        session.commit()
        session.refresh(client)
        return client.id


def test_create_campaign_via_form(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)

    # follow_redirects=False: the redirect target route is built in Task 9
    response = test_client.post(
        f"/clients/{client_id}/recovery/new",
        data={
            "face": "quote",
            "name": "June quotes",
            "customers_raw": "+15551112222,Mike,AC install,8000",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(test_engine) as session:
        jobs = session.exec(select(RecoveryJob)).all()
    assert len(jobs) == 1
    assert jobs[0].customer_name == "Mike"


def test_recovery_campaign_detail_renders(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)
    test_client.post(
        f"/clients/{client_id}/recovery/new",
        data={"face": "reactivation", "name": "Dormant list", "customers_raw": "+1,Sue,Tune-up,400"},
    )

    with Session(test_engine) as session:
        campaign_id = session.exec(select(app_module.RecoveryCampaign)).first().id

    response = test_client.get(f"/clients/{client_id}/recovery/{campaign_id}")
    assert response.status_code == 200
    assert "Sue" in response.text


def test_inbound_sms_routes_active_recovery_reply_to_recovery(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)
    test_client.post(
        f"/clients/{client_id}/recovery/new",
        data={"face": "quote", "name": "June quotes", "customers_raw": "+15551112222,Mike,AC install,8000"},
    )

    monkeypatch.setattr(
        recovery_service,
        "agent",
        StubAgent({
            "reply": "",
            "jobs": [],
            "new_messages": [],
            "pending_tool_call": {"name": "record_response", "input": {"intent": "interested"}},
        }),
    )

    response = test_client.post(
        "/webhook/sms",
        data={"From": "+15551112222", "To": "+15559990000", "Body": "Yes!"},
    )

    assert response.status_code == 200
    assert "1)" in response.text  # slot options offered, not a Frontdesk reply


def test_client_detail_lists_recovery_campaigns(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)
    test_client.post(
        f"/clients/{client_id}/recovery/new",
        data={"face": "quote", "name": "June quotes", "customers_raw": "+1,Mike,AC install,8000"},
    )

    response = test_client.get(f"/clients/{client_id}")

    assert response.status_code == 200
    assert "June quotes" in response.text
