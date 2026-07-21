"""Founder-admin 'deploy an employee' action (platform PRD §11a: Founder
Admin is the only place an Employee gets deployed for a business)."""
import json

from fastapi.testclient import TestClient
from sqlmodel import Session

import app as app_module
from conftest import DASH_AUTH
from db_models import Business
from runner import is_active


def make_client(test_engine) -> int:
    with Session(test_engine) as session:
        client = Business(business_name="Test Co", trade="HVAC")
        session.add(client)
        session.commit()
        session.refresh(client)
        return client.id


def test_deploy_employee_appends_to_requested_roster(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app, headers=DASH_AUTH)

    response = test_client.post(
        f"/clients/{client_id}/employees/deploy",
        data={"role_key": "upsell_agent"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(test_engine) as session:
        business = session.get(Business, client_id)
        assert json.loads(business.requested_roster) == ["upsell_agent"]
        assert is_active(business, "upsell_agent") is True


def test_deploy_employee_is_idempotent(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app, headers=DASH_AUTH)

    test_client.post(f"/clients/{client_id}/employees/deploy", data={"role_key": "upsell_agent"})
    test_client.post(f"/clients/{client_id}/employees/deploy", data={"role_key": "upsell_agent"})

    with Session(test_engine) as session:
        business = session.get(Business, client_id)
        assert json.loads(business.requested_roster) == ["upsell_agent"]


def test_deploy_employee_requires_admin_auth(test_engine, monkeypatch):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)

    response = test_client.post(f"/clients/{client_id}/employees/deploy", data={"role_key": "upsell_agent"})

    assert response.status_code == 401
