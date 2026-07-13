import json

from db_models import Business


def test_client_to_config_includes_answer_mode(session):
    client = Business(
        business_name="Test Co",
        trade="HVAC",
        services_json=json.dumps(["AC repair"]),
        hours="9-5",
        pricing_faq="n/a",
        escalation_phone="+15550000000",
        answer_mode="primary",
    )
    session.add(client)
    session.commit()
    session.refresh(client)

    config = client.to_config()

    assert config.answer_mode == "primary"


def test_client_answer_mode_defaults_to_backup(session):
    client = Business(
        business_name="Test Co",
        trade="HVAC",
        services_json=json.dumps(["AC repair"]),
        hours="9-5",
        pricing_faq="n/a",
        escalation_phone="+15550000000",
    )
    session.add(client)
    session.commit()
    session.refresh(client)

    assert client.answer_mode == "backup"


def test_client_to_config_includes_tone(session):
    client = Business(
        business_name="Test Co",
        trade="HVAC",
        services_json=json.dumps(["AC repair"]),
        hours="9-5",
        pricing_faq="n/a",
        escalation_phone="+15550000000",
        tone="upbeat and casual",
    )
    session.add(client)
    session.commit()
    session.refresh(client)

    config = client.to_config()

    assert config.tone == "upbeat and casual"
