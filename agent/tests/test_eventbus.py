from db_models import Business, Event, Job
from eventbus import EventBus
from events import MESSAGE_RECEIVED, DomainEvent
from sqlmodel import Session, select


def _make_business(test_engine):
    with Session(test_engine) as s:
        b = Business(business_name="B", trade="hvac", email="bus@test.io")
        s.add(b)
        s.commit()
        s.refresh(b)
        return b.id


def test_publish_dispatches_and_persists(test_engine):
    bid = _make_business(test_engine)
    seen = []
    bus = EventBus()
    bus.subscribe(MESSAGE_RECEIVED, lambda e: seen.append(e.payload["text"]))
    with Session(test_engine) as s:
        assert (
            bus.publish(
                s, DomainEvent(type=MESSAGE_RECEIVED, business_id=bid, payload={"text": "hi"})
            )
            is True
        )
    assert seen == ["hi"]
    with Session(test_engine) as s:
        assert s.exec(select(Event).where(Event.type == MESSAGE_RECEIVED)).first() is not None


def test_duplicate_dedup_key_dropped(test_engine):
    bid = _make_business(test_engine)
    seen = []
    bus = EventBus()
    bus.subscribe(MESSAGE_RECEIVED, lambda e: seen.append(1))
    mk = lambda: DomainEvent(type=MESSAGE_RECEIVED, business_id=bid, payload={}, dedup_key="SM-DUP")
    with Session(test_engine) as s:
        assert bus.publish(s, mk()) is True
        assert bus.publish(s, mk()) is False
    assert seen == [1]


def test_session_stays_usable_after_dropped_duplicate(test_engine):
    """The property PR 2 depends on: publishing happens inside bookings.book_job,
    on the money path. A dropped duplicate must leave the caller's session able
    to keep working — otherwise a repeat event would take a booking down with it."""
    bid = _make_business(test_engine)
    bus = EventBus()
    mk = lambda: DomainEvent(
        type=MESSAGE_RECEIVED, business_id=bid, payload={}, dedup_key="SM-SAME"
    )
    with Session(test_engine) as s:
        assert bus.publish(s, mk()) is True
        assert bus.publish(s, mk()) is False

        job = Job(business_id=bid, service_type="drain cleaning", urgency="routine")
        s.add(job)
        s.commit()
        s.refresh(job)
        assert job.id is not None


def test_bus_resolves_no_engine_at_construction(test_engine):
    """Regression guard for this PR. Binding db.engine at import is what made
    the `bus` singleton unusable from any live route (see runner.py) — the whole
    reason a fully-built Event Platform has never had a publisher."""
    import ast
    import inspect

    import eventbus

    assert set(inspect.signature(EventBus.__init__).parameters) == {"self"}

    # Asserted over the import statements themselves, not the source text: the
    # module docstring quotes the old `from db import engine` line to explain
    # why it's gone, and a substring check would match the explanation.
    tree = ast.parse(inspect.getsource(eventbus))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
        alias.name for n in ast.walk(tree) if isinstance(n, ast.Import) for alias in n.names
    }
    assert "db" not in imported
