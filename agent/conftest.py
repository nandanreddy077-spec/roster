import os

import db_models  # noqa: F401  (registers tables with SQLModel.metadata)
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

# The dashboard sits behind HTTP Basic auth (see app.py's dashboard_auth
# middleware). Tests run against a fixed password so they don't depend on
# whatever ADMIN_PASSWORD happens to be in the developer's real environment.
TEST_ADMIN_PASSWORD = "test-admin-password"
DASH_AUTH = {
    "Authorization": "Basic YWRtaW46dGVzdC1hZG1pbi1wYXNzd29yZA=="
}  # admin:test-admin-password


@pytest.fixture(autouse=True)
def _admin_password(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", TEST_ADMIN_PASSWORD)


@pytest.fixture(autouse=True)
def _session_secret(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-session-secret-key")


@pytest.fixture
def test_engine():
    """The database every test runs against.

    Set ROSTER_TEST_DATABASE_URL to point the whole suite at a real Postgres
    instead of in-memory SQLite. That is not decoration: production is moving to
    Postgres, and a suite that only ever ran on SQLite cannot tell you the app
    works there — SQLite does not enforce foreign keys by default, has no
    sequences, and takes a different branch in locks.py. Running both is how the
    migration was verified.

    Schema is dropped and recreated per test so isolation matches the SQLite
    behaviour (a fresh in-memory database each time) rather than leaking rows
    between tests.
    """
    url = os.environ.get("ROSTER_TEST_DATABASE_URL")
    if url:
        eng = create_engine(url)
        SQLModel.metadata.drop_all(eng)
        SQLModel.metadata.create_all(eng)
        return eng

    # StaticPool keeps a single shared connection across threads. Needed because
    # FastAPI's TestClient runs async routes in a worker thread, and plain
    # "sqlite://" in-memory DBs are otherwise per-connection (so the route's
    # thread would see an empty, table-less database).
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(test_engine):
    with Session(test_engine) as s:
        yield s


def provisioned_business(test_engine, **overrides):
    """A business as the FOUNDER creates it, without walking any wizard.

    Self-registration is closed (portal.py), so tests can no longer set
    themselves up by POSTing /signup + /onboarding/*. This mirrors what
    /clients/new actually writes. `frontdesk_live` defaults False on purpose —
    that is the true state of a founder-provisioned shop, and several bugs
    came from tests only ever exercising the self-serve shape where it's True.
    """
    import json as _json

    from db_models import Business

    fields = {
        "business_name": "Ridgeline Plumbing",
        "trade": "Plumbing",
        "services_json": _json.dumps(["Drains"]),
        "hours": "9-5",
        "pricing_faq": "Diagnostic visit: $89.",
        "escalation_phone": "+15555550101",
        "email": "owner@example.com",
    }
    fields.update(overrides)
    with Session(test_engine) as s:
        business = Business(**fields)
        s.add(business)
        s.commit()
        s.refresh(business)
        return business.id


def login_as(client, business_id: int) -> None:
    """Give a TestClient a session for this business, the way a real owner
    gets one now: the founder-issued access link."""
    import os

    from auth import make_access_token

    client.get(f"/access/{make_access_token(business_id, os.environ)}")


class StubAgent:
    """Test double for AgentEngine — returns a canned respond() result."""

    def __init__(self, result):
        self._result = result

    def respond(self, client_config, history, tools=None, system_prompt=None, max_iters=None):
        return self._result
