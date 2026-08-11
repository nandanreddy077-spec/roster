"""Error tracking. Off unless SENTRY_DSN is set — no account needed for local
dev or CI, and the test suite never talks to Sentry.

The payoff for doing structured logging (logging_config.py) first: every
logger.error(...) call already carries business_id/job_id/customer_phone/
call_id as `extra=` fields. LoggingIntegration turns each one into a Sentry
event automatically, with those same fields attached as event context — no
sentry_sdk.capture_exception() calls needed at any of the ~40 sites converted
in the previous commit, and none needed at any future one either.
"""

import logging


def configure_sentry(environ) -> bool:
    """Returns True if Sentry was actually configured, False if skipped (no
    DSN) — callers can log which happened rather than guess."""
    dsn = environ.get("SENTRY_DSN")
    if not dsn:
        return False

    import sentry_sdk
    from sentry_sdk.integrations.logging import LoggingIntegration

    sentry_sdk.init(
        dsn=dsn,
        environment=environ.get("ROSTER_ENV", "development"),
        integrations=[
            LoggingIntegration(
                level=logging.INFO,  # breadcrumbs: recent context leading up to an event
                event_level=logging.ERROR,  # an actual Sentry issue
            )
        ],
        # Error tracking only — no APM. Turning on tracing here is a separate,
        # deliberate decision with its own cost, not a default to reach for.
        traces_sample_rate=0.0,
        # Explicit, not just relying on the SDK default: this process already
        # stores customer phone numbers and names in plaintext (memory.py,
        # notifications.py) as an accepted part of running the product, so an
        # error event carrying the same business_id/customer_phone a support
        # engineer already has in the database is not a new exposure. What
        # send_default_pii additionally captures — request bodies, cookies,
        # IP addresses — is a different, broader category this stays off for.
        send_default_pii=False,
    )
    return True
