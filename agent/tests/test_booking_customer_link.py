from sqlmodel import select
from db_models import Business, Job, Customer
from service import handle_customer_message


class _FakeAgent:
    def respond(self, client_config, history, tools=None, system_prompt=None, max_iters=None):
        return {
            "reply": "ok",
            "jobs": [
                {
                    "id": "t1",
                    "input": {"service_type": "AC", "urgency": "routine", "customer_name": "Pat"},
                }
            ],
            "new_messages": [],
            "pending_tool_call": None,
        }


def test_booking_links_customer(session, monkeypatch):
    import service

    monkeypatch.setattr(service, "agent", _FakeAgent())
    b = Business(
        business_name="B",
        trade="hvac",
        email="link@test.io",
        frontdesk_live=True,
        trial_cap_cents=10000,
    )
    session.add(b)
    session.commit()
    session.refresh(b)
    handle_customer_message(session, b, "+15552223333", "my AC is out")
    job = session.exec(select(Job).where(Job.business_id == b.id)).first()
    assert job.customer_id is not None
    cust = session.get(Customer, job.customer_id)
    assert cust is not None and cust.phone == "+15552223333" and cust.name == "Pat"
