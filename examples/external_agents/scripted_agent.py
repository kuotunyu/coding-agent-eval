"""A minimal offline external agent for ``cae run --adapter stdio-jsonl``.

It deliberately makes no diagnosis. The example is an executable protocol
reference: inspect the supplied tree once, then complete with no findings.
``write_findings`` is omitted because that tool requires a non-empty list.
"""

from __future__ import annotations

import json
import sys
from typing import Any


def receive() -> dict[str, Any] | None:
    """Read one host message, stopping cleanly if the host closes stdin."""
    line = sys.stdin.readline()
    if not line:
        return None
    message = json.loads(line)
    if not isinstance(message, dict):
        raise ValueError("host message must be an object")
    return message


def send(request_id: int, message_type: str, payload: dict[str, Any]) -> None:
    """Emit one compact JSONL protocol response."""
    message = {
        "protocol": "cae-agent-stdio",
        "version": "1.0.0",
        "id": request_id,
        "type": message_type,
        "payload": payload,
    }
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main() -> int:
    """Handshake, inspect the root directory, and complete without findings."""
    initialize = receive()
    if initialize is None:
        return 0
    send(
        int(initialize["id"]),
        "initialized",
        {
            "agent": {
                "name": "example-scripted-agent",
                "version": "1.0.0",
                "model": "deterministic-script",
            }
        },
    )

    inspect = receive()
    if inspect is None:
        return 0
    send(
        int(inspect["id"]),
        "step",
        {"kind": "tool_call", "tool_name": "list_directory", "arguments": {"path": "."}},
    )

    result = receive()
    if result is None:
        return 0
    send(int(result["id"]), "step", {"kind": "stop", "reason": "completed"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
