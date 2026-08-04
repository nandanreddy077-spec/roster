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

Log rotation (2026-07-29, Frontdesk reliability audit): `_inbound.jsonl` is
appended to by EVERY webhook delivery forever, with no per-call boundary to
bound it naturally — unlike a per-call `{call_id}.jsonl` file, which is
deliberately NEVER rotated (it's already scoped to one call's own short
lifetime, and splitting a single call's trace across files would break the
"open one file, see the whole call" debugging workflow this module exists
for). Rotation is size-based and keeps a bounded number of numbered backups
(`_inbound.jsonl.1`, `.2`, ...) — rotating without ever deleting old backups
would just reshape unbounded growth into many files, not prevent it. It
happens strictly BETWEEN writes: `capture_unverified` opens, writes ONE
complete line, and closes the file every call (no long-lived handle), so a
record can never straddle the rotation boundary.
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

# Rotation defaults for the shared pre-auth quarantine file (_inbound.jsonl)
# ONLY — see the module docstring for why a per-call file is never rotated.
# 5 MB keeps a single file comfortably readable; 5 backups bounds total disk
# use to ~30 MB worst case rather than growing forever. Both are overridable
# per call (capture_unverified's own params), same injectable-constant
# pattern as xai_voice_adapter's MAX_CALL_DURATION_SECONDS/MAX_CALL_TOKEN_BUDGET.
MAX_INBOUND_LOG_BYTES = 5 * 1024 * 1024
MAX_INBOUND_LOG_BACKUPS = 5


def _rotate_if_needed(path: Path, max_bytes: int, max_backups: int) -> None:
    """If `path` is already at or past `max_bytes`, shift the numbered
    backup chain (path.N -> path.N+1, oldest deleted) and rename `path` to
    `path.1` — so the NEXT write always lands in a fresh, empty `path`.
    Runs before any write begins, never mid-write, so a record can never be
    split across the rotation boundary."""
    if not path.exists() or path.stat().st_size < max_bytes:
        return
    oldest = path.with_name(f"{path.name}.{max_backups}")
    if oldest.exists():
        oldest.unlink()
    for n in range(max_backups - 1, 0, -1):
        src = path.with_name(f"{path.name}.{n}")
        if src.exists():
            src.rename(path.with_name(f"{path.name}.{n + 1}"))
    path.rename(path.with_name(f"{path.name}.1"))


def _safe_name(call_id: Any) -> str:
    name = _UNSAFE_FILENAME.sub("_", str(call_id))[:128]
    return name or "unnamed"


class CallTrace:
    def __init__(self, call_id: str, capture_dir: Optional[Any] = None):
        self.call_id = call_id
        self.capture_dir = Path(capture_dir) if capture_dir else None
        self._t0 = time.monotonic()
        self.records: list[Dict[str, Any]] = []
        self.suppressed: Dict[str, int] = {}  # noisy event type -> count
        self._fh = None  # opened once and kept open for the call, not per write

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

    # Events that carry base64 AUDIO and arrive continuously (~5/sec each way).
    # One real 34-second call produced 259 `input_audio_buffer.append` records;
    # every one wrote the caller's raw voice to the capture file AND a line to
    # stderr. That is disk bloat on the volume, real signal drowned in noise,
    # and recorded customer audio sitting in plaintext logs. The TYPE is worth
    # knowing, the payload never is — nobody debugs base64 audio by eye — so
    # these are counted and summarised instead of stored.
    NOISY_EVENT_TYPES = frozenset({
        "input_audio_buffer.append",
        "response.output_audio.delta",
    })

    def event(self, event: Dict[str, Any]) -> None:
        etype = event.get("type")
        if etype in self.NOISY_EVENT_TYPES:
            self.suppressed[etype] = self.suppressed.get(etype, 0) + 1
            return
        self._write({"kind": "event", "type": etype, "raw": event})

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
        fh = self._ensure_open()
        for rec in self.records:
            fh.write(json.dumps(rec, default=str) + "\n")
        fh.flush()

    def _ensure_open(self):
        # ponytail: still a blocking write() per record (not offloaded to a
        # thread), but reusing one handle for the whole call cuts the open()
        # syscall a live call was paying on every single WS event. Move to a
        # background writer/thread if profiling shows write() itself matters.
        if self._fh is None:
            self.capture_dir.mkdir(parents=True, exist_ok=True)
            self._fh = open(self._capture_file(), "a")
        return self._fh

    def close(self) -> None:
        """Write the suppressed-event tally, then release the handle. The counts
        are the part of a noisy event worth keeping: "audio flowed both ways for
        the whole call" is answerable from them, without storing the audio."""
        if self.suppressed:
            self._write({"kind": "stage", "stage": "audio_stream_summary",
                         **self.suppressed})
            self.suppressed = {}
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def _write(self, rec: Dict[str, Any]) -> None:
        rec = {"call_id": self.call_id, "t_ms": self._elapsed_ms(), **rec}
        self.records.append(rec)
        if self.capture_dir is not None:
            fh = self._ensure_open()
            fh.write(json.dumps(rec, default=str) + "\n")
            fh.flush()
        label = rec.get("stage") or rec.get("type") or rec["kind"]
        print(f"[call {self.call_id}] +{rec['t_ms']}ms {rec['kind']}: {label}", file=sys.stderr)

    @staticmethod
    def capture_unverified(capture_dir: Optional[Any], headers: Dict[str, Any], body: bytes,
                           max_bytes: int = MAX_INBOUND_LOG_BYTES,
                           max_backups: int = MAX_INBOUND_LOG_BACKUPS) -> None:
        """Append a raw inbound webhook to a single fixed quarantine file,
        BEFORE authentication. Uses a constant filename (never the caller's
        call_id), so an unauthenticated request cannot control the write path —
        this preserves the "capture every inbound webhook" ground-truth log
        without the pre-auth path-traversal / arbitrary-file-append exposure of
        keying the file on attacker-supplied input.

        Rotated by size (see module docstring) before this write begins, so
        the record below always lands in a fresh file if rotation just fired
        — never split across the old and new file."""
        if capture_dir is None:
            return
        capture_dir = Path(capture_dir)
        capture_dir.mkdir(parents=True, exist_ok=True)
        path = capture_dir / "_inbound.jsonl"
        _rotate_if_needed(path, max_bytes, max_backups)
        body_text = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else str(body)
        rec = {"kind": "unverified_webhook", "ts": time.time(),
               "headers": dict(headers), "body": body_text}
        with open(path, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
