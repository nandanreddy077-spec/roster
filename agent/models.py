from dataclasses import dataclass
from typing import List


@dataclass
class ClientConfig:
    client_id: str
    business_name: str
    trade: str
    services: List[str]
    hours: str
    pricing_faq: str
    escalation_phone: str
