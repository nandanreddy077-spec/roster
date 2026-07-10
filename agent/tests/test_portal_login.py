from fastapi.testclient import TestClient

import app as app_module
import db as db_module
import portal as portal_module


def test_login_form_renders():
    client = TestClient(app_module.app)
    response = client.get("/login")
    assert response.status_code == 200


def test_login_succeeds_and_redirects_to_dashboard(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    client.post("/signup", data={"email": "owner@example.com", "password": "hunter22"})
    client.post("/logout")

    response = client.post(
        "/login", data={"email": "owner@example.com", "password": "hunter22"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"


def test_login_rejects_wrong_password(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    client.post("/signup", data={"email": "owner@example.com", "password": "hunter22"})

    response = client.post("/login", data={"email": "owner@example.com", "password": "wrong"})
    assert response.status_code == 400
    assert "Invalid email or password" in response.text


def test_login_rejects_unknown_email(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    response = client.post("/login", data={"email": "nobody@example.com", "password": "whatever"})
    assert response.status_code == 400
    assert "Invalid email or password" in response.text
