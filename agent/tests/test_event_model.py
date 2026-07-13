from db_models import Business, Event


def test_event_persists(session):
    b = Business(business_name="B", trade="hvac", email="ev@test.io")
    session.add(b)
    session.commit()
    session.refresh(b)
    e = Event(business_id=b.id, type="message.received", dedup_key="SM123")
    session.add(e)
    session.commit()
    session.refresh(e)
    assert e.id is not None and e.occurred_at is not None
