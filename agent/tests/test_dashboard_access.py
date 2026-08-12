"""The door for hand-provisioned customers.

Roster sets every business up through the founder console, which never creates
a password for the owner. Before /access/{token} existed, a shop that was fully
set up — number bought, voice wired, departments staffed — had no way at all to
reach its own dashboard. These tests pin that door open, and pin it shut
against forged and expired links.
"""

import os

import app as app_module
import db as db_module
import portal as portal_module
from auth import make_access_token, read_access_token
from db_models import Business
from fastapi.testclient import TestClient
from sqlmodel import Session


def _wire(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)


def _business(test_engine, **kwargs) -> int:
    with Session(test_engine) as session:
        business = Business(business_name="Ridgeline Plumbing", **kwargs)
        session.add(business)
        session.commit()
        session.refresh(business)
        return business.id


# ---- The token itself -------------------------------------------------------


def test_a_token_round_trips_to_its_business():
    env = {"SESSION_SECRET_KEY": "test-secret"}
    assert read_access_token(make_access_token(42, env), env) == 42


def test_a_token_signed_with_another_secret_is_rejected():
    """The whole point: without SESSION_SECRET_KEY nobody can mint a link into
    someone else's business."""
    minted = make_access_token(42, {"SESSION_SECRET_KEY": "attacker-secret"})
    assert read_access_token(minted, {"SESSION_SECRET_KEY": "real-secret"}) is None


def test_a_tampered_payload_is_rejected():
    """The real attack: edit the business id in the link to reach someone
    else's dashboard. The payload is the first dot-separated segment, so this
    rewrites exactly what an attacker would."""
    env = {"SESSION_SECRET_KEY": "test-secret"}
    payload, _, rest = make_access_token(42, env).partition(".")
    forged = make_access_token(99, env).partition(".")[0]
    assert payload != forged

    assert read_access_token(f"{forged}.{rest}", env) is None


def test_a_tampered_signature_is_rejected():
    """Truncating the signature, NOT flipping its last character.

    The flip was flaky and failed roughly 1 CI run in 16 with "assert 42 is
    None" -- it was not tampering at all. itsdangerous base64url-encodes a
    20-byte HMAC into 27 characters: 162 bits of encoding for 160 bits of
    signature, so the final character's low 2 bits are always zero and only
    16 of the 64 base64 characters can ever appear there. When that character
    was 'A', the test's 'A'->'B' substitution changed only an unused bit, the
    signature decoded to identical bytes, and the "tampered" token stayed
    perfectly valid. Measured: 123 no-ops in 2000 tokens, 6.2%.

    Dropping a character changes the decoded bytes every time.
    """
    env = {"SESSION_SECRET_KEY": "test-secret"}
    token = make_access_token(42, env)

    assert read_access_token(token[:-1], env) is None


def test_an_expired_token_is_rejected(monkeypatch):
    env = {"SESSION_SECRET_KEY": "test-secret"}
    token = make_access_token(42, env)
    monkeypatch.setattr("auth.ACCESS_LINK_MAX_AGE_SECONDS", -1)
    assert read_access_token(token, env) is None


def test_a_session_cookie_signature_cannot_be_replayed_as_an_access_link():
    """Both are signed with SESSION_SECRET_KEY; the salt is what keeps them
    from being interchangeable."""
    from itsdangerous import URLSafeTimedSerializer

    env = {"SESSION_SECRET_KEY": "test-secret"}
    unsalted = URLSafeTimedSerializer("test-secret").dumps(42)
    assert read_access_token(unsalted, env) is None


# ---- The route --------------------------------------------------------------


def test_the_link_logs_the_owner_straight_into_their_dashboard(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    business_id = _business(test_engine)

    client = TestClient(app_module.app)
    response = client.get(
        f"/access/{make_access_token(business_id, os.environ)}", follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == portal_module.DASHBOARD_HOME

    # And the session actually sticks — the dashboard renders, no login bounce.
    assert client.get(portal_module.DASHBOARD_HOME, follow_redirects=False).status_code == 200


def test_the_link_never_bounces_a_provisioned_owner_into_onboarding(monkeypatch, test_engine):
    """frontdesk_live is False on every founder-provisioned business (it's set
    by the retired self-serve wizard). The link must not care."""
    _wire(monkeypatch, test_engine)
    business_id = _business(test_engine, frontdesk_live=False)

    client = TestClient(app_module.app)
    response = client.get(
        f"/access/{make_access_token(business_id, os.environ)}", follow_redirects=False
    )
    assert response.headers["location"] != "/onboarding/business"


def test_a_forged_link_gets_no_session(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    _business(test_engine)

    client = TestClient(app_module.app)
    response = client.get("/access/not-a-real-token", follow_redirects=False)
    assert response.status_code == 400
    # No session was granted, so the dashboard still turns them away.
    assert (
        client.get(portal_module.DASHBOARD_HOME, follow_redirects=False).headers["location"]
        == "/login"
    )


def test_a_link_for_a_deleted_business_fails_instead_of_500ing(monkeypatch, test_engine):
    """A link outlives a deletion, and a session pointing at a dead id would
    otherwise blow up on the next page."""
    _wire(monkeypatch, test_engine)
    token = make_access_token(99999, os.environ)

    client = TestClient(app_module.app)
    assert client.get(f"/access/{token}", follow_redirects=False).status_code == 400


def test_the_access_route_is_public(monkeypatch, test_engine):
    """It's the customer's door — it must never sit behind the founder's
    HTTP-Basic gate."""
    monkeypatch.setenv("ADMIN_PASSWORD", "hunter2")
    _wire(monkeypatch, test_engine)

    client = TestClient(app_module.app)
    response = client.get("/access/anything", follow_redirects=False)
    assert response.status_code not in (401, 503)
