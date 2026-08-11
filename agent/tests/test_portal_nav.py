"""The customer dashboard's navigation shell.

DESIGN PRINCIPLE (founder, 2026-07-29): navigation represents stable customer
concepts, never implementation structure. The nav names things an owner
already thinks about; it never names Roster's internals. A nav built from
implementation structure has to be renamed every time the implementation
changes — exactly what this migration is undoing."""

import re

from sqlmodel import Session
from starlette.testclient import TestClient

import app as app_module
import portal
from db_models import Business


def _nav_html(body: str) -> str:
    match = re.search(r'<nav class="portal-nav".*?</nav>', body, re.S)
    assert match, "portal-nav not found in rendered page"
    return match.group(0)


def test_the_five_nav_items_are_the_blueprint_five_in_order():
    assert [item["key"] for item in portal.NAV_ITEMS] == [
        "overview",
        "departments",
        "briefing",
        "notifications",
        "settings",
    ]


def test_there_is_no_sixth_nav_item():
    """Expansion is contextual content, never navigation — a permanent 'buy
    more' tab would make the product read as a storefront (blueprint §5)."""
    assert len(portal.NAV_ITEMS) == 5
    labels = " ".join(item["label"].lower() for item in portal.NAV_ITEMS)
    assert "grow" not in labels and "workforce" not in labels


def test_the_nav_names_no_implementation_structure():
    """THE design principle, as a test. These are how the work is BUILT, not
    how the owner thinks about their business."""
    forbidden = ("employee", "agent", "job", "campaign", "recovery", "roster", "role")
    labels = " ".join(item["label"].lower() for item in portal.NAV_ITEMS)
    offenders = [word for word in forbidden if word in labels]
    assert offenders == [], f"nav names implementation structure: {offenders}"


def test_every_nav_label_is_centralized():
    """Renaming a nav item must be a one-line change, so labels live in
    NAV_ITEMS and templates render {{ item.label }}."""
    assert portal.templates.env.globals["nav_items"] == portal.NAV_ITEMS


def test_the_briefing_nav_label_comes_from_the_one_constant():
    briefing = next(i for i in portal.NAV_ITEMS if i["key"] == "briefing")
    assert briefing["label"] == portal.BRIEFING_LABEL


def test_the_new_dashboard_requires_a_session(test_engine, monkeypatch):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(portal, "engine", test_engine)

    r = TestClient(app_module.app).get("/v2/dashboard", follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_the_new_dashboard_renders_every_nav_item(test_engine, monkeypatch):
    body = _render_dashboard(test_engine, monkeypatch, "nav1@test.io")

    nav = _nav_html(body)
    for item in portal.NAV_ITEMS:
        assert item["label"] in nav, f"{item['label']} missing from nav"


def test_the_current_page_is_marked_active(test_engine, monkeypatch):
    body = _render_dashboard(test_engine, monkeypatch, "nav2@test.io")

    assert 'aria-current="page"' in _nav_html(body)


def test_the_mobile_bar_renders(test_engine, monkeypatch):
    """The owner persona is on a phone in a truck (platform PRD P1), so the
    nav has to work there, not only on a desktop sidebar."""
    body = _render_dashboard(test_engine, monkeypatch, "nav3@test.io")

    assert "portal-tabbar" in body


def test_an_unactivated_business_still_reaches_the_dashboard(test_engine, monkeypatch):
    """The old dashboard bounced these into onboarding. After Phase 6 there is
    no onboarding wizard to bounce to, so the new one shows honest empty
    states instead."""
    body = _render_dashboard(test_engine, monkeypatch, "nav4@test.io", frontdesk_live=False)

    assert "portal-nav" in body


def _render_dashboard(test_engine, monkeypatch, email, **kw):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(portal, "engine", test_engine)
    with Session(test_engine) as s:
        b = Business(business_name="Ridgeline Plumbing", trade="Plumbing", email=email, **kw)
        s.add(b)
        s.commit()
        s.refresh(b)
        bid = b.id
    client = TestClient(app_module.app)
    # Log in through the real password path so the session cookie is genuine.
    with Session(test_engine) as s:
        biz = s.get(Business, bid)
        from auth import hash_password

        biz.password_hash = hash_password("pw12345")
        s.add(biz)
        s.commit()
    client.post("/login", data={"email": email, "password": "pw12345"})
    r = client.get("/v2/dashboard")
    assert r.status_code == 200, r.text[:400]
    return r.text
