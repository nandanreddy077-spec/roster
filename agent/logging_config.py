"""Structured logging: one JSON object per line, on stdout.

Every production-path log call before this was a bare print() — readable in
a terminal, unsearchable everywhere else. Railway captures stdout as logs but
has no idea "business 7" and "job 42" are the same incident unless the line
itself says so in a shape a log search can filter on. This module changes
the SHAPE of what already gets logged; it does not add new observability by
itself — see the next commit for the call sites that actually carry
business_id/customer_phone/job_id as structured fields now.

Deliberately stdlib only: `logging` + `json`, no structlog, no
python-json-logger. Railway (like every platform log collector) reads
whatever a process writes to stdout — a JSON Formatter is the entire
integration surface needed, and pulling in a library for eleven lines of
formatting is exactly the kind of dependency the ladder exists to skip.
"""

import json
import logging
import sys
from datetime import datetime, timezone

# Every attribute a stock LogRecord carries. Diffed against a real record's
# __dict__ to find only what the CALLER attached via `extra=` — the
# business_id/job_id/customer_phone fields that make a line searchable.
_STANDARD_RECORD_KEYS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)))


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = {k: v for k, v in vars(record).items() if k not in _STANDARD_RECORD_KEYS}
        payload.update(extra)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """Attach the JSON handler to the root logger, once. Safe to call from
    every process entrypoint (app.py's import-time boot AND recovery_tick.py's
    __main__, for a standalone `python recovery_tick.py` run) — a second call
    is a no-op rather than a duplicated handler and doubled log lines."""
    root = logging.getLogger()
    if any(
        isinstance(h, logging.StreamHandler) and isinstance(h.formatter, JSONFormatter)
        for h in root.handlers
    ):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root.addHandler(handler)
    root.setLevel(level)
