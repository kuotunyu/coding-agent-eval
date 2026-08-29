"""Persistent JSONL subprocess adapter lifecycle and failure evidence."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import coding_agent_eval.agent.stdio_adapter as stdio_adapter_module
from coding_agent_eval.agent.loop import run_agent
from coding_agent_eval.agent.protocol import (
    AdapterFailure,
    AdapterWallclockExceeded,
    Budget,
    Observation,
    Step,
    TerminationReason,
)
from coding_agent_eval.agent.stdio_adapter import StdioAgentAdapter
from coding_agent_eval.agent.tools import ToolContext, model_schemas
from coding_agent_eval.runconfig import StdioRunConfiguration

CHILD = r"""
import json
import os
import signal
import sys
import time

mode, evidence = sys.argv[1:3]

def send(request_id, message_type, payload):
    message = {
        "protocol": "cae-agent-stdio",
        "version": "1.0.0",
        "id": request_id,
        "type": message_type,
        "payload": payload,
    }
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()

def receive():
    line = sys.stdin.readline()
    if not line:
        return None
    message = json.loads(line)
    with open(evidence, "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"pid": os.getpid(), "message": message}) + "\n")
    return message

initialize = receive()
if initialize is None:
    raise SystemExit(90)

if mode == "hang_handshake":
    time.sleep(60)
elif mode == "malformed_handshake":
    sys.stdout.write("not json\n")
    sys.stdout.flush()
elif mode == "oversize_handshake":
    sys.stdout.buffer.write(b"x" * 4097 + b"\n")
    sys.stdout.buffer.flush()
elif mode == "eof_handshake":
    raise SystemExit(0)
else:
    identity = {"name": "external", "version": "2.3", "model": "script"}
    if mode == "identity_mismatch":
        identity["name"] = "other"
    send(initialize["id"], "initialized", {"agent": identity})

if mode == "late_response":
    step = receive()
    with open(evidence + ".late", "w", encoding="utf-8") as stream:
        stream.write("response is now late")
    send(step["id"], "step", {"kind": "stop", "reason": "completed"})
elif mode == "hang_after_large_second":
    step = receive()
    send(step["id"], "step", {
        "kind": "tool_call",
        "tool_name": "list_directory",
        "arguments": {"path": "."},
    })
    receive()
    with open(evidence + ".waiting", "w", encoding="utf-8") as stream:
        stream.write("both deadlines are now expired")
    time.sleep(60)
elif mode == "hang_before_read":
    time.sleep(60)
elif mode in {"hang", "step_timeout"}:
    receive()
    time.sleep(60)
elif mode == "nonzero":
    receive()
    sys.stderr.write("PRIVATE_NONZERO_STDERR\n")
    sys.stderr.flush()
    raise SystemExit(23)
elif mode == "stderr":
    receive()
    sys.stderr.write("A" * 20000 + "PRIVATE_STDERR_TAIL")
    sys.stderr.flush()
    raise SystemExit(24)
elif mode not in {
    "malformed_handshake",
    "oversize_handshake",
    "eof_handshake",
    "identity_mismatch",
}:
    step = receive()
    if mode == "wrong_id":
        send(step["id"] + 1, "step", {"kind": "stop", "reason": "completed"})
    elif mode == "malformed_step":
        sys.stdout.write("{bad json\n")
        sys.stdout.flush()
    elif mode == "oversize_step":
        sys.stdout.buffer.write(b"x" * 4097 + b"\n")
        sys.stdout.buffer.flush()
    elif mode == "eof_step":
        raise SystemExit(0)
    else:
        send(step["id"], "step", {
            "kind": "tool_call",
            "tool_name": "list_directory",
            "arguments": {"path": "."},
            "usage": {"total_tokens": 1},
        })
        second = receive()
        if second is not None:
            send(second["id"], "step", {
                "kind": "tool_call",
                "tool_name": "write_findings",
                "arguments": {"findings": []},
                "usage": {"total_tokens": 2},
            })
            third = receive()
            if third is not None:
                send(third["id"], "step", {"kind": "stop", "reason": "completed"})

if mode == "ignore_stdin":
    if os.name != "nt":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True:
        time.sleep(1)

if mode == "normal":
    if sys.stdin.readline() == "":
        with open(evidence + ".closed", "w", encoding="utf-8") as stream:
            stream.write("stdin closed")
"""


ALL_TOOLS = tuple(model_schemas())
WRITE_ONLY = tuple(tool for tool in ALL_TOOLS if tool["name"] == "write_findings")

STRICT_CONTRACT_CHILD = r"""
import json
import sys

EXPECTED_CAPABILITIES = {
    "incremental_observations": True,
    "one_tool_call_per_step": True,
    "host_executes_tools": True,
}

def receive():
    message = json.loads(sys.stdin.readline())
    assert set(message) == {"protocol", "version", "id", "type", "payload"}
    assert message["protocol"] == "cae-agent-stdio"
    assert message["version"] == "1.0.0"
    return message

def send(request_id, message_type, payload):
    message = {
        "protocol": "cae-agent-stdio",
        "version": "1.0.0",
        "id": request_id,
        "type": message_type,
        "payload": payload,
    }
    print(json.dumps(message, separators=(",", ":")), flush=True)

initialize = receive()
assert initialize["type"] == "initialize"
assert set(initialize["payload"]) == {"instructions", "capabilities"}
assert initialize["payload"]["capabilities"] == EXPECTED_CAPABILITIES
send(initialize["id"], "initialized", {
    "agent": {"name": "external", "version": "2.3", "model": "script"}
})

step = receive()
assert step["type"] == "next_step"
assert set(step["payload"]) == {"tools", "observation"}
assert step["payload"]["observation"] is None
send(step["id"], "step", {"kind": "stop", "reason": "completed"})
"""


class CloseInterruptedReader:
    """A pipe proxy that deterministically wakes a blocked read by raising on close."""

    def __init__(self, delegate: Any, operation: str) -> None:
        self._delegate = delegate
        self._operation = operation
        self.entered = threading.Event()
        self.closed = threading.Event()

    def readline(self, _limit: int = -1) -> bytes:
        assert self._operation == "readline"
        self.entered.set()
        assert self.closed.wait(timeout=2.0)
        raise OSError(9, "deterministic close interruption")

    def read(self, _size: int = -1) -> bytes:
        assert self._operation == "read"
        self.entered.set()
        assert self.closed.wait(timeout=2.0)
        raise ValueError("deterministic close interruption")

    def close(self) -> None:
        self.closed.set()
        self._delegate.close()


class UnexpectedReaderFailure:
    """A pipe proxy that injects a read failure while the adapter is running."""

    def __init__(self, delegate: Any, operation: str) -> None:
        self._delegate = delegate
        self._operation = operation

    def readline(self, _limit: int = -1) -> bytes:
        assert self._operation == "readline"
        raise OSError(5, "deterministic unexpected reader failure")

    def read(self, _size: int = -1) -> bytes:
        assert self._operation == "read"
        raise ValueError("deterministic unexpected reader failure")

    def close(self) -> None:
        self._delegate.close()


def patch_process_reader(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stream_name: str,
    wrapper_type: type[CloseInterruptedReader] | type[UnexpectedReaderFailure],
) -> list[Any]:
    """Wrap one real child pipe while retaining the real subprocess boundary."""
    real_popen = stdio_adapter_module.subprocess.Popen
    wrapped: list[Any] = []

    def open_process(*args: Any, **kwargs: Any) -> Any:
        process = real_popen(*args, **kwargs)
        delegate = getattr(process, stream_name)
        assert delegate is not None
        operation = "readline" if stream_name == "stdout" else "read"
        proxy = wrapper_type(delegate, operation)
        setattr(process, stream_name, proxy)
        wrapped.append(proxy)
        return process

    monkeypatch.setattr(stdio_adapter_module.subprocess, "Popen", open_process)
    return wrapped


def observation(tool_name: str) -> Observation:
    return Observation(tool_name=tool_name, content="result", is_error=False)


def adapter_for(
    tmp_path: Path,
    mode: str = "normal",
    *,
    wallclock: float = 5.0,
    startup_timeout: float = 1.0,
    step_timeout: float = 1.0,
    shutdown_grace: float = 0.05,
    max_message_bytes: int = 4096,
    clock: Callable[[], float] = time.monotonic,
) -> StdioAgentAdapter:
    script = tmp_path / f"child-{mode}.py"
    script.write_text(CHILD, encoding="utf-8")
    evidence = tmp_path / f"evidence-{mode}.jsonl"
    config = StdioRunConfiguration(
        command=(sys.executable, str(script), mode, str(evidence)),
        inherited_environment=(),
        agent_name="external",
        agent_version="2.3",
        agent_model="script",
        budget=Budget(max_tool_calls=10, max_wallclock_seconds=wallclock),
        startup_timeout_seconds=startup_timeout,
        step_timeout_seconds=step_timeout,
        shutdown_grace_seconds=shutdown_grace,
        max_message_bytes=max_message_bytes,
        _preflight_environ=os.environ,
    )
    return StdioAgentAdapter(
        config,
        instructions="Review the supplied tree.",
        clock=clock,
    )


def evidence_for(tmp_path: Path, mode: str) -> list[dict[str, Any]]:
    path = tmp_path / f"evidence-{mode}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def marker_clock(marker: Path, *, late_value: float) -> Callable[[], float]:
    return lambda: late_value if marker.exists() else 0.0


def test_one_process_handles_multiple_dynamic_tool_steps(tmp_path: Path) -> None:
    adapter = adapter_for(tmp_path)
    try:
        first = adapter.next_step(tools=ALL_TOOLS, transcript=[])
        second = adapter.next_step(tools=WRITE_ONLY, transcript=[observation("list_directory")])
        assert first.invocation and first.invocation.tool_name == "list_directory"
        assert second.invocation and second.invocation.tool_name == "write_findings"
        assert adapter.observed_child_pids == {adapter.child_pid}
        requests = evidence_for(tmp_path, "normal")
        assert requests[0]["message"]["payload"] == {
            "instructions": "Review the supplied tree.",
            "capabilities": {
                "incremental_observations": True,
                "one_tool_call_per_step": True,
                "host_executes_tools": True,
            },
        }
        assert requests[1]["message"]["type"] == "next_step"
        assert requests[1]["message"]["payload"]["observation"] is None
        assert requests[2]["message"]["payload"]["observation"] == {
            "content": "result",
            "is_error": False,
            "tool_name": "list_directory",
        }
        assert requests[2]["message"]["payload"]["tools"] == list(WRITE_ONLY)
    finally:
        adapter.close()


def test_independently_written_strict_child_accepts_the_shipped_host_contract(
    tmp_path: Path,
) -> None:
    """A child derived from the approved design must interoperate without shared helpers."""
    script = tmp_path / "strict-contract-child.py"
    script.write_text(STRICT_CONTRACT_CHILD, encoding="utf-8")
    configuration = StdioRunConfiguration(
        command=(sys.executable, str(script)),
        inherited_environment=(),
        agent_name="external",
        agent_version="2.3",
        agent_model="script",
        budget=Budget(max_tool_calls=1, max_wallclock_seconds=5.0),
        startup_timeout_seconds=1.0,
        step_timeout_seconds=1.0,
        shutdown_grace_seconds=0.1,
        _preflight_environ=os.environ,
    )
    adapter = StdioAgentAdapter(configuration, instructions="Review the supplied tree.")
    try:
        step = adapter.next_step(tools=ALL_TOOLS, transcript=[])
    finally:
        adapter.close()

    assert step.stop is TerminationReason.COMPLETED


def test_wrong_response_id_becomes_adapter_error_evidence(tmp_path: Path) -> None:
    adapter = adapter_for(tmp_path, "wrong_id")
    result = run_agent(adapter, context=ToolContext(root=tmp_path))
    adapter.close()

    assert result.termination_reason is TerminationReason.ADAPTER_ERROR
    termination = result.events[-1]["payload"]
    assert termination["adapter_error"] == {
        "code": "wrong_request_id",
        "phase": "step",
    }
    assert "wrong request id" in termination["adapter_error_message"]
    assert len(evidence_for(tmp_path, "wrong_id")) == 2, "a bad response must not be retried"


def test_overall_deadline_kills_a_hung_child(tmp_path: Path) -> None:
    adapter = adapter_for(tmp_path, "hang", wallclock=0.05, step_timeout=5.0)
    result = run_agent(adapter, context=ToolContext(root=tmp_path))
    adapter.close()

    assert result.termination_reason is TerminationReason.BUDGET_EXHAUSTED_WALLCLOCK
    assert "adapter_error" not in result.events[-1]["payload"]
    assert adapter.poll() is not None


def test_overall_deadline_bounds_a_blocked_stdin_write(tmp_path: Path) -> None:
    adapter = adapter_for(
        tmp_path,
        "hang_before_read",
        wallclock=0.3,
        step_timeout=5.0,
        max_message_bytes=200_000,
    )
    huge_tools = ({"name": "x", "description": "x" * 100_000, "parameters": {}},)
    started = time.monotonic()
    with pytest.raises(AdapterWallclockExceeded):
        adapter.next_step(tools=huge_tools, transcript=[])
    adapter.close()

    assert time.monotonic() - started < 2.0
    assert adapter.poll() is not None


def test_response_received_after_step_deadline_is_never_accepted(tmp_path: Path) -> None:
    marker = tmp_path / "evidence-late_response.jsonl.late"
    adapter = adapter_for(
        tmp_path,
        "late_response",
        wallclock=5.0,
        step_timeout=1.0,
        clock=marker_clock(marker, late_value=2.0),
    )
    try:
        with pytest.raises(AdapterFailure) as error:
            adapter.next_step(tools=ALL_TOOLS, transcript=[])
        assert not isinstance(error.value, AdapterWallclockExceeded)
        assert error.value.as_dict() == {"code": "step_timeout", "phase": "step"}
    finally:
        adapter.close()


def test_earlier_step_deadline_wins_when_both_are_expired(tmp_path: Path) -> None:
    marker = tmp_path / "evidence-hang_after_large_second.jsonl.waiting"
    adapter = adapter_for(
        tmp_path,
        "hang_after_large_second",
        wallclock=0.5,
        step_timeout=0.3,
        max_message_bytes=200_000,
        clock=marker_clock(marker, late_value=1.0),
    )
    huge_tools = ({"name": "x", "description": "x" * 100_000, "parameters": {}},)
    try:
        first = adapter.next_step(tools=ALL_TOOLS, transcript=[])
        assert first.invocation and first.invocation.tool_name == "list_directory"
        with pytest.raises(AdapterFailure) as error:
            adapter.next_step(
                tools=huge_tools,
                transcript=[observation("list_directory")],
            )
        assert not isinstance(error.value, AdapterWallclockExceeded)
        assert error.value.as_dict() == {"code": "step_timeout", "phase": "step"}
    finally:
        adapter.close()


def test_non_adapter_termination_payload_is_unchanged(tmp_path: Path) -> None:
    class CompletedAdapter:
        name = "completed"
        version = "1"

        def next_step(self, **_kwargs: Any) -> Step:
            return Step(stop=TerminationReason.COMPLETED)

    result = run_agent(CompletedAdapter(), context=ToolContext(root=tmp_path))
    termination = result.events[-1]["payload"]
    assert "adapter_error" not in termination
    assert "adapter_error_message" not in termination


@pytest.mark.parametrize(
    ("mode", "expected_code", "phase"),
    [
        ("identity_mismatch", "identity_mismatch", "initialize"),
        ("malformed_handshake", "invalid_json", "initialize"),
        ("oversize_handshake", "message_too_large", "initialize"),
        ("eof_handshake", "unexpected_eof", "initialize"),
        ("malformed_step", "invalid_json", "step"),
        ("oversize_step", "message_too_large", "step"),
        ("eof_step", "unexpected_eof", "step"),
        ("nonzero", "child_exit", "step"),
    ],
)
def test_protocol_and_process_failures_are_typed(
    tmp_path: Path, mode: str, expected_code: str, phase: str
) -> None:
    adapter = adapter_for(tmp_path, mode)
    try:
        with pytest.raises(AdapterFailure) as error:
            adapter.next_step(tools=ALL_TOOLS, transcript=[])
        assert error.value.as_dict() == {"code": expected_code, "phase": phase}
        assert error.value.message
    finally:
        adapter.close()


def test_step_timeout_is_adapter_error_but_overall_deadline_is_budget_exhaustion(
    tmp_path: Path,
) -> None:
    step_adapter = adapter_for(tmp_path, "step_timeout", wallclock=5.0, step_timeout=0.03)
    with pytest.raises(AdapterFailure) as step_error:
        step_adapter.next_step(tools=ALL_TOOLS, transcript=[])
    step_adapter.close()
    assert not isinstance(step_error.value, AdapterWallclockExceeded)
    assert step_error.value.as_dict() == {"code": "step_timeout", "phase": "step"}

    deadline_adapter = adapter_for(tmp_path, "hang", wallclock=0.2, step_timeout=5.0)
    with pytest.raises(AdapterWallclockExceeded) as deadline_error:
        deadline_adapter.next_step(tools=ALL_TOOLS, transcript=[])
    deadline_adapter.close()
    assert deadline_error.value.as_dict() == {
        "code": "wallclock_exceeded",
        "phase": "step",
    }


def test_startup_timeout_has_a_distinct_structural_code(tmp_path: Path) -> None:
    adapter = adapter_for(tmp_path, "hang_handshake", startup_timeout=0.03)
    try:
        with pytest.raises(AdapterFailure) as error:
            adapter.next_step(tools=ALL_TOOLS, transcript=[])
        assert error.value.as_dict() == {"code": "startup_timeout", "phase": "initialize"}
    finally:
        adapter.close()


def test_configured_message_cap_rejects_an_oversize_outbound_request(tmp_path: Path) -> None:
    adapter = adapter_for(tmp_path, max_message_bytes=1024)
    oversize_tools = ({"name": "x", "description": "x" * 2000, "parameters": {}},)
    try:
        with pytest.raises(AdapterFailure) as error:
            adapter.next_step(tools=oversize_tools, transcript=[])
        assert error.value.as_dict() == {"code": "message_too_large", "phase": "step"}
        assert len(evidence_for(tmp_path, "normal")) == 1
    finally:
        adapter.close()


def test_oversize_response_cleanup_leaves_no_worker_thread(tmp_path: Path) -> None:
    original_threads = set(threading.enumerate())
    adapter = adapter_for(tmp_path, "oversize_step")
    with pytest.raises(AdapterFailure):
        adapter.next_step(tools=ALL_TOOLS, transcript=[])
    adapter.close()

    leaked = [thread for thread in threading.enumerate() if thread not in original_threads]
    assert leaked == []


@pytest.mark.parametrize("stream_name", ["stdout", "stderr"])
@pytest.mark.filterwarnings("error::pytest.PytestUnhandledThreadExceptionWarning")
def test_close_swallows_only_expected_blocked_reader_interruptions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stream_name: str,
) -> None:
    """Closing a blocked real-process drainer must not escape through threading.excepthook."""
    wrapped = patch_process_reader(
        monkeypatch,
        stream_name=stream_name,
        wrapper_type=CloseInterruptedReader,
    )
    adapter = adapter_for(tmp_path, "hang_handshake", shutdown_grace=0.02)
    adapter._start()
    assert len(wrapped) == 1
    assert wrapped[0].entered.wait(timeout=1.0)

    adapter.close()

    assert wrapped[0].closed.is_set()
    assert adapter.poll() is not None


@pytest.mark.parametrize(
    ("stream_name", "expected_code"),
    [("stdout", "stdout_reader_failed"), ("stderr", "stderr_reader_failed")],
)
@pytest.mark.filterwarnings("error::pytest.PytestUnhandledThreadExceptionWarning")
def test_unexpected_reader_failure_is_returned_to_the_exchange(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stream_name: str,
    expected_code: str,
) -> None:
    """A crashed drainer is an adapter failure, not a misleading response timeout."""
    patch_process_reader(
        monkeypatch,
        stream_name=stream_name,
        wrapper_type=UnexpectedReaderFailure,
    )
    adapter = adapter_for(tmp_path, "hang_handshake", startup_timeout=1.0)
    try:
        with pytest.raises(AdapterFailure) as error:
            adapter.next_step(tools=ALL_TOOLS, transcript=[])
        assert error.value.as_dict() == {"code": expected_code, "phase": "initialize"}
        assert "deterministic unexpected reader failure" in error.value.private_message
    finally:
        adapter.close()


def test_stderr_is_bounded_and_remains_private(tmp_path: Path) -> None:
    adapter = adapter_for(tmp_path, "stderr")
    result = run_agent(adapter, context=ToolContext(root=tmp_path))
    adapter.close()

    termination = result.events[-1]["payload"]
    assert termination["adapter_error"] == {"code": "child_exit", "phase": "step"}
    assert "PRIVATE_STDERR_TAIL" in termination["adapter_error_message"]
    assert len(termination["adapter_error_message"]) < 10000
    assert "PRIVATE_STDERR_TAIL" not in json.dumps(termination["adapter_error"])


def test_transcript_must_only_append(tmp_path: Path) -> None:
    adapter = adapter_for(tmp_path)
    try:
        adapter.next_step(tools=ALL_TOOLS, transcript=[])
        adapter.next_step(tools=ALL_TOOLS, transcript=[observation("list_directory")])
        with pytest.raises(AdapterFailure) as error:
            adapter.next_step(tools=ALL_TOOLS, transcript=[])
        assert error.value.as_dict() == {"code": "transcript_not_append_only", "phase": "step"}
    finally:
        adapter.close()


def test_transcript_must_append_at_most_one_observation_per_step(tmp_path: Path) -> None:
    """Batching observations would violate the incremental protocol 1.0.0 contract."""
    adapter = adapter_for(tmp_path)
    try:
        adapter.next_step(tools=ALL_TOOLS, transcript=[])
        with pytest.raises(AdapterFailure) as error:
            adapter.next_step(
                tools=ALL_TOOLS,
                transcript=[observation("list_directory"), observation("read_file")],
            )
        assert error.value.as_dict() == {
            "code": "transcript_not_incremental",
            "phase": "step",
        }
    finally:
        adapter.close()


def test_normal_close_is_stdin_eof_and_close_is_idempotent(tmp_path: Path) -> None:
    adapter = adapter_for(tmp_path)
    adapter.next_step(tools=ALL_TOOLS, transcript=[])
    adapter.close()
    adapter.close()

    assert adapter.poll() is not None
    assert (tmp_path / "evidence-normal.jsonl.closed").read_text(encoding="utf-8") == "stdin closed"


def test_close_reaps_a_child_that_ignores_stdin_and_termination(tmp_path: Path) -> None:
    adapter = adapter_for(tmp_path, "ignore_stdin", shutdown_grace=0.02)
    adapter.next_step(tools=ALL_TOOLS, transcript=[])
    started = time.monotonic()
    adapter.close()

    assert time.monotonic() - started < 2.0
    assert adapter.poll() is not None


def test_child_runs_in_a_fresh_empty_temporary_directory(tmp_path: Path) -> None:
    adapter = adapter_for(tmp_path)
    try:
        adapter.next_step(tools=ALL_TOOLS, transcript=[])
        assert adapter.child_cwd != tmp_path
        assert list(adapter.child_cwd.iterdir()) == []
    finally:
        adapter.close()


def test_next_step_after_close_is_a_typed_failure(tmp_path: Path) -> None:
    adapter = adapter_for(tmp_path)
    adapter.next_step(tools=ALL_TOOLS, transcript=[])
    adapter.close()

    with pytest.raises(AdapterFailure) as error:
        adapter.next_step(tools=ALL_TOOLS, transcript=[])
    assert error.value.as_dict() == {"code": "adapter_closed", "phase": "step"}
