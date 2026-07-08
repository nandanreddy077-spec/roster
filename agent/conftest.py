import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import db_models  # noqa: F401  (registers tables with SQLModel.metadata)

# The dashboard sits behind HTTP Basic auth (see app.py's dashboard_auth
# middleware). Tests run against a fixed password so they don't depend on
# whatever ADMIN_PASSWORD happens to be in the developer's real environment.
TEST_ADMIN_PASSWORD = "test-admin-password"
DASH_AUTH = {"Authorization": "Basic YWRtaW46dGVzdC1hZG1pbi1wYXNzd29yZA=="}  # admin:test-admin-password


@pytest.fixture(autouse=True)
def _admin_password(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", TEST_ADMIN_PASSWORD)


@pytest.fixture
def test_engine():
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


class StubAgent:
    """Test double for AgentEngine — returns a canned respond() result."""

    def __init__(self, result):
        self._result = result

    def respond(self, client_config, history, tools=None, system_prompt=None, max_iters=None):
        return self._result
