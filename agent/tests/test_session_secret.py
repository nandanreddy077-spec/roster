import pytest
from app import resolve_session_secret


def test_prod_requires_secret():
    with pytest.raises(RuntimeError):
        resolve_session_secret({"ROSTER_ENV": "production"})


def test_dev_allows_fallback():
    assert resolve_session_secret({"ROSTER_ENV": "dev"})


def test_explicit_secret_always_wins():
    assert (
        resolve_session_secret({"ROSTER_ENV": "production", "SESSION_SECRET_KEY": "abc"}) == "abc"
    )
