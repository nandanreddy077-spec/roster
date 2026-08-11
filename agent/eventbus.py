"""In-process event bus over the append-only `event` table.

`publish` takes the CALLER's session. It previously resolved `db.engine` inside
`__init__`, i.e. at import time, which is exactly why no live code path could use
the `bus` singleton — see runner.py's note — and why every test builds its own. A
session parameter matches every other service here (recovery_service,
review_service, deployment, expansion, notifications) and lets a caller publish
inside the transaction it already holds.

Call publish only AFTER the caller's primary work has committed: a losing dedup
race rolls the session back, and an uncommitted write sharing that session would
be rolled back with it. Same ordering rule notifications.record_owner_notification
already documents.

Second consequence of sharing the session, and the one that actually bit first:
publish commits, and a commit EXPIRES every instance in that session. A caller
holding an ORM object it intends to hand back must refresh it after publishing,
or its fields raise DetachedInstanceError once the session closes. See
bookings.book_job for the worked example.
"""

import json
from collections import defaultdict
from typing import Callable, Dict, List

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from db_models import Event
from events import DomainEvent


class EventBus:
    def __init__(self) -> None:
        self._subscribers: Dict[str, List[Callable[[DomainEvent], None]]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Callable[[DomainEvent], None]) -> None:
        self._subscribers[event_type].append(handler)

    def publish(self, session: Session, event: DomainEvent) -> bool:
        """Persist the event, then dispatch to subscribers. Returns False —
        dispatching nothing — when an event with this dedup_key is already
        recorded, whether found by the pre-check or by losing the insert race to
        a concurrent worker. The session is left usable either way."""
        if (
            event.dedup_key
            and session.exec(select(Event).where(Event.dedup_key == event.dedup_key)).first()
        ):
            return False
        session.add(
            Event(
                business_id=event.business_id,
                type=event.type,
                payload_json=json.dumps(event.payload),
                customer_id=event.customer_id,
                employee_id=event.employee_id,
                dedup_key=event.dedup_key,
            )
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return False
        for handler in self._subscribers.get(event.type, []):
            handler(event)  # synchronous, in-process (async/durable deferred behind this seam)
        return True


bus = EventBus()
