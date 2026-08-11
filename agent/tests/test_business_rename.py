from sqlmodel import select
from db_models import Business, Job


def test_business_persists(session):
    # Uses conftest's isolated in-memory `session` fixture — never the real
    # roster.db — so the suite stays idempotent across repeated local runs.
    b = Business(business_name="Test Plumbing", trade="plumbing", email="rename@test.io")
    session.add(b)
    session.commit()
    session.refresh(b)
    assert (
        session.exec(select(Business).where(Business.email == "rename@test.io"))
        .first()
        .business_name
        == "Test Plumbing"
    )


def test_no_client_symbol_remains():
    import db_models

    assert not hasattr(db_models, "Client")


def test_job_uses_business_id():
    assert "business_id" in Job.__table__.columns.keys()
    assert "client_id" not in Job.__table__.columns.keys()
