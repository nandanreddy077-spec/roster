import os
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

DATA_DIR = Path(os.environ.get("ROSTER_DATA_DIR") or (Path(__file__).parent / "data"))
DATA_DIR.mkdir(exist_ok=True, parents=True)

engine = create_engine(f"sqlite:///{DATA_DIR / 'roster.db'}")


def init_db():
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
