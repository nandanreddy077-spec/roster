"""Settings was in the nav for weeks pointing at a route that didn't exist —
a 404 in the customer's top bar, and the exact thing ARCHITECTURE.md invariant
9 ("no dead controls") forbids. These tests keep it real."""
from fastapi.testclient import TestClient
from sqlmodel import Session, select

import app as app_module
import db as db_module
import portal as portal_module
from db_models import Business


def _logged_in(monkeypatch, test_engine, **kwargs) -> TestClient:
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    with Session(test_engine) as session:
        business = Business(business_name="Ridgeline Plumbing", trade="Plumbing", **kwargs)
        session.add(business)
        session.commit()
        session.refresh(business)
        business_id = business.id

    client = TestClient(app_module.app)
    from auth import make_access_token
    import os

    client.get(f"/access/{make_access_token(business_id, os.environ)}")
    return client


def test_every_nav_item_resolves_to_a_real_page(monkeypatch, test_engine):
    """The regression that started this: a nav label whose href 404s."""
    client = _logged_in(monkeypatch, test_engine)
    for item in portal_module.NAV_ITEMS:
        response = client.get(item["href"], follow_redirects=False)
        assert response.status_code == 200, f"{item['label']} → {item['href']} gave {response.status_code}"


def test_settings_requires_a_session(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)
    client = TestClient(app_module.app)
    response = client.get(portal_module.SETTINGS_HOME, follow_redirects=False)
    assert response.headers["location"] == "/login"


def test_settings_shows_the_owners_current_values(monkeypatch, test_engine):
    client = _logged_in(
        monkeypatch, test_engine,
        review_link="https://g.page/r/existing", referral_incentive="$25 off",
    )
    body = client.get(portal_module.SETTINGS_HOME).text
    assert "https://g.page/r/existing" in body
    assert "$25 off" in body


def test_saving_a_review_link_persists_and_returns_to_settings(monkeypatch, test_engine):
    client = _logged_in(monkeypatch, test_engine)
    response = client.post(
        "/dashboard/review-link",
        data={"review_link": "https://g.page/r/new"},
        follow_redirects=False,
    )
    assert response.headers["location"].startswith(portal_module.SETTINGS_HOME)
    with Session(test_engine) as session:
        business = session.exec(select(Business)).first()
        assert business.review_link == "https://g.page/r/new"


def test_saving_a_referral_incentive_persists_and_returns_to_settings(monkeypatch, test_engine):
    client = _logged_in(monkeypatch, test_engine)
    response = client.post(
        "/dashboard/referral-incentive",
        data={"referral_incentive": "$50 off"},
        follow_redirects=False,
    )
    assert response.headers["location"].startswith(portal_module.SETTINGS_HOME)
    with Session(test_engine) as session:
        business = session.exec(select(Business)).first()
        assert business.referral_incentive == "$50 off"


def test_clearing_a_field_turns_the_feature_off(monkeypatch, test_engine):
    """Blank means off, not "keep the old value" — the settings copy promises
    that, and Reviews/Referral both gate on the column being set."""
    client = _logged_in(monkeypatch, test_engine, review_link="https://g.page/r/existing")
    client.post("/dashboard/review-link", data={"review_link": ""})
    with Session(test_engine) as session:
        assert session.exec(select(Business)).first().review_link is None
