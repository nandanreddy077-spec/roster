"""Revenue Recovery: standalone outbound SMS agent that chases unsold quotes and
lapsed customers. Independent of the Frontdesk agent — Recovery owns its own
intake, reply handling, and booking, so a client can run Recovery without ever
enabling Frontdesk.
"""
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlmodel import Session, select

from channels import get_channel
from db_models import Client, RecoveryCampaign, RecoveryJob
from engine import AgentEngine

agent = AgentEngine()
sms_channel = get_channel()

ACTIVE_STATUSES = ("pending", "awaiting_slot")


def create_campaign(
    session: Session,
    client: Client,
    face: str,
    name: str,
    customers: List[Dict[str, Any]],
    template_overrides: Optional[Dict[str, str]] = None,
) -> RecoveryCampaign:
    """`customers` is a list of dicts with keys: phone, name (optional),
    service_type, estimate_amount (optional), days_since (optional)."""
    campaign = RecoveryCampaign(
        client_id=client.id,
        face=face,
        name=name,
        customer_list_json=json.dumps(customers),
        template_overrides_json=json.dumps(template_overrides or {}),
    )
    session.add(campaign)
    session.commit()
    session.refresh(campaign)

    for c in customers:
        session.add(
            RecoveryJob(
                campaign_id=campaign.id,
                client_id=client.id,
                customer_phone=c["phone"],
                customer_name=c.get("name"),
                service_type=c["service_type"],
                estimate_amount=c.get("estimate_amount"),
                days_since=c.get("days_since"),
            )
        )
    session.commit()
    return campaign


def find_active_recovery_job(session: Session, client_id: int, customer_phone: str) -> Optional[RecoveryJob]:
    return session.exec(
        select(RecoveryJob).where(
            RecoveryJob.client_id == client_id,
            RecoveryJob.customer_phone == customer_phone,
            RecoveryJob.current_status.in_(ACTIVE_STATUSES),
        )
    ).first()
