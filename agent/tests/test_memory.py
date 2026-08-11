from sqlmodel import Session
from db_models import Business, Message
from memory import BusinessMemory


def test_recall_is_business_scoped(test_engine):
    with Session(test_engine) as s:
        b1 = Business(business_name="One", trade="hvac", email="m1@test.io")
        b2 = Business(business_name="Two", trade="plumbing", email="m2@test.io")
        s.add(b1)
        s.add(b2)
        s.commit()
        s.refresh(b1)
        s.refresh(b2)
        b1_id, b2_id = b1.id, b2.id
    c = BusinessMemory(b1_id, test_engine).upsert_customer("+15551110000", "A")
    with Session(test_engine) as s:
        s.add(
            Message(
                business_id=b1_id,
                customer_id=c.id,
                customer_phone="+15551110000",
                role="user",
                content_json='"help"',
            )
        )
        s.add(Message(business_id=b2_id, customer_phone="x", role="user", content_json='"other"'))
        s.commit()
    recalled = BusinessMemory(b1_id, test_engine).recall("anything")
    assert recalled and all(m.business_id == b1_id for m in recalled)


def test_upsert_and_get_customer(test_engine):
    with Session(test_engine) as s:
        b = Business(business_name="B", trade="hvac", email="m3@test.io")
        s.add(b)
        s.commit()
        s.refresh(b)
        bid = b.id
    mem = BusinessMemory(bid, test_engine)
    c = mem.upsert_customer("+15559998888", "Pat")
    got = mem.get_customer("+15559998888")
    assert got.id == c.id and got.name == "Pat"
