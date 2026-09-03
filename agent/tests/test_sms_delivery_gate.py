"""SMS "configured" is not "deliverable" (docs/PRODUCTION_READINESS.md P1-5).

A number being wired up does not mean texts reach US mobiles — that needs the
business's own A2P 10DLC campaign approved by the carriers. Until then carriers
filter the traffic SILENTLY: the send succeeds, Twilio reports success, nobody
receives it. So proactive sends are HELD in `pending_campaign`, not fired into
the void. Voice and owner alerts are unaffected.
"""

from datetime import datetime, timedelta

import app as app_module
import db as db_module
import portal as portal_module
from channels import sms_deliverable
from conftest import DASH_AUTH, provisioned_business
from db_models import (
    SMS_ACTIVE,
    SMS_NOT_CONFIGURED,
    SMS_PENDING_CAMPAIGN,
    Business,
    RecoveryCampaign,
    RecoveryJob,
    WebhookDelivery,
)
from fastapi.testclient import TestClient
from sqlmodel import Session, select


class _Bag:
    def __init__(self, status):
        self.sms_delivery_status = status


def test_sms_deliverable_holds_only_pending_campaign():
    assert sms_deliverable(_Bag(SMS_PENDING_CAMPAIGN)) is False
    assert sms_deliverable(_Bag(SMS_ACTIVE)) is True
    assert sms_deliverable(_Bag(SMS_NOT_CONFIGURED)) is True
    assert sms_deliverable(_Bag(None)) is True  # unknown → don't second-guess


def _wire(monkeypatch, test_engine):
    monkeypatch.setattr(app_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(portal_module, "engine", test_engine)


def test_provision_number_moves_a_new_business_to_pending_campaign(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    monkeypatch.setattr(
        app_module,
        "buy_twilio_number",
        lambda area_code=None: {"phone_number": "+15125550133", "sid": "PN3"},
    )
    monkeypatch.setattr(app_module, "provision_voice", lambda *a, **k: None)

    bid = provisioned_business(test_engine, provisioning_unlocked_at=datetime.utcnow())
    TestClient(app_module.app).post(
        f"/clients/{bid}/provision-number",
        data={"area_code": "512"},
        headers=DASH_AUTH,
        follow_redirects=False,
    )
    with Session(test_engine) as s:
        assert s.get(Business, bid).sms_delivery_status == SMS_PENDING_CAMPAIGN


def test_founder_marks_sms_active_and_rejects_an_unknown_status(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    bid = provisioned_business(test_engine, sms_delivery_status=SMS_PENDING_CAMPAIGN)
    client = TestClient(app_module.app)

    r = client.post(
        f"/clients/{bid}/sms-delivery-status",
        data={"status": "banana"},
        headers=DASH_AUTH,
        follow_redirects=False,
    )
    assert "sms_status_error" in r.headers["location"]
    with Session(test_engine) as s:
        assert s.get(Business, bid).sms_delivery_status == SMS_PENDING_CAMPAIGN

    client.post(
        f"/clients/{bid}/sms-delivery-status",
        data={"status": "active"},
        headers=DASH_AUTH,
        follow_redirects=False,
    )
    with Session(test_engine) as s:
        assert s.get(Business, bid).sms_delivery_status == SMS_ACTIVE


def _recovery_setup(session, status):
    biz = Business(business_name="Kestrel", trade="HVAC", inbound_number="+15125550100",
                   sms_delivery_status=status)
    session.add(biz)
    session.commit()
    session.refresh(biz)
    from deployment import deploy_role

    deploy_role(session, biz.id, "quote_chaser")
    campaign = RecoveryCampaign(
        business_id=biz.id, face="quote", name="c",
        customer_list_json="[]", started_at=datetime.utcnow() - timedelta(days=2),
    )
    session.add(campaign)
    session.commit()
    session.refresh(campaign)
    rj = RecoveryJob(
        campaign_id=campaign.id, business_id=biz.id, customer_phone="+15125559999",
        service_type="AC repair", current_status="pending",
    )
    session.add(rj)
    session.commit()
    session.refresh(rj)
    return biz, rj


def test_recovery_tick_holds_a_send_while_the_campaign_is_pending(session, monkeypatch):
    import recovery_service

    sent = []
    monkeypatch.setattr(recovery_service.sms_channel, "send", lambda **kw: sent.append(kw))
    _recovery_setup(session, SMS_PENDING_CAMPAIGN)

    recovery_service.tick(session)

    assert sent == []
    # The day was NOT claimed — it's held, not marked done.
    rj = session.exec(select(RecoveryJob)).first()
    assert rj.last_sent_day is None


def test_recovery_tick_sends_once_the_campaign_is_active(session, monkeypatch):
    import recovery_service

    sent = []
    monkeypatch.setattr(recovery_service.sms_channel, "send", lambda **kw: sent.append(kw))
    _recovery_setup(session, SMS_ACTIVE)

    recovery_service.tick(session)

    assert len(sent) == 1


def test_missed_call_text_back_is_held_while_pending(monkeypatch, test_engine):
    _wire(monkeypatch, test_engine)
    sent = []
    monkeypatch.setattr(app_module.sms_channel, "send", lambda **kw: sent.append(kw))
    monkeypatch.setattr(app_module, "_twilio_signature_ok", lambda request, form: True)

    with Session(test_engine) as s:
        biz = Business(business_name="Kestrel", trade="HVAC", inbound_number="+15125550100",
                       sms_delivery_status=SMS_PENDING_CAMPAIGN)
        s.add(biz)
        s.commit()

    r = TestClient(app_module.app).post(
        "/webhook/voice-status",
        data={"From": "+15125559999", "To": "+15125550100", "CallSid": "CA1", "CallStatus": "no-answer"},
    )
    assert r.status_code == 204
    assert sent == []
    # The delivery was still claimed (one-time side effect), so a Twilio retry
    # won't reprocess.
    with Session(test_engine) as s:
        assert s.exec(select(WebhookDelivery)).first() is not None
