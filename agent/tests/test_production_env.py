"""Production must not boot half-configured in silence: every missing
credential that silently degrades a money path gets named at startup."""

from app import (
    scheduler_interval_seconds,
    session_cookie_kwargs,
    should_start_scheduler,
    warn_missing_production_env,
)


def test_production_lists_every_missing_critical_var():
    missing = warn_missing_production_env({"ROSTER_ENV": "production"})
    for var in (
        "ANTHROPIC_API_KEY",
        "DATABASE_URL",
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
        "XAI_API_KEY",
        "PUBLIC_BASE_URL",
    ):
        assert var in missing


def test_production_with_everything_set_is_quiet():
    env = {
        "ROSTER_ENV": "production",
        "ANTHROPIC_API_KEY": "x",
        "DATABASE_URL": "postgresql://u@h/db",
        "TWILIO_ACCOUNT_SID": "x",
        "TWILIO_AUTH_TOKEN": "x",
        "XAI_API_KEY": "x",
        "PUBLIC_BASE_URL": "https://x.com",
    }
    assert warn_missing_production_env(env) == []


def test_dev_mode_never_warns():
    assert warn_missing_production_env({}) == []


def test_session_cookie_is_secure_in_production():
    kwargs = session_cookie_kwargs({"ROSTER_ENV": "production"})
    assert kwargs["https_only"] is True  # Secure flag: no cookie over plaintext HTTP
    assert kwargs["same_site"] == "lax"  # cross-site POSTs don't carry the cookie


def test_session_cookie_not_secure_in_dev():
    # http://localhost dev has no TLS, so Secure must be off or sign-in breaks.
    assert session_cookie_kwargs({})["https_only"] is False


# ---- background scheduler gate (2026-07-30) ----------------------------
# The in-process tick scheduler must only ever run in production — the test
# suite and local dev must never spin up a live loop hitting Twilio/
# Anthropic/the real database on a timer just because app.py was imported.


def test_scheduler_starts_in_production():
    assert should_start_scheduler({"ROSTER_ENV": "production"}) is True


def test_scheduler_does_not_start_in_dev():
    assert should_start_scheduler({}) is False


def test_scheduler_does_not_start_when_roster_env_is_anything_else():
    assert should_start_scheduler({"ROSTER_ENV": "staging"}) is False


def test_scheduler_interval_defaults_to_one_hour():
    assert scheduler_interval_seconds({}) == 3600


def test_scheduler_interval_is_configurable():
    assert scheduler_interval_seconds({"TICK_INTERVAL_SECONDS": "900"}) == 900


def test_scheduler_interval_ignores_a_malformed_override():
    """A typo'd env var must fall back to the safe default, never crash boot."""
    assert scheduler_interval_seconds({"TICK_INTERVAL_SECONDS": "not-a-number"}) == 3600
