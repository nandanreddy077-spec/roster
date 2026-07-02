import json

from fastapi.testclient import TestClient
from sqlmodel import Session, select

import app as app_module
from db_models import Client, RecoveryJob


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
