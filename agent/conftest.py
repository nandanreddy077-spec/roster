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
