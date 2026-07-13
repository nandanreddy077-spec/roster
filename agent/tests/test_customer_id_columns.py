from db_models import Job, Message


def test_job_and_message_have_customer_id():
    assert "customer_id" in Job.__table__.columns.keys()
    assert "customer_id" in Message.__table__.columns.keys()
