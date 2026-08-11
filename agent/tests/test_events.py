from events import JOB_BOOKED, MESSAGE_RECEIVED, DomainEvent


def test_domain_event_defaults():
    e = DomainEvent(type=MESSAGE_RECEIVED, business_id=1, payload={"text": "hi"})
    assert e.customer_id is None and e.dedup_key is None
    assert MESSAGE_RECEIVED == "message.received" and JOB_BOOKED == "job.booked"
