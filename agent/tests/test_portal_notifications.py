"""The Notifications page — reads Phase 2's recent_notifications() directly;
no new view model, since there's no synthesis to do over a list that's
already exactly what the page shows."""

import app as app_module
import portal
from auth import hash_password
from db_models import Business, OwnerNotification
from sqlmodel import Session

_EMAIL = iter(f"np-{n}@test.io" for n in range(1000))


def _client_for(test_engine, monkeypatch):
    from starlette.testclient import TestClient

    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(portal, "engine", test_engine)
    email = next(_EMAIL)
    with Session(test_engine) as s:
        b = Business(
            business_name="Ridgeline Plumbing",
            trade="Plumbing",
            email=email,
            password_hash=hash_password("pw12345"),
        )
        s.add(b)
        s.commit()
        s.refresh(b)
        bid = b.id
    client = TestClient(app_module.app)
    client.post("/login", data={"email": email, "password": "pw12345"})
    return client, bid


def test_requires_a_session(test_engine, monkeypatch):
    from starlette.testclient import TestClient

    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(portal, "engine", test_engine)

    r = TestClient(app_module.app).get("/v2/dashboard/notifications", follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_an_empty_log_shows_an_honest_empty_state(test_engine, monkeypatch):
    client, _ = _client_for(test_engine, monkeypatch)

    r = client.get("/v2/dashboard/notifications")

    assert r.status_code == 200
    assert "Nothing yet" in r.text


def test_notifications_render_newest_first(test_engine, monkeypatch):
    client, bid = _client_for(test_engine, monkeypatch)
    with Session(test_engine) as s:
        s.add(
            OwnerNotification(
                business_id=bid, kind="job_booked", source="sms_booking", message="job one"
            )
        )
        s.add(
            OwnerNotification(
                business_id=bid, kind="escalation", source="alert_owner", message="job two"
            )
        )
        s.commit()

    body = client.get("/v2/dashboard/notifications").text

    assert body.index("job two") < body.index("job one")


def test_kind_renders_as_a_customer_label_not_a_raw_string(test_engine, monkeypatch):
    client, bid = _client_for(test_engine, monkeypatch)
    with Session(test_engine) as s:
        s.add(
            OwnerNotification(
                business_id=bid,
                kind="escalation",
                source="alert_owner",
                message="URGENT — caller needs you",
            )
        )
        s.commit()

    body = client.get("/v2/dashboard/notifications").text

    assert "Sent to you personally" in body


def test_never_leaks_another_businesss_notifications(test_engine, monkeypatch):
    client, bid = _client_for(test_engine, monkeypatch)
    with Session(test_engine) as s:
        other = Business(business_name="Other Co", trade="hvac", email="other@test.io")
        s.add(other)
        s.commit()
        s.refresh(other)
        s.add(
            OwnerNotification(
                business_id=other.id, kind="job_booked", source="sms_booking", message="not yours"
            )
        )
        s.commit()

    body = client.get("/v2/dashboard/notifications").text

    assert "not yours" not in body
