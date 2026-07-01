import pytest
from sqlmodel import Session, SQLModel, create_engine

import db_models  # noqa: F401  (registers tables with SQLModel.metadata)


@pytest.fixture
def test_engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})
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
