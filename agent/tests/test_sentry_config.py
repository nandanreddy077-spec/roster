"""Sentry stays off with no DSN (dev/CI never talk to it), and turning it on
must not require touching any of the ~40 logger.error() call sites converted
in the structured-logging commit -- LoggingIntegration is what makes that
true, so this pins that it's actually wired up.

Monkeypatches sentry_sdk.init rather than calling the real thing: the real
SDK keeps a process-global client, and letting tests actually initialize it
would leak state across the suite and risk a flush attempt at interpreter
exit. Asserting on the call sentry_sdk.init() receives is what actually
proves this module's own logic is correct.
"""

import logging

from sentry_config import configure_sentry


def test_no_dsn_means_no_sentry(monkeypatch):
    calls = []
    monkeypatch.setattr("sentry_sdk.init", lambda **kw: calls.append(kw))

    result = configure_sentry({})

    assert result is False
    assert calls == []


def test_a_dsn_configures_sentry(monkeypatch):
    calls = []
    monkeypatch.setattr("sentry_sdk.init", lambda **kw: calls.append(kw))

    result = configure_sentry({"SENTRY_DSN": "https://public@example.ingest.sentry.io/1"})

    assert result is True
    assert len(calls) == 1
    assert calls[0]["dsn"] == "https://public@example.ingest.sentry.io/1"


def test_environment_tag_comes_from_roster_env(monkeypatch):
    calls = []
    monkeypatch.setattr("sentry_sdk.init", lambda **kw: calls.append(kw))

    configure_sentry(
        {"SENTRY_DSN": "https://public@example.ingest.sentry.io/1", "ROSTER_ENV": "production"}
    )

    assert calls[0]["environment"] == "production"


def test_environment_defaults_to_development(monkeypatch):
    calls = []
    monkeypatch.setattr("sentry_sdk.init", lambda **kw: calls.append(kw))

    configure_sentry({"SENTRY_DSN": "https://public@example.ingest.sentry.io/1"})

    assert calls[0]["environment"] == "development"


def test_error_level_logs_become_events_via_the_logging_integration(monkeypatch):
    """The whole payoff: every logger.error() from Step 6 needs no code
    change to reach Sentry. Pinned by checking the integration's own
    thresholds rather than trusting it's configured correctly by inspection."""
    calls = []
    monkeypatch.setattr("sentry_sdk.init", lambda **kw: calls.append(kw))

    configure_sentry({"SENTRY_DSN": "https://public@example.ingest.sentry.io/1"})

    integrations = calls[0]["integrations"]
    logging_integrations = [i for i in integrations if type(i).__name__ == "LoggingIntegration"]
    assert len(logging_integrations) == 1
    integration = logging_integrations[0]
    assert integration._handler.level == logging.ERROR
    assert integration._breadcrumb_handler.level == logging.INFO


def test_no_apm_tracing_by_default(monkeypatch):
    """Error tracking only. Tracing is a separate cost this milestone never
    asked for, and turning it on later should be a deliberate, visible
    change, not a default nobody chose."""
    calls = []
    monkeypatch.setattr("sentry_sdk.init", lambda **kw: calls.append(kw))

    configure_sentry({"SENTRY_DSN": "https://public@example.ingest.sentry.io/1"})

    assert calls[0]["traces_sample_rate"] == 0.0


def test_default_pii_capture_is_off(monkeypatch):
    calls = []
    monkeypatch.setattr("sentry_sdk.init", lambda **kw: calls.append(kw))

    configure_sentry({"SENTRY_DSN": "https://public@example.ingest.sentry.io/1"})

    assert calls[0]["send_default_pii"] is False
