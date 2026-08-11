"""Per-conversation serialization: two simultaneous messages from the same
customer must run one-at-a-time (interleaved turns corrupt history and break
Anthropic's strict user/assistant alternation); different customers must not
block each other."""

import threading
import time

import app as app_module
import db as db_module
import portal as portal_module
import service
from db_models import Business
from sqlmodel import Session


def test_same_conversation_is_mutually_exclusive():
    from locks import conversation_lock

    active, max_active, lk = [0], [0], threading.Lock()

    def worker():
        with conversation_lock(1, "+15550001111"):
            with lk:
                active[0] += 1
                max_active[0] = max(max_active[0], active[0])
            time.sleep(0.03)
            with lk:
                active[0] -= 1

    threads = [threading.Thread(target=worker) for _ in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert max_active[0] == 1


def test_different_conversations_do_not_block_each_other():
    from locks import conversation_lock

    active, max_active, lk = [0], [0], threading.Lock()

    def worker(phone):
        with conversation_lock(1, phone):
            with lk:
                active[0] += 1
                max_active[0] = max(max_active[0], active[0])
            time.sleep(0.05)
            with lk:
                active[0] -= 1

    t1 = threading.Thread(target=worker, args=("+15550001111",))
    t2 = threading.Thread(target=worker, args=("+15550002222",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert max_active[0] == 2


def test_inbound_sms_turns_for_same_customer_are_serialized(test_engine, monkeypatch):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)

    with Session(test_engine) as s:
        b = Business(business_name="X", inbound_number="+15125550100", frontdesk_live=True)
        s.add(b)
        s.commit()

    active, max_active, lk = [0], [0], threading.Lock()

    class SlowAgent:
        def respond(self, *a, **k):
            with lk:
                active[0] += 1
                max_active[0] = max(max_active[0], active[0])
            time.sleep(0.05)
            with lk:
                active[0] -= 1
            return {"reply": "ok", "jobs": [], "new_messages": [], "pending_tool_call": None}

    monkeypatch.setattr(service, "agent", SlowAgent())

    def turn(sid):
        app_module._process_inbound_sms("+15550001111", "+15125550100", "hi", sid)

    t1 = threading.Thread(target=turn, args=("SMa",))
    t2 = threading.Thread(target=turn, args=("SMb",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert max_active[0] == 1, "same-customer turns must never run concurrently"
