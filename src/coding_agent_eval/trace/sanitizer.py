"""The only route from private evidence to a published artifact (spec §10.5).

Because it is the only route, its failure mode decides what a leak costs.
Best-effort redaction fails open: the file is still written, and whatever the
rules missed is now public. This one fails closed — it raises, writes no output
file, and leaves no partial file behind.

The last part carries more weight than it appears to. A half-written artifact
left after a rejection is exactly what a later step picks up and publishes, so
the whole projection is built and checked in memory, written to a temporary file
beside the destination, and only then moved into place. Nothing partial ever
exists at the destination path, and an artifact published earlier survives a
failed re-run untouched.

Email is rejected here without exception, including the project's own published
address. A tracked file necessarily contains that address; a run artifact has no
reason to contain any (spec §10.8).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from coding_agent_eval.hygiene.policy import PUBLIC_ARTIFACT_POLICY
from coding_agent_eval.trace.allowlist import UnknownFieldError
from coding_agent_eval.trace.public_trace import project_record


class SanitizerError(RuntimeError):
    """The artifact was refused. Nothing was written."""


def _render(records: list[dict[str, Any]]) -> str:
    return "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for record in records
    )


def _project_all(raw_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        return [project_record(raw) for raw in raw_events]
    except UnknownFieldError as exc:
        raise SanitizerError(
            f"refusing to publish: {exc}. An unclassified field could leak or could vanish "
            "from the record, and neither would be noticed."
        ) from exc
    except (KeyError, TypeError) as exc:
        raise SanitizerError(f"refusing to publish: malformed raw event ({exc})") from exc


def _iter_strings(value: Any, path: str = "") -> list[tuple[str, str]]:
    """Every string in a projected structure, with the path that reached it."""
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, dict):
        return [
            item
            for key, child in value.items()
            for item in _iter_strings(child, f"{path}.{key}" if path else str(key))
        ]
    if isinstance(value, list):
        return [
            item
            for index, child in enumerate(value)
            for item in _iter_strings(child, f"{path}[{index}]")
        ]
    return []


def _scan(records: list[dict[str, Any]]) -> None:
    """Scan the values, not the rendered JSON.

    Scanning the serialised form misses two whole classes of leak: a Windows
    path arrives as `C:\\\\Users\\\\...` once backslashes are escaped, and a
    multi-line secret collapses onto one line as `\\n`, so neither pattern
    matches. Walking the values sees the content as it will be read back.
    """
    findings: list[str] = []
    for record in records:
        for path, text in _iter_strings(record):
            for finding in PUBLIC_ARTIFACT_POLICY.findings(text):
                findings.append(f"seq {record.get('seq')} at {path}: {finding.rule}")

    if findings:
        raise SanitizerError(
            f"refusing to publish: {len(findings)} leak finding(s) in the projected artifact "
            f"({'; '.join(findings[:5])})"
        )


def sanitize_events(raw_events: list[dict[str, Any]], output_path: Path) -> None:
    """Project, verify, and atomically write. Raises without writing on any problem."""
    records = _project_all(raw_events)
    _scan(records)
    rendered = _render(records)

    # Written beside the destination and moved into place, so the destination
    # only ever holds a complete, checked artifact.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        dir=output_path.parent, prefix=f".{output_path.name}.", suffix=".tmp"
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(output_path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def sanitize_run(raw_events: list[dict[str, Any]], run_dir: Path) -> Path:
    """Write `<run_dir>/trace.jsonl` from raw events. Returns the artifact path."""
    output = run_dir / "trace.jsonl"
    sanitize_events(raw_events, output)
    return output
