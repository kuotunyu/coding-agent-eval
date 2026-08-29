"""Closed JSONL contract for externally supplied agent processes."""

from __future__ import annotations

import json
import math
from typing import Any

import pytest

from coding_agent_eval.agent.protocol import TerminationReason
from coding_agent_eval.agent.stdio_protocol import (
    MAX_MESSAGE_BYTES,
    PROTOCOL,
    PROTOCOL_VERSION,
    InitializedMessage,
    StdioProtocolError,
    StepMessage,
    decode_initialized,
    decode_step,
    encode_initialize,
    encode_next_step,
)


def wire(request_id: int, message_type: str, payload: dict[str, Any]) -> bytes:
    """Build one independently-derived canonical JSONL wire message."""
    message = {
        "protocol": PROTOCOL,
        "version": PROTOCOL_VERSION,
        "id": request_id,
        "type": message_type,
        "payload": payload,
    }
    return json.dumps(message, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    ) + b"\n"


def step_wire(request_id: int, payload: dict[str, Any]) -> bytes:
    return wire(request_id, "step", payload)


def test_encoders_emit_canonical_lf_terminated_messages() -> None:
    """Changing envelope construction or JSON formatting must break this wire contract."""
    assert encode_initialize(0, {}) == (
        b'{"id":0,"payload":{},"protocol":"cae-agent-stdio","type":"initialize","version":"1.0.0"}\n'
    )
    assert encode_next_step(1, {}) == (
        b'{"id":1,"payload":{},"protocol":"cae-agent-stdio","type":"next-step","version":"1.0.0"}\n'
    )


def test_initialized_decodes_matching_operator_identity() -> None:
    """Removing identity extraction or matching would allow a different agent to run."""
    line = wire(
        0,
        "initialized",
        {"agent": {"name": "external", "version": "2.3", "model": "script"}},
    )

    assert decode_initialized(
        line,
        request_id=0,
        expected_identity=("external", "2.3", "script"),
    ) == InitializedMessage(agent_name="external", agent_version="2.3", agent_model="script")


def test_initialized_identity_must_match_operator_declaration() -> None:
    line = wire(
        0,
        "initialized",
        {"agent": {"name": "other", "version": "1", "model": "script"}},
    )
    with pytest.raises(StdioProtocolError, match="identity") as error:
        decode_initialized(line, request_id=0, expected_identity=("expected", "1", "script"))

    assert error.value.code == "identity_mismatch"


def test_step_decodes_closed_tool_call_and_stop_branches() -> None:
    """Dropping branch-specific fields must not turn a tool call into an ambiguous step."""
    tool_call = decode_step(
        step_wire(
            1,
            {
                "kind": "tool_call",
                "tool_name": "read_file",
                "arguments": {"path": "src/demo.py"},
                "usage": {"total_tokens": 4, "estimated_cost_usd": 0.0},
            },
        ),
        request_id=1,
    )
    stop = decode_step(
        step_wire(2, {"kind": "stop", "reason": "completed", "usage": {}}), request_id=2
    )

    assert tool_call == StepMessage(
        kind="tool_call",
        tool_name="read_file",
        arguments={"path": "src/demo.py"},
        reason=None,
        usage={"total_tokens": 4, "estimated_cost_usd": 0.0},
    )
    assert stop == StepMessage(
        kind="stop",
        tool_name=None,
        arguments={},
        reason=TerminationReason.COMPLETED,
        usage={},
    )


@pytest.mark.parametrize("reason", ["adapter_error", "provider_error", "step_exhausted"])
def test_child_cannot_claim_host_owned_stop_reason(reason: str) -> None:
    with pytest.raises(StdioProtocolError, match="schema") as error:
        decode_step(step_wire(1, {"kind": "stop", "reason": reason, "usage": {}}), request_id=1)

    assert error.value.code == "schema_violation"


def test_unknown_nested_field_is_rejected() -> None:
    payload = {"kind": "tool_call", "tool_name": "read_file", "arguments": {}, "extra": 1}
    with pytest.raises(StdioProtocolError, match="schema") as error:
        decode_step(step_wire(1, payload), request_id=1)

    assert error.value.code == "schema_violation"


@pytest.mark.parametrize(
    ("field", "value"),
    [("protocol", "other"), ("version", "9.9.9"), ("type", "initialized")],
)
def test_step_requires_exact_protocol_version_and_type(field: str, value: str) -> None:
    """Relaxing any envelope discriminator would admit a different protocol message."""
    message = json.loads(step_wire(3, {"kind": "stop", "reason": "completed", "usage": {}}))
    message[field] = value
    line = json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"

    with pytest.raises(StdioProtocolError, match="schema") as error:
        decode_step(line, request_id=3)

    assert error.value.code == "schema_violation"


def test_step_requires_the_expected_request_id() -> None:
    with pytest.raises(StdioProtocolError, match="request id") as error:
        decode_step(
            step_wire(4, {"kind": "stop", "reason": "completed", "usage": {}}), request_id=3
        )

    assert error.value.code == "wrong_request_id"


def test_tool_call_arguments_must_be_an_object() -> None:
    with pytest.raises(StdioProtocolError, match="schema") as error:
        decode_step(
            step_wire(
                1,
                {
                    "kind": "tool_call",
                    "tool_name": "read_file",
                    "arguments": ["src/demo.py"],
                    "usage": {},
                },
            ),
            request_id=1,
        )

    assert error.value.code == "schema_violation"


@pytest.mark.parametrize("value", [-1, math.nan])
def test_usage_rejects_negative_and_non_finite_numbers(value: float) -> None:
    """Removing numeric validation could let an external agent erase host accounting."""
    line = step_wire(
        1,
        {"kind": "stop", "reason": "completed", "usage": {"estimated_cost_usd": value}},
    )

    with pytest.raises(StdioProtocolError, match="schema") as error:
        decode_step(line, request_id=1)

    assert error.value.code == "schema_violation"


def test_invalid_utf8_is_rejected_before_json_decoding() -> None:
    with pytest.raises(StdioProtocolError, match="UTF-8") as error:
        decode_step(b"\xff\n", request_id=1)

    assert error.value.code == "invalid_utf8"


@pytest.mark.parametrize("line", [b"{}", b"{}\n\n", b"{} trailing\n"])
def test_decoder_rejects_missing_multiple_or_non_whitespace_trailing_data(line: bytes) -> None:
    with pytest.raises(StdioProtocolError):
        decode_step(line, request_id=1)


def test_decoder_rejects_a_message_over_the_byte_limit() -> None:
    with pytest.raises(StdioProtocolError, match="too large") as error:
        decode_step(b" " * MAX_MESSAGE_BYTES + b"\n", request_id=1)

    assert error.value.code == "message_too_large"
