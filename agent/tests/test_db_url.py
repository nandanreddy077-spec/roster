"""Production database selection: DATABASE_URL -> Postgres, else SQLite dev file."""

from db import resolve_engine_config


def test_sqlite_default_when_no_database_url():
    url, connect_args, kwargs = resolve_engine_config({})
    assert url.startswith("sqlite:///")
    assert connect_args == {"check_same_thread": False}


def test_database_url_used_verbatim_for_postgresql():
    url, connect_args, kwargs = resolve_engine_config(
        {"DATABASE_URL": "postgresql://u:p@host:5432/roster"}
    )
    assert url == "postgresql://u:p@host:5432/roster"
    assert connect_args == {}
    assert kwargs.get("pool_pre_ping") is True


def test_heroku_style_postgres_scheme_is_normalized():
    url, _, _ = resolve_engine_config({"DATABASE_URL": "postgres://u:p@host/db"})
    assert url == "postgresql://u:p@host/db"
