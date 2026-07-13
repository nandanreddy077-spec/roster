from sqlmodel import Session, select
from db_models import Business, Event
from events import DomainEvent, MESSAGE_RECEIVED
from eventbus import EventBus


def _make_business(test_engine):
    with Session(test_engine) as s:
        b = Business(business_name="B", trade="hvac", email="bus@test.io")
        s.add(b); s.commit(); s.refresh(b)
        return b.id


def test_publish_dispatches_and_persists(test_engine):
    bid = _make_business(test_engine)
    seen = []
    bus = EventBus(test_engine)
    bus.subscribe(MESSAGE_RECEIVED, lambda e: seen.append(e.payload["text"]))
    assert bus.publish(DomainEvent(type=MESSAGE_RECEIVED, business_id=bid, payload={"text": "hi"})) is True
    assert seen == ["hi"]
    with Session(test_engine) as s:
        assert s.exec(select(Event).where(Event.type == MESSAGE_RECEIVED)).first() is not None


def test_duplicate_dedup_key_dropped(test_engine):
    bid = _make_business(test_engine)
    seen = []
    bus = EventBus(test_engine)
    bus.subscribe(MESSAGE_RECEIVED, lambda e: seen.append(1))
    mk = lambda: DomainEvent(type=MESSAGE_RECEIVED, business_id=bid, payload={}, dedup_key="SM-DUP")
    assert bus.publish(mk()) is True
    assert bus.publish(mk()) is False
    assert seen == [1]
