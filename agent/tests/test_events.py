from events import DomainEvent, MESSAGE_RECEIVED, JOB_BOOKED


def test_domain_event_defaults():
    e = DomainEvent(type=MESSAGE_RECEIVED, business_id=1, payload={"text": "hi"})
    assert e.customer_id is None and e.dedup_key is None
    assert MESSAGE_RECEIVED == "message.received" and JOB_BOOKED == "job.booked"
