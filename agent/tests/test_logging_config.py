"""JSONFormatter: every log line is one JSON object, and whatever a caller
attaches via extra= (business_id, job_id, customer_phone) survives as a
searchable field rather than being buried in an interpolated string."""

import json
import logging

from logging_config import JSONFormatter, configure_logging


def _record(msg="hello", **extra) -> logging.LogRecord:
    record = logging.LogRecord(
        name="roster.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    for k, v in extra.items():
        setattr(record, k, v)
    return record


def test_a_plain_message_becomes_one_json_object():
    line = JSONFormatter().format(_record("job booked"))
    payload = json.loads(line)  # raises if it's not valid JSON
    assert payload["message"] == "job booked"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "roster.test"
    assert "ts" in payload


def test_extra_fields_survive_as_structured_data_not_string_interpolation():
    """The whole point: business_id=7 must be a real JSON field a log search
    can filter on, not text sitting inside `message`."""
    line = JSONFormatter().format(_record("booked", business_id=7, job_id=42))
    payload = json.loads(line)
    assert payload["business_id"] == 7
    assert payload["job_id"] == 42
    assert "business_id" not in payload["message"]


def test_customer_phone_and_error_both_survive():
    line = JSONFormatter().format(
        _record("send failed", business_id=1, customer_phone="+15551234567", error="boom")
    )
    payload = json.loads(line)
    assert payload["customer_phone"] == "+15551234567"
    assert payload["error"] == "boom"


def test_exception_info_is_captured():
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        import sys

        record = logging.LogRecord(
            name="roster.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="failed",
            args=(),
            exc_info=sys.exc_info(),
        )
    payload = json.loads(JSONFormatter().format(record))
    assert "RuntimeError: boom" in payload["exc_info"]


def test_configure_logging_is_idempotent():
    """Called from both app.py's boot and recovery_tick.py's __main__ -- a
    second call must not double every log line.

    Doesn't assert on the root logger's total handler count: app.py already
    calls configure_logging() at import time, and pytest caches that import
    across the whole session, so an earlier test file may have already added
    the one JSONFormatter handler this asserts on. What must hold regardless
    of import order is that there is never more than one."""
    configure_logging()
    configure_logging()
    configure_logging()

    root = logging.getLogger()
    json_handlers = [h for h in root.handlers if isinstance(h.formatter, JSONFormatter)]
    assert len(json_handlers) == 1
