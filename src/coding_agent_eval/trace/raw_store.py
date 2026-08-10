"""Private raw evidence store (design spec §10.1).

The complete record of a run, in the form it actually took: every tool call,
every response, unredacted. It is what makes a disputed result checkable, and
for the same reason it is what must never be published — it can hold third-party
source, host paths, and provider responses.

Two properties follow.

**Append-only.** A sequence number is used once and never revisited, including
across a reopened store, so the record of what happened cannot be quietly
rewritten after the fact. Events are flushed as they arrive, because a run that
crashes is precisely the run whose record matters.

**Never in version control.** The directory is gitignored, and tests assert both
that the ignore line exists and that nothing under it is tracked. Publishing is
then always a deliberate pass through the sanitizer rather than something that
happens by leaving a file where Git can see it.

Blobs are content-addressed by SHA-256. Tool outputs repeat constantly across a
run, so storing each copy would waste space, and the digest is what the public
trace carries in place of the content — which means anyone holding the original
can verify the reference.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EVENTS_FILENAME = "events.jsonl"
BLOBS_DIRNAME = "blobs"
DEFAULT_RETENTION_DAYS = 30


class RawStoreError(RuntimeError):
    """The store was asked to do something that would corrupt the record."""


class RawStore:
    """One run's private evidence."""

    def __init__(self, root: Path, *, run_id: str) -> None:
        self.root = Path(root)
        self.run_id = run_id
        self.run_dir = self.root / run_id
        self.events_path = self.run_dir / EVENTS_FILENAME
        self.blobs_dir = self.root / BLOBS_DIRNAME
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.blobs_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- events

    def _used_sequence_numbers(self) -> set[int]:
        if not self.events_path.is_file():
            return set()
        numbers: set[int] = set()
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                numbers.add(json.loads(line)["seq"])
        return numbers

    def append(self, seq: int, event: str, payload: dict[str, Any]) -> None:
        """Record one event. A reused or out-of-order sequence number is refused."""
        self.append_record(
            {
                "seq": seq,
                "ts": datetime.now(UTC).isoformat(),
                "event": event,
                "payload": payload,
            }
        )

    def append_record(self, record: dict[str, Any]) -> None:
        """Append a complete recorder event without changing its sequence or timestamp."""
        seq = record.get("seq")
        if not isinstance(seq, int):
            raise RawStoreError("a raw event sequence must be an integer")
        used = self._used_sequence_numbers()
        if seq in used:
            raise RawStoreError(
                f"sequence {seq} is already recorded for run {self.run_id}; the store is "
                "append-only so an event cannot be replaced"
            )
        if used and seq <= max(used):
            raise RawStoreError(
                f"sequence {seq} is not after the last recorded sequence {max(used)}; "
                "out-of-order writes would make the record ambiguous"
            )

        if set(record) != {"seq", "ts", "event", "payload"}:
            raise RawStoreError("a raw event must contain exactly seq, ts, event, and payload")
        if not isinstance(record["ts"], str) or not isinstance(record["event"], str):
            raise RawStoreError("a raw event timestamp and event name must be strings")
        if not isinstance(record["payload"], dict):
            raise RawStoreError("a raw event payload must be an object")
        # newline="\n" so a run recorded on Windows and one on Linux produce the
        # same bytes; the projection downstream is compared byte for byte.
        with self.events_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()

    def read_events(self) -> list[dict[str, Any]]:
        if not self.events_path.is_file():
            return []
        return [
            json.loads(line)
            for line in self.events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    # -------------------------------------------------------------- blobs

    def _blob_path(self, digest: str) -> Path:
        return self.blobs_dir / digest[:2] / digest

    def put_blob(self, content: bytes) -> str:
        """Store content, returning its SHA-256. Storing the same bytes twice is free."""
        digest = hashlib.sha256(content).hexdigest()
        path = self._blob_path(digest)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return digest

    def get_blob(self, digest: str) -> bytes:
        path = self._blob_path(digest)
        if not path.is_file():
            raise RawStoreError(f"no blob with digest {digest}")
        return path.read_bytes()

    def blob_count(self) -> int:
        return sum(1 for path in self.blobs_dir.rglob("*") if path.is_file())

    # ---------------------------------------------------------- retention

    @staticmethod
    def prune(root: Path, *, retention_days: int = DEFAULT_RETENTION_DAYS) -> list[str]:
        """Delete run directories older than the window. Returns the run ids removed.

        Blobs are left alone: they are shared across runs, and the cost of an
        orphan is disk space, whereas the cost of deleting one still referenced
        is an unverifiable trace.
        """
        root = Path(root)
        if not root.is_dir():
            return []

        cutoff = time.time() - retention_days * 86400
        removed: list[str] = []
        for entry in sorted(root.iterdir()):
            if not entry.is_dir() or entry.name == BLOBS_DIRNAME:
                continue
            if entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry)
                removed.append(entry.name)
        return removed
