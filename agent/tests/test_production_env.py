"""Production must not boot half-configured in silence: every missing
credential that silently degrades a money path gets named at startup."""
from app import session_cookie_kwargs, warn_missing_production_env


def test_production_lists_every_missing_critical_var():
    missing = warn_missing_production_env({"ROSTER_ENV": "production"})
    for var in ("ANTHROPIC_API_KEY", "DATABASE_URL", "TWILIO_ACCOUNT_SID",
                "TWILIO_AUTH_TOKEN", "XAI_API_KEY", "PUBLIC_BASE_URL"):
        assert var in missing


def test_production_with_everything_set_is_quiet():
    env = {"ROSTER_ENV": "production", "ANTHROPIC_API_KEY": "x",
           "DATABASE_URL": "postgresql://u@h/db", "TWILIO_ACCOUNT_SID": "x",
           "TWILIO_AUTH_TOKEN": "x", "XAI_API_KEY": "x",
           "PUBLIC_BASE_URL": "https://x.com"}
    assert warn_missing_production_env(env) == []


def test_dev_mode_never_warns():
    assert warn_missing_production_env({}) == []


def test_session_cookie_is_secure_in_production():
    kwargs = session_cookie_kwargs({"ROSTER_ENV": "production"})
    assert kwargs["https_only"] is True      # Secure flag: no cookie over plaintext HTTP
    assert kwargs["same_site"] == "lax"       # cross-site POSTs don't carry the cookie


def test_session_cookie_not_secure_in_dev():
    # http://localhost dev has no TLS, so Secure must be off or sign-in breaks.
    assert session_cookie_kwargs({})["https_only"] is False
