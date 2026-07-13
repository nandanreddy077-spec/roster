from db_models import Business


def test_client_can_be_created_with_only_email_and_password():
    """Signup creates a Business before any business info is known — every
    previously-required field must have a usable default."""
    client = Business(email="owner@example.com", password_hash="hashed")
    assert client.business_name == ""
    assert client.trade == ""
    assert client.services == []
    assert client.hours == ""
    assert client.pricing_faq == ""
    assert client.escalation_phone == ""


def test_client_trial_and_activation_defaults():
    client = Business(email="owner@example.com", password_hash="hashed")
    assert client.tone == "professional and friendly"
    assert client.source is None
    assert client.source_prompt_dismissed is False
    assert client.frontdesk_live is False
    assert client.activated_at is None
    assert client.trial_spend_cents == 0
    assert client.trial_cap_cents == 2000
    assert client.trial_soft_buffer_cents == 200
