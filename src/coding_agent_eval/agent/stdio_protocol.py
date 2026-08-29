"""The closed JSONL wire contract for an external agent process."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Literal

from jsonschema import Draft202012Validator

from coding_agent_eval.agent.protocol import TerminationReason
from coding_agent_eval.schemas.loader import load_schema

PROTOCOL = "cae-agent-stdio"
PROTOCOL_VERSION = "1.0.0"
MAX_MESSAGE_BYTES = 2 * 1024 * 1024


class StdioProtocolError(ValueError):
    """A stable, operator-visible reason an external-agent message was rejected."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class InitializedMessage:
    agent_name: str
    agent_version: str
    agent_model: str


@dataclass(frozen=True)
class StepMessage:
    kind: Literal["tool_call", "stop"]
    tool_name: str | None
    arguments: dict[str, Any]
    reason: TerminationReason | None
    usage: dict[str, Any]


def _schema_error(message: str) -> StdioProtocolError:
    return StdioProtocolError("schema_violation", f"schema violation: {message}")


def _validator() -> Draft202012Validator:
    return Draft202012Validator(load_schema("agent-stdio-message"))


def _validate_schema(message: Any) -> dict[str, Any]:
    errors = sorted(_validator().iter_errors(message), key=lambda error: error.json_path)
    if errors:
        raise _schema_error(errors[0].message)
    if not isinstance(message, dict):  # Guard the type narrowing promised by the schema.
        raise _schema_error("message must be an object")
    return message


def _check_finite_usage(usage: dict[str, Any]) -> None:
    for field, value in usage.items():
        if isinstance(value, float) and not math.isfinite(value):
            raise _schema_error(f"usage.{field} must be finite")


def _decode(line: bytes) -> dict[str, Any]:
    if len(line) > MAX_MESSAGE_BYTES:
        raise StdioProtocolError("message_too_large", "message is too large")
    if not line.endswith(b"\n") or line.endswith(b"\n\n") or b"\n" in line[:-1]:
        raise StdioProtocolError("invalid_json", "message must have exactly one terminating LF")
    try:
        text = line[:-1].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StdioProtocolError("invalid_utf8", "message is not valid UTF-8") from exc
    try:
        raw: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StdioProtocolError("invalid_json", "message is not valid JSON") from exc
    return _validate_schema(raw)


def _encode(
    request_id: int,
    message_type: Literal["initialize", "next-step"],
    payload: dict[str, Any],
) -> bytes:
    message = {
        "protocol": PROTOCOL,
        "version": PROTOCOL_VERSION,
        "id": request_id,
        "type": message_type,
        "payload": payload,
    }
    _validate_schema(message)
    encoded_text = json.dumps(message, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    encoded = encoded_text.encode("utf-8") + b"\n"
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise StdioProtocolError("message_too_large", "message is too large")
    return encoded


def encode_initialize(request_id: int, payload: dict[str, Any]) -> bytes:
    """Encode one host-to-child initialization request."""
    return _encode(request_id, "initialize", payload)


def encode_next_step(request_id: int, payload: dict[str, Any]) -> bytes:
    """Encode one host-to-child next-step request."""
    return _encode(request_id, "next-step", payload)


def decode_initialized(
    line: bytes,
    *,
    request_id: int,
    expected_identity: tuple[str, str, str],
) -> InitializedMessage:
    """Decode the child's handshake and bind it to operator-declared identity."""
    message = _decode(line)
    if message["type"] != "initialized":
        raise _schema_error("expected an initialized message")
    if message["id"] != request_id:
        raise StdioProtocolError("wrong_request_id", "message has the wrong request id")
    agent = message["payload"]["agent"]
    identity = (agent["name"], agent["version"], agent["model"])
    if identity != expected_identity:
        raise StdioProtocolError(
            "identity_mismatch", "agent identity does not match operator declaration"
        )
    return InitializedMessage(
        agent_name=identity[0], agent_version=identity[1], agent_model=identity[2]
    )


def decode_step(line: bytes, *, request_id: int) -> StepMessage:
    """Decode one child decision after the host has issued ``next-step``."""
    message = _decode(line)
    if message["type"] != "step":
        raise _schema_error("expected a step message")
    if message["id"] != request_id:
        raise StdioProtocolError("wrong_request_id", "message has the wrong request id")
    payload = message["payload"]
    usage = dict(payload.get("usage", {}))
    _check_finite_usage(usage)
    if payload["kind"] == "tool_call":
        return StepMessage(
            kind="tool_call",
            tool_name=payload["tool_name"],
            arguments=dict(payload["arguments"]),
            reason=None,
            usage=usage,
        )
    return StepMessage(
        kind="stop",
        tool_name=None,
        arguments={},
        reason=TerminationReason(payload["reason"]),
        usage=usage,
    )
