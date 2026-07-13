import json
from collections import defaultdict
from typing import Callable, Dict, List
from sqlmodel import Session, select
from db_models import Event
from events import DomainEvent


class EventBus:
    def __init__(self, engine=None):
        from db import engine as _default
        self._engine = engine or _default
        self._subscribers: Dict[str, List[Callable[[DomainEvent], None]]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Callable[[DomainEvent], None]) -> None:
        self._subscribers[event_type].append(handler)

    def publish(self, event: DomainEvent) -> bool:
        with Session(self._engine) as s:
            if event.dedup_key and s.exec(select(Event).where(Event.dedup_key == event.dedup_key)).first():
                return False
            s.add(Event(
                business_id=event.business_id, type=event.type,
                payload_json=json.dumps(event.payload), customer_id=event.customer_id,
                employee_id=event.employee_id, dedup_key=event.dedup_key,
            ))
            s.commit()
        for handler in self._subscribers.get(event.type, []):
            handler(event)  # synchronous, in-process (async/durable deferred behind this seam)
        return True


bus = EventBus()
