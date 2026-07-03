import json
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session, select

import app as app_module
from conftest import StubAgent
from db_models import Client, Job, RecoveryCampaign, RecoveryJob, ReferralLead
import recovery_service
import referral_service


def make_client(test_engine) -> int:
    with Session(test_engine) as session:
        client = Client(
            business_name="Test Co", trade="HVAC", services_json=json.dumps(["AC repair"]),
            hours="9-5", pricing_faq="n/a", escalation_phone="+15550000000",
            inbound_number="+15559990000",
        )
        session.add(client)
        session.commit()
        session.refresh(client)
        return client.id


def test_create_campaign_via_form(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)

    # follow_redirects=False: the redirect target route is built in Task 9
    response = test_client.post(
        f"/clients/{client_id}/recovery/new",
        data={
            "face": "quote",
            "name": "June quotes",
            "customers_raw": "+15551112222,Mike,AC install,8000",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(test_engine) as session:
        jobs = session.exec(select(RecoveryJob)).all()
    assert len(jobs) == 1
    assert jobs[0].customer_name == "Mike"


def test_recovery_campaign_detail_renders(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)
    test_client.post(
        f"/clients/{client_id}/recovery/new",
        data={"face": "reactivation", "name": "Dormant list", "customers_raw": "+1,Sue,Tune-up,400"},
    )

    with Session(test_engine) as session:
        campaign_id = session.exec(select(app_module.RecoveryCampaign)).first().id

    response = test_client.get(f"/clients/{client_id}/recovery/{campaign_id}")
    assert response.status_code == 200
    assert "Sue" in response.text


def test_inbound_sms_routes_active_recovery_reply_to_recovery(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)
    test_client.post(
        f"/clients/{client_id}/recovery/new",
        data={"face": "quote", "name": "June quotes", "customers_raw": "+15551112222,Mike,AC install,8000"},
    )

    # Recovery only claims a reply once it has actually texted the customer —
    # simulate the day-1 sequence message having already gone out.
    with Session(test_engine) as session:
        job = session.exec(select(RecoveryJob)).first()
        job.last_sent_day = 1
        session.add(job)
        session.commit()

    monkeypatch.setattr(
        recovery_service,
        "agent",
        StubAgent({
            "reply": "",
            "jobs": [],
            "new_messages": [],
            "pending_tool_call": {"name": "record_response", "input": {"intent": "interested"}},
        }),
    )

    response = test_client.post(
        "/webhook/sms",
        data={"From": "+15551112222", "To": "+15559990000", "Body": "Yes!"},
    )

    assert response.status_code == 200
    assert "1)" in response.text  # slot options offered, not a Frontdesk reply


def test_client_detail_lists_recovery_campaigns(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)
    test_client.post(
        f"/clients/{client_id}/recovery/new",
        data={"face": "quote", "name": "June quotes", "customers_raw": "+1,Mike,AC install,8000"},
    )

    response = test_client.get(f"/clients/{client_id}")

    assert response.status_code == 200
    assert "June quotes" in response.text


def test_new_campaign_form_preselects_face_from_query_param(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)

    response = test_client.get(f"/clients/{client_id}/recovery/new?face=membership")

    assert response.status_code == 200
    assert 'value="membership" selected' in response.text


def test_create_membership_campaign_accepts_valid_date(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)

    response = test_client.post(
        f"/clients/{client_id}/recovery/new",
        data={"face": "membership", "name": "July renewals", "customers_raw": "+1,Sarah,AC tune-up,2026-07-15"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(test_engine) as session:
        job = session.exec(select(RecoveryJob)).first()
    assert job.anchor_date == "2026-07-15"


def test_create_membership_campaign_rejects_malformed_date(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)

    response = test_client.post(
        f"/clients/{client_id}/recovery/new",
        data={"face": "membership", "name": "July renewals", "customers_raw": "+1,Sarah,AC tune-up,not-a-date"},
    )

    assert response.status_code == 400


def test_client_detail_groups_campaigns_by_named_agent_tile(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)
    test_client.post(
        f"/clients/{client_id}/recovery/new",
        data={"face": "quote", "name": "June quotes", "customers_raw": "+1,Mike,AC install,8000"},
    )
    test_client.post(
        f"/clients/{client_id}/recovery/new",
        data={"face": "membership", "name": "July renewals", "customers_raw": "+2,Sarah,AC tune-up,2026-07-15"},
    )

    response = test_client.get(f"/clients/{client_id}")

    assert response.status_code == 200
    assert "Chaser" in response.text
    assert "Rebooker" in response.text
    assert "Renewals" in response.text
    assert "June quotes" in response.text
    assert "July renewals" in response.text


def test_recovery_campaign_detail_shows_display_name_not_raw_face(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)
    test_client.post(
        f"/clients/{client_id}/recovery/new",
        data={"face": "membership", "name": "July renewals", "customers_raw": "+1,Sarah,AC tune-up,2026-07-15"},
    )
    with Session(test_engine) as session:
        campaign_id = session.exec(select(app_module.RecoveryCampaign)).first().id

    response = test_client.get(f"/clients/{client_id}/recovery/{campaign_id}")

    assert response.status_code == 200
    assert "Renewals" in response.text


def test_set_review_link_saves_and_shows_on_client_detail(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)

    response = test_client.post(
        f"/clients/{client_id}/review-link",
        data={"review_link": "https://g.page/r/test-review-link"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    with Session(test_engine) as session:
        client = session.get(Client, client_id)
    assert client.review_link == "https://g.page/r/test-review-link"

    detail = test_client.get(f"/clients/{client_id}")
    assert "https://g.page/r/test-review-link" in detail.text


class FakeSMS:
    def __init__(self):
        self.sent = []

    def send(self, from_number, to_number, body):
        self.sent.append({"from": from_number, "to": to_number, "body": body})


class ExplodingSMS:
    def send(self, from_number, to_number, body):
        raise RuntimeError("simulated Twilio failure")


def test_mark_job_done_sends_review_sms_when_link_set(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)
    test_client.post(f"/clients/{client_id}/review-link", data={"review_link": "https://g.page/r/test"})

    with Session(test_engine) as session:
        job = Job(client_id=client_id, service_type="AC repair", urgency="routine", callback_number="+15551234567")
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    fake = FakeSMS()
    monkeypatch.setattr(app_module, "sms_channel", fake)

    response = test_client.post(f"/clients/{client_id}/jobs/{job_id}/complete", follow_redirects=False)

    assert response.status_code == 303
    assert len(fake.sent) == 1
    assert "https://g.page/r/test" in fake.sent[0]["body"]
    with Session(test_engine) as session:
        completed = session.get(Job, job_id)
    assert completed.completed_at is not None


def test_mark_job_done_twice_only_sends_review_sms_once(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)
    test_client.post(f"/clients/{client_id}/review-link", data={"review_link": "https://g.page/r/test"})

    with Session(test_engine) as session:
        job = Job(client_id=client_id, service_type="AC repair", urgency="routine", callback_number="+15551234567")
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    fake = FakeSMS()
    monkeypatch.setattr(app_module, "sms_channel", fake)

    test_client.post(f"/clients/{client_id}/jobs/{job_id}/complete", follow_redirects=False)
    second = test_client.post(f"/clients/{client_id}/jobs/{job_id}/complete", follow_redirects=False)

    assert second.status_code == 303
    assert len(fake.sent) == 1  # re-posting an already-completed job must not re-send


def test_mark_job_done_without_review_link_sends_nothing(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)

    with Session(test_engine) as session:
        job = Job(client_id=client_id, service_type="AC repair", urgency="routine", callback_number="+15551234567")
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    fake = FakeSMS()
    monkeypatch.setattr(app_module, "sms_channel", fake)

    response = test_client.post(f"/clients/{client_id}/jobs/{job_id}/complete", follow_redirects=False)

    assert response.status_code == 303
    assert fake.sent == []
    with Session(test_engine) as session:
        completed = session.get(Job, job_id)
    assert completed.completed_at is not None


def test_mark_job_done_still_completes_when_sms_send_fails(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)
    test_client.post(f"/clients/{client_id}/review-link", data={"review_link": "https://g.page/r/test"})

    with Session(test_engine) as session:
        job = Job(client_id=client_id, service_type="AC repair", urgency="routine", callback_number="+15551234567")
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    monkeypatch.setattr(app_module, "sms_channel", ExplodingSMS())

    response = test_client.post(f"/clients/{client_id}/jobs/{job_id}/complete", follow_redirects=False)

    assert response.status_code == 303
    with Session(test_engine) as session:
        completed = session.get(Job, job_id)
    assert completed.completed_at is not None


def test_client_detail_shows_mark_done_then_completed_badge(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)

    with Session(test_engine) as session:
        job = Job(client_id=client_id, service_type="AC repair", urgency="routine", callback_number="+1")
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    before = test_client.get(f"/clients/{client_id}")
    assert "Mark done" in before.text

    monkeypatch.setattr(app_module, "sms_channel", FakeSMS())
    test_client.post(f"/clients/{client_id}/jobs/{job_id}/complete")

    after = test_client.get(f"/clients/{client_id}")
    assert "Completed" in after.text


def test_inbound_sms_routes_active_referral_reply(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)

    with Session(test_engine) as session:
        job = Job(
            client_id=client_id, service_type="AC repair", urgency="routine",
            customer_name="Mike", callback_number="+15551112222",
            completed_at=datetime.utcnow() - timedelta(days=5),
            referral_sent_at=datetime.utcnow(),
        )
        session.add(job)
        session.commit()

    monkeypatch.setattr(
        referral_service,
        "agent",
        StubAgent({
            "reply": "Thanks, we'll reach out to them!",
            "jobs": [],
            "new_messages": [],
            "pending_tool_call": {"name": "record_referral", "input": {"referred_name": "Sarah"}},
        }),
    )

    test_client = TestClient(app_module.app)
    response = test_client.post(
        "/webhook/sms",
        data={"From": "+15551112222", "To": "+15559990000", "Body": "my friend Sarah needs this"},
    )

    assert response.status_code == 200
    assert "Thanks, we'll reach out to them!" in response.text


def test_inbound_sms_prioritizes_active_recovery_over_referral(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)
    test_client.post(
        f"/clients/{client_id}/recovery/new",
        data={"face": "quote", "name": "June quotes", "customers_raw": "+15551112222,Mike,AC install,8000"},
    )
    with Session(test_engine) as session:
        recovery_job = session.exec(select(RecoveryJob)).first()
        recovery_job.last_sent_day = 1
        session.add(recovery_job)
        referral_job = Job(
            client_id=client_id, service_type="AC repair", urgency="routine",
            callback_number="+15551112222",
            completed_at=datetime.utcnow() - timedelta(days=5),
            referral_sent_at=datetime.utcnow(),
        )
        session.add(referral_job)
        session.commit()

    monkeypatch.setattr(
        recovery_service,
        "agent",
        StubAgent({
            "reply": "",
            "jobs": [],
            "new_messages": [],
            "pending_tool_call": {"name": "record_response", "input": {"intent": "interested"}},
        }),
    )

    response = test_client.post(
        "/webhook/sms",
        data={"From": "+15551112222", "To": "+15559990000", "Body": "Yes!"},
    )

    assert response.status_code == 200
    assert "1)" in response.text  # Recovery's slot-offer reply wins, not the referral handler


def test_set_referral_incentive_saves_and_shows_on_client_detail(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)
    test_client = TestClient(app_module.app)

    response = test_client.post(
        f"/clients/{client_id}/referral-incentive",
        data={"referral_incentive": "$25 off your next service"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    detail = test_client.get(f"/clients/{client_id}")
    assert "$25 off your next service" in detail.text
    assert "Lead-gen" in detail.text


def test_client_detail_shows_captured_referral_leads(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    client_id = make_client(test_engine)

    with Session(test_engine) as session:
        job = Job(client_id=client_id, service_type="AC repair", urgency="routine", callback_number="+1")
        session.add(job)
        session.commit()
        session.refresh(job)
        session.add(ReferralLead(
            client_id=client_id, source_job_id=job.id, asker_phone="+1",
            referred_name="Sarah", referred_phone="+15559998888",
            raw_reply_text="my friend Sarah, 555-998-8888",
        ))
        session.commit()

    test_client = TestClient(app_module.app)
    response = test_client.get(f"/clients/{client_id}")

    assert "Sarah" in response.text
    assert "+15559998888" in response.text
