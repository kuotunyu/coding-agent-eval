"""A minimal offline external agent for ``cae run --adapter stdio-jsonl``.

It deliberately makes no diagnosis. The example is an executable protocol
reference: inspect the supplied tree once, then complete with no findings.
``write_findings`` is omitted because that tool requires a non-empty list.
"""

from __future__ import annotations

import json
import sys
from typing import Any

CAPABILITIES = {
    "incremental_observations": True,
    "one_tool_call_per_step": True,
    "host_executes_tools": True,
}


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


def require_host_message(
    message: dict[str, Any],
    *,
    message_type: str,
    payload_fields: set[str],
) -> dict[str, Any]:
    """Validate the closed host envelope without depending on harness code."""
    if set(message) != {"protocol", "version", "id", "type", "payload"}:
        raise ValueError("host envelope is not closed")
    if message["protocol"] != "cae-agent-stdio" or message["version"] != "1.0.0":
        raise ValueError("unsupported host protocol")
    if message["type"] != message_type:
        raise ValueError(f"expected host message type {message_type!r}")
    payload = message["payload"]
    if not isinstance(payload, dict) or set(payload) != payload_fields:
        raise ValueError(f"{message_type} payload is not closed")
    return payload


def main() -> int:
    """Handshake, inspect the root directory, and complete without findings."""
    initialize = receive()
    if initialize is None:
        return 0
    initialize_payload = require_host_message(
        initialize,
        message_type="initialize",
        payload_fields={"instructions", "capabilities"},
    )
    if initialize_payload["capabilities"] != CAPABILITIES:
        raise ValueError("unsupported host capabilities")
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
    inspect_payload = require_host_message(
        inspect,
        message_type="next_step",
        payload_fields={"tools", "observation"},
    )
    if inspect_payload["observation"] is not None:
        raise ValueError("the first next_step observation must be null")
    send(
        int(inspect["id"]),
        "step",
        {"kind": "tool_call", "tool_name": "list_directory", "arguments": {"path": "."}},
    )

    result = receive()
    if result is None:
        return 0
    result_payload = require_host_message(
        result,
        message_type="next_step",
        payload_fields={"tools", "observation"},
    )
    observation = result_payload["observation"]
    if not isinstance(observation, dict) or set(observation) != {
        "tool_name",
        "content",
        "is_error",
    }:
        raise ValueError("next_step observation must contain exactly one tool result")
    send(int(result["id"]), "step", {"kind": "stop", "reason": "completed"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
