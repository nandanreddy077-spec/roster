"""Voice provisioning ordering — the constraint that bricked +16187473488.

xAI hands back a number's webhook signing secret exactly once at registration:
there is no read-back endpoint, and re-registering the same number answers 409.
Attaching that number to a Twilio SIP trunk, by contrast, is an ordinary
retryable call. All three call sites used to persist the secret AFTER the trunk
work, so a failure in the retryable step destroyed the irreplaceable one and
left the number permanently unusable for voice.

These tests pin the ordering, the idempotent resume, and the visibility of the
failure. What they cannot prove is covered in test_voice_loop_integration.py
(the call loop) and, finally, only by a real phone call.
"""

import pytest
from sqlmodel import Session

import provisioning
from db_models import Business
from provisioning import ProvisioningError, provision_voice

SECRET = "whsec_dGVzdHNlY3JldHRlc3RzZWNyZXR0ZXN0c2VjcmV0"


def _client(session, **overrides):
    fields = dict(
        business_name="Ridgeline HVAC",
        trade="hvac",
        services_json="[]",
        hours="9-5",
        inbound_number="+15125557777",
        twilio_number_sid="PN123",
    )
    fields.update(overrides)
    c = Business(**fields)
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


class _Calls:
    """Records what provisioning actually invoked, in order."""

    def __init__(self, register=None, attach=None):
        self.order = []
        self._register = register
        self._attach = attach

    def register(self, phone_number):
        self.order.append("register")
        if self._register:
            raise self._register
        return {"signing_secret": SECRET, "xai_phone_number": phone_number}

    def attach(self, sid, phone_number):
        self.order.append("attach")
        if self._attach:
            raise self._attach


@pytest.fixture
def calls(monkeypatch):
    def _install(register=None, attach=None):
        c = _Calls(register=register, attach=attach)
        monkeypatch.setattr(provisioning, "register_number_with_xai", c.register)
        monkeypatch.setattr(provisioning, "attach_number_to_xai_trunk", c.attach)
        return c

    return _install


# ---- the regression: a retryable failure must not destroy the secret -------


def test_a_failed_trunk_attach_still_persists_the_once_only_signing_secret(test_engine, calls):
    """THE bug. Trunk attachment is retryable; the signing secret is not. If a
    trunk failure loses the secret, the number can never do voice again."""
    calls(attach=ProvisioningError("trunk attach failed"))
    with Session(test_engine) as session:
        client = _client(session, email="brick@test.io")
        error = provision_voice(session, client)

        assert error is not None
        session.refresh(client)
        assert client.xai_signing_secret == SECRET, "secret was destroyed by a retryable failure"
        assert client.xai_phone_number == "+15125557777"


def test_the_secret_is_committed_before_the_trunk_call_is_even_attempted(
    test_engine, calls, monkeypatch
):
    """Ordering, proven from inside the retryable step: by the time attach runs,
    the secret must already be durable in a SEPARATE session — not merely
    assigned on the in-memory object and committed afterwards."""
    seen = {}
    calls()

    def attach_that_inspects_the_db(sid, phone_number):
        with Session(test_engine) as fresh:
            seen["secret_on_disk"] = fresh.get(Business, seen["id"]).xai_signing_secret
        raise ProvisioningError("boom")

    monkeypatch.setattr(provisioning, "attach_number_to_xai_trunk", attach_that_inspects_the_db)

    with Session(test_engine) as session:
        client = _client(session, email="order@test.io")
        seen["id"] = client.id
        provision_voice(session, client)

    assert seen["secret_on_disk"] == SECRET, "secret was not durable before the retryable step"


def test_a_retry_after_a_failed_attach_does_not_re_register(test_engine, calls):
    """Re-registering would 409 and is unnecessary — the secret is already
    stored, so a retry resumes at the trunk step."""
    c = calls(attach=ProvisioningError("trunk attach failed"))
    with Session(test_engine) as session:
        client = _client(session, email="resume@test.io")
        provision_voice(session, client)
        assert c.order == ["register", "attach"]

        c2 = calls()  # both succeed this time
        assert provision_voice(session, client) is None
        assert c2.order == ["attach"], "re-registered a number that already had a secret"


# ---- failure is recorded, not silent --------------------------------------


def test_a_failure_is_recorded_on_the_business_row(test_engine, calls):
    calls(attach=ProvisioningError("trunk attach failed"))
    with Session(test_engine) as session:
        client = _client(session, email="visible@test.io")
        provision_voice(session, client)
        session.refresh(client)
        assert "trunk attach failed" in client.voice_provisioning_error


def test_a_later_success_clears_the_recorded_failure(test_engine, calls):
    calls(attach=ProvisioningError("trunk attach failed"))
    with Session(test_engine) as session:
        client = _client(session, email="clear@test.io")
        provision_voice(session, client)
        assert client.voice_provisioning_error is not None

        calls()
        assert provision_voice(session, client) is None
        session.refresh(client)
        assert client.voice_provisioning_error is None


def test_a_registration_failure_leaves_no_half_state(test_engine, calls):
    """If registration itself fails there is no secret to keep — but the number
    must not be marked as voice-capable either."""
    c = calls(register=ProvisioningError("XAI_API_KEY must be set"))
    with Session(test_engine) as session:
        client = _client(session, email="nokey@test.io")
        error = provision_voice(session, client)

        assert "XAI_API_KEY" in error
        session.refresh(client)
        assert client.xai_signing_secret is None
        assert client.xai_phone_number is None
        assert c.order == ["register"], "attempted trunk work without a registration"


def test_an_unexpected_exception_is_still_recorded_not_raised(test_engine, calls):
    """Signup must never 500 on a provisioning bug — SMS-only is a working
    Frontdesk, so anything unexpected degrades rather than crashes."""
    calls(attach=RuntimeError("some twilio sdk surprise"))
    with Session(test_engine) as session:
        client = _client(session, email="surprise@test.io")
        error = provision_voice(session, client)
        assert "some twilio sdk surprise" in error
        session.refresh(client)
        assert client.xai_signing_secret == SECRET


# ---- guards ----------------------------------------------------------------


def test_no_twilio_number_yet_is_reported_not_attempted(test_engine, calls):
    c = calls()
    with Session(test_engine) as session:
        client = _client(
            session, email="nonum@test.io", inbound_number=None, twilio_number_sid=None
        )
        error = provision_voice(session, client)
        assert "No purchased Twilio number" in error
        assert c.order == []


def test_success_returns_none_and_wires_both_fields(test_engine, calls):
    c = calls()
    with Session(test_engine) as session:
        client = _client(session, email="ok@test.io")
        assert provision_voice(session, client) is None
        session.refresh(client)
        assert client.xai_phone_number == "+15125557777"
        assert client.xai_signing_secret == SECRET
        assert client.voice_provisioning_error is None
        assert c.order == ["register", "attach"]


# ---- activation degrades to SMS-only, never crashes ------------------------


def test_activation_completes_sms_only_when_voice_provisioning_fails(test_engine, monkeypatch):
    import activation

    monkeypatch.setattr(
        activation,
        "buy_twilio_number",
        lambda *a, **k: {"phone_number": "+15125550000", "sid": "PN999"},
    )
    monkeypatch.setattr(activation, "provision_voice", lambda session, client: "xAI is down")

    with Session(test_engine) as session:
        client = _client(
            session, email="degrade@test.io", inbound_number=None, twilio_number_sid=None
        )
        activation.activate_frontdesk(session, client)
        session.refresh(client)

        assert client.frontdesk_live is True  # SMS Frontdesk is fully working
        assert client.inbound_number == "+15125550000"
        assert client.xai_signing_secret is None  # voice simply isn't wired yet


def test_the_purchased_number_is_saved_even_if_everything_after_it_fails(test_engine, monkeypatch):
    """A Twilio number is billable the moment it's bought — losing track of one
    means paying for a number nothing knows about."""
    import activation

    monkeypatch.setattr(
        activation,
        "buy_twilio_number",
        lambda *a, **k: {"phone_number": "+15125550001", "sid": "PN1000"},
    )

    def explode(session, client):
        raise RuntimeError("catastrophic")

    monkeypatch.setattr(activation, "provision_voice", explode)

    with Session(test_engine) as session:
        client = _client(
            session, email="billable@test.io", inbound_number=None, twilio_number_sid=None
        )
        activation.activate_frontdesk(session, client)

        with Session(test_engine) as fresh:
            row = fresh.get(Business, client.id)
            assert row.inbound_number == "+15125550001"
            assert row.twilio_number_sid == "PN1000"


# ---- xAI's real response shape, confirmed against a live registration ------


def test_the_real_xai_response_shape_yields_the_secret():
    """The exact payload xAI returned on 2026-08-04. `dispatchSigningSecret`
    matched none of the names originally guessed from the docs, and that miss
    burned a registration — the secret is returned only once."""
    from provisioning import _extract_signing_secret

    payload = {
        "phoneNumber": {"phoneNumberId": "phone_abc", "phoneNumber": "+16187473488"},
        "webhook": {"dispatchSigningSecret": "whsec_realone", "webhookId": "webhook_xyz"},
    }
    assert _extract_signing_secret(payload) == "whsec_realone"


def test_a_renamed_secret_field_still_resolves():
    """Matching on shape, not an exact name, so the next rename doesn't cost
    another phone number."""
    from provisioning import _extract_signing_secret

    for key in ("dispatch_signing_secret", "signingSecret", "webhook_signing_secret"):
        assert _extract_signing_secret({"webhook": {key: "whsec_x"}}) == "whsec_x"
    assert _extract_signing_secret({"signing_secret": "whsec_top"}) == "whsec_top"


def test_a_response_with_no_secret_still_fails_loud():
    """Never store None — an unverifiable webhook fails silently forever."""
    from provisioning import _extract_signing_secret

    assert _extract_signing_secret({"webhook": {"webhookId": "webhook_only"}}) is None
    assert _extract_signing_secret({"phoneNumber": {"phoneNumber": "+1"}}) is None


def test_a_non_string_secret_field_is_not_mistaken_for_the_secret():
    from provisioning import _extract_signing_secret

    assert _extract_signing_secret({"webhook": {"hasSecret": True, "secretCount": 1}}) is None
