"""Seed a realistic demo so the dashboard looks alive without needing the API key.

Inserts one client (Lou's Heating & Cooling) plus a sample missed-call text-back
conversation and the job it captured — mirroring exactly what the live engine
produces. Safe to re-run: it replaces the prior demo of the same name.
"""
import json

from sqlmodel import Session, delete, select

from db import engine, init_db
from db_models import Client, Job, Message

DEMO_NAME = "Lou's Heating & Cooling"
THREAD = "dashboard"  # the dashboard chat view reads this thread

CONVO = [
    ("assistant", "Hi! Sorry we missed your call to Lou's Heating & Cooling. I can help "
                  "right here over text — what do you need a hand with?"),
    ("user", "My AC stopped cooling last night, house is 85 degrees"),
    ("assistant", "Oh no, that's rough in this heat. Is it not turning on at all, or "
                  "running but blowing warm air?"),
    ("user", "running but blowing warm"),
    ("assistant", "Got it — that's usually a quick fix (low refrigerant or a frozen coil). "
                  "What's the address, and a good callback number? I'll get you on Lou's "
                  "schedule today."),
    ("user", "1420 Pearl St, Denver. 720-555-0148. Name's Mike"),
    ("assistant", "Thanks Mike! I've got you down — AC blowing warm at 1420 Pearl St, "
                  "same-day. Lou or a tech will text you a time within the hour. Hang tight!"),
]


def run():
    init_db()
    with Session(engine) as s:
        prior = s.exec(select(Client).where(Client.business_name == DEMO_NAME)).all()
        for c in prior:
            s.exec(delete(Message).where(Message.client_id == c.id))
            s.exec(delete(Job).where(Job.client_id == c.id))
            s.delete(c)
        s.commit()

        client = Client(
            business_name=DEMO_NAME,
            trade="HVAC",
            services_json=json.dumps(
                ["AC repair", "AC installation", "Furnace repair", "Maintenance plans"]
            ),
            hours="Mon-Sat 7am-7pm, 24/7 emergency",
            pricing_faq="Diagnostic visit $89 (waived if repaired same day). Same-day "
                        "emergency call-out +$50. No exact quotes over text — tech confirms on site.",
            escalation_phone="+19014038929",
            answer_mode="backup",
            inbound_number="+19014038929",
        )
        s.add(client)
        s.commit()
        s.refresh(client)

        for role, text in CONVO:
            s.add(Message(client_id=client.id, customer_phone=THREAD, role=role,
                          content_json=json.dumps(text)))
        s.add(Job(
            client_id=client.id, customer_phone=THREAD, customer_name="Mike",
            service_type="AC not cooling — blowing warm air", urgency="same_day",
            address="1420 Pearl St, Denver", callback_number="(720) 555-0148",
            notes="Started last night, house at 85°. Quick-fix likely.",
        ))
        s.commit()
        print(f"Seeded demo client #{client.id}: {DEMO_NAME} with {len(CONVO)} messages + 1 job.")


if __name__ == "__main__":
    run()
