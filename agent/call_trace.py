"""Per-call instrumentation for the live-voice loop.

The voice path crosses four provider boundaries (Twilio SIP -> xAI webhook ->
xAI realtime WS -> our tool execution), and the hardest bugs live *at* those
boundaries, not inside the model. So every call gets a CallTrace that records,
with a millisecond offset from call receipt:

  - the raw inbound webhook (headers + body),
  - every WebSocket event (including unexpected types),
  - each pipeline stage (ws_connected, first_ai_response, tool_invoked,
    job_persisted, owner_notified, call_completed, ...).

Records are kept in memory (so tests can assert on them) and, once capture is
enabled, appended as JSONL to `{capture_dir}/{call_id}.jsonl` so an
authenticated call leaves a complete, replayable ground-truth log. A one-line
summary of each record also goes to stderr for live tailing.

Two capture safety rules matter here: (1) the per-call file is only opened via
`enable_capture()` *after* the inbound webhook is signature-verified, so an
unauthenticated caller never triggers a disk write keyed on their input; and
(2) `capture_unverified()` records the raw pre-auth webhook to a single fixed
filename (`_inbound.jsonl`), never one derived from the caller-supplied
call_id, so the pre-auth capture can't be steered into a path traversal.
"""
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

# call_id is validated at the webhook trust boundary, but the capture file is a
# filesystem path built from it, so we defensively re-sanitize here as well: a
# bad call_id must never let a write escape the capture directory (CWE-22).
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9_-]")


def _safe_name(call_id: Any) -> str:
    name = _UNSAFE_FILENAME.sub("_", str(call_id))[:128]
    return name or "unnamed"


class CallTrace:
    def __init__(self, call_id: str, capture_dir: Optional[Any] = None):
        self.call_id = call_id
        self.capture_dir = Path(capture_dir) if capture_dir else None
        self._t0 = time.monotonic()
        self.records: list[Dict[str, Any]] = []

    def _elapsed_ms(self) -> float:
        return round((time.monotonic() - self._t0) * 1000, 1)

    def _capture_file(self) -> Path:
        assert self.capture_dir is not None
        base = self.capture_dir.resolve()
        candidate = (self.capture_dir / f"{_safe_name(self.call_id)}.jsonl").resolve()
        # Belt-and-suspenders: even if the sanitizer above is ever weakened, the
        # write must stay directly under capture_dir.
        if candidate.parent != base:
            raise ValueError(f"capture path escapes capture_dir: {candidate}")
        return candidate

    def stage(self, name: str, **extra: Any) -> None:
        self._write({"kind": "stage", "stage": name, **extra})

    def event(self, event: Dict[str, Any]) -> None:
        self._write({"kind": "event", "type": event.get("type"), "raw": event})

    def webhook(self, headers: Dict[str, Any], body: bytes) -> None:
        body_text = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else str(body)
        self._write({"kind": "webhook", "headers": dict(headers), "body": body_text})

    def enable_capture(self, capture_dir: Any) -> None:
        """Begin persisting this trace to `{call_id}.jsonl`, flushing everything
        buffered so far. Call this only AFTER the webhook is authenticated: it
        is what keeps an unauthenticated caller from creating per-call capture
        files (and, together with call_id validation, from steering the write
        path via call_id)."""
        self.capture_dir = Path(capture_dir)
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        path = self._capture_file()
        with open(path, "a") as f:
            for rec in self.records:
                f.write(json.dumps(rec, default=str) + "\n")

    def _write(self, rec: Dict[str, Any]) -> None:
        rec = {"call_id": self.call_id, "t_ms": self._elapsed_ms(), **rec}
        self.records.append(rec)
        if self.capture_dir is not None:
            self.capture_dir.mkdir(parents=True, exist_ok=True)
            with open(self._capture_file(), "a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        label = rec.get("stage") or rec.get("type") or rec["kind"]
        print(f"[call {self.call_id}] +{rec['t_ms']}ms {rec['kind']}: {label}", file=sys.stderr)

    @staticmethod
    def capture_unverified(capture_dir: Optional[Any], headers: Dict[str, Any], body: bytes) -> None:
        """Append a raw inbound webhook to a single fixed quarantine file,
        BEFORE authentication. Uses a constant filename (never the caller's
        call_id), so an unauthenticated request cannot control the write path —
        this preserves the "capture every inbound webhook" ground-truth log
        without the pre-auth path-traversal / arbitrary-file-append exposure of
        keying the file on attacker-supplied input."""
        if capture_dir is None:
            return
        capture_dir = Path(capture_dir)
        capture_dir.mkdir(parents=True, exist_ok=True)
        body_text = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else str(body)
        rec = {"kind": "unverified_webhook", "ts": time.time(),
               "headers": dict(headers), "body": body_text}
        with open(capture_dir / "_inbound.jsonl", "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
