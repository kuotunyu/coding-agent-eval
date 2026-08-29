"""Persistent, bounded JSONL adapter for externally supplied agent processes."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Literal, NoReturn

from coding_agent_eval.agent.protocol import (
    AdapterFailure,
    AdapterWallclockExceeded,
    Observation,
    Step,
    ToolInvocation,
)
from coding_agent_eval.agent.stdio_protocol import (
    StdioProtocolError,
    decode_initialized,
    decode_step,
    encode_initialize,
    encode_next_step,
)
from coding_agent_eval.runconfig import StdioRunConfiguration

_STDERR_TAIL_BYTES = 8192
_CAPABILITIES = {
    "incremental_observations": True,
    "one_tool_call_per_step": True,
    "host_executes_tools": True,
}


@dataclass(frozen=True)
class _StdoutLine:
    content: bytes
    received_at: float


@dataclass(frozen=True)
class _ReaderFailure:
    stream_name: str
    exception: Exception


@dataclass
class _WriteProgress:
    bytes_written: int = 0
    complete: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, count: int) -> None:
        with self._lock:
            self.bytes_written += count

    def mark_complete(self) -> None:
        with self._lock:
            self.complete = True

    def status(self) -> Literal["complete", "partial"]:
        with self._lock:
            return "complete" if self.complete else "partial"


class StdioAgentAdapter:
    """Own one external agent process for its complete evaluation run."""

    def __init__(
        self,
        configuration: StdioRunConfiguration,
        *,
        instructions: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._configuration = configuration
        self._instructions = instructions
        self._clock = clock
        self.name = configuration.agent_name
        self.version = configuration.agent_version
        self.model = configuration.agent_model
        self._process: subprocess.Popen[bytes] | None = None
        self._request_id = 0
        self._transcript: tuple[Observation, ...] = ()
        self._started_at: float | None = None
        self._closed = False
        self._reader_events: queue.Queue[_StdoutLine | _ReaderFailure] = queue.Queue(maxsize=1)
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._writer_threads: list[threading.Thread] = []
        self._reader_stop = threading.Event()
        self._stderr_tail = bytearray()
        self._stderr_lock = threading.Lock()
        self._temporary_directory = tempfile.TemporaryDirectory(prefix="cae-agent-")
        self.child_cwd = Path(self._temporary_directory.name)
        self.child_pid: int | None = None
        self.observed_child_pids: set[int] = set()

    def __enter__(self) -> StdioAgentAdapter:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def poll(self) -> int | None:
        """Expose child liveness without exposing the Popen handle."""
        if self._process is None:
            return None
        return self._process.poll()

    def _start(self) -> None:
        if self._closed:
            raise AdapterFailure("adapter_closed", "initialize", "adapter is already closed")
        if self._process is not None:
            return
        self._started_at = self._clock()
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            process = subprocess.Popen(
                self._configuration.command,
                shell=False,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.child_cwd,
                env=self._configuration.child_environment(),
                creationflags=creationflags,
                bufsize=0,
            )
        except OSError as exc:
            raise AdapterFailure(
                "child_start_failed",
                "initialize",
                "external agent process could not be started",
                detail={"exception": type(exc).__name__},
            ) from exc
        self._process = process
        self.child_pid = process.pid
        self.observed_child_pids.add(process.pid)
        assert process.stdout is not None
        assert process.stderr is not None
        self._stdout_thread = threading.Thread(
            target=self._drain_stdout,
            args=(process.stdout,),
            daemon=True,
            name="cae-stdio-stdout",
        )
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            args=(process.stderr,),
            daemon=True,
            name="cae-stdio-stderr",
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _drain_stdout(self, stream: BinaryIO) -> None:
        limit = self._configuration.max_message_bytes + 1
        while not self._reader_stop.is_set():
            try:
                line = stream.readline(limit)
            except Exception as exc:
                if self._reader_stop.is_set() and isinstance(exc, (OSError, ValueError)):
                    return
                self._publish_reader_event(_ReaderFailure("stdout", exc))
                return
            received_at = self._clock()
            self._publish_reader_event(_StdoutLine(line, received_at))
            if self._reader_stop.is_set():
                return
            if not line:
                return

    def _drain_stderr(self, stream: BinaryIO) -> None:
        while not self._reader_stop.is_set():
            try:
                chunk = stream.read(4096)
            except Exception as exc:
                if self._reader_stop.is_set() and isinstance(exc, (OSError, ValueError)):
                    return
                self._publish_reader_event(_ReaderFailure("stderr", exc))
                return
            if not chunk:
                return
            with self._stderr_lock:
                self._stderr_tail.extend(chunk)
                del self._stderr_tail[:-_STDERR_TAIL_BYTES]

    def _publish_reader_event(self, event: _StdoutLine | _ReaderFailure) -> None:
        while not self._reader_stop.is_set():
            try:
                self._reader_events.put(event, timeout=0.05)
                return
            except queue.Full:
                continue

    def _stderr_detail(self) -> dict[str, str]:
        with self._stderr_lock:
            tail = bytes(self._stderr_tail)
        if not tail:
            return {}
        return {"stderr_tail": tail.decode("utf-8", errors="replace")}

    def _remaining_wallclock(self) -> float:
        assert self._started_at is not None
        maximum = self._configuration.budget.max_wallclock_seconds
        assert maximum is not None
        return maximum - (self._clock() - self._started_at)

    def _exchange(
        self,
        request: bytes,
        *,
        phase: str,
        phase_timeout: float,
    ) -> tuple[bytes, float]:
        assert self._process is not None
        assert self._process.stdin is not None
        started = self._clock()
        phase_deadline = started + phase_timeout
        overall_deadline = self._overall_deadline()
        remaining = overall_deadline - started
        if remaining <= 0:
            self.close()
            raise AdapterWallclockExceeded(
                "wallclock_exceeded", phase, "external agent overall wallclock expired"
            )
        write_result: queue.Queue[BaseException | None] = queue.Queue(maxsize=1)
        write_progress = _WriteProgress()
        writer = threading.Thread(
            target=self._write_request,
            args=(self._process.stdin, request, write_result, write_progress),
            daemon=True,
            name="cae-stdio-writer",
        )
        self._writer_threads.append(writer)
        writer.start()
        try:
            write_error = write_result.get(timeout=min(phase_timeout, remaining))
        except queue.Empty as exc:
            self._raise_timeout(
                request,
                phase,
                started,
                phase_deadline,
                overall_deadline,
                write_progress,
                exc,
            )
        if write_error is not None:
            self._settle_reader_threads()
            raise self._child_failure(
                request,
                phase,
                started,
                write_progress,
                fallback_code="broken_pipe",
                fallback_message="child stdin closed",
            ) from write_error
        timeout = min(phase_deadline - self._clock(), self._remaining_wallclock())
        if timeout <= 0:
            self._raise_timeout(
                request,
                phase,
                started,
                phase_deadline,
                overall_deadline,
                write_progress,
                None,
            )
        try:
            event = self._reader_events.get(timeout=timeout)
        except queue.Empty as exc:
            self._raise_timeout(
                request,
                phase,
                started,
                phase_deadline,
                overall_deadline,
                write_progress,
                exc,
            )
        if isinstance(event, _ReaderFailure):
            latency = max(0.0, self._clock() - started)
            failure = AdapterFailure(
                f"{event.stream_name}_reader_failed",
                phase,
                f"external agent {event.stream_name} reader failed",
                detail={
                    **self._stderr_detail(),
                    "exception": type(event.exception).__name__,
                    "reader_error": str(event.exception),
                },
                trace=self._failure_trace(
                    request,
                    None,
                    latency,
                    f"{event.stream_name}_reader_failed",
                    write_progress.status(),
                ),
            )
            self.close()
            raise failure from event.exception
        response = event.content
        received_at = event.received_at
        if received_at >= min(phase_deadline, overall_deadline):
            self._raise_timeout(
                request,
                phase,
                started,
                phase_deadline,
                overall_deadline,
                write_progress,
                None,
            )
        latency = received_at - started
        if not response:
            self._settle_reader_threads()
            raise self._child_failure(
                request,
                phase,
                started,
                write_progress,
                fallback_code="unexpected_eof",
                fallback_message="child stdout closed",
            )
        if len(response) > self._configuration.max_message_bytes:
            raise AdapterFailure(
                "message_too_large",
                phase,
                "external agent response exceeds the configured byte limit",
                detail={**self._stderr_detail(), "response": "<oversize>"},
                trace=self._failure_trace(
                    request,
                    "<oversize>",
                    latency,
                    "message_too_large",
                    write_progress.status(),
                ),
            )
        return response, latency

    @staticmethod
    def _write_request(
        stream: BinaryIO,
        request: bytes,
        result: queue.Queue[BaseException | None],
        progress: _WriteProgress,
    ) -> None:
        try:
            remaining = memoryview(request)
            while remaining:
                written = stream.write(remaining)
                if written is None or written <= 0:
                    raise BrokenPipeError("child stdin accepted no bytes")
                progress.add(written)
                remaining = remaining[written:]
            stream.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            result.put(exc)
        else:
            progress.mark_complete()
            result.put(None)

    def _raise_timeout(
        self,
        request: bytes,
        phase: str,
        started: float,
        phase_deadline: float,
        overall_deadline: float,
        write_progress: _WriteProgress,
        cause: BaseException | None,
    ) -> NoReturn:
        self.close()
        latency = max(0.0, self._clock() - started)
        if phase_deadline <= overall_deadline:
            timeout_code = "startup_timeout" if phase == "initialize" else "step_timeout"
            failure: AdapterFailure = AdapterFailure(
                timeout_code,
                phase,
                f"external agent {phase} response timed out",
                detail=self._stderr_detail(),
                trace=self._failure_trace(
                    request,
                    None,
                    latency,
                    timeout_code,
                    write_progress.status(),
                ),
            )
        else:
            failure = AdapterWallclockExceeded(
                "wallclock_exceeded",
                phase,
                "external agent overall wallclock expired",
                trace=self._failure_trace(
                    request,
                    None,
                    latency,
                    "wallclock_exceeded",
                    write_progress.status(),
                ),
            )
        if cause is None:
            raise failure
        raise failure from cause

    def _settle_reader_threads(self) -> None:
        process = self._process
        if process is None:
            return
        try:
            process.wait(timeout=0.05)
        except subprocess.TimeoutExpired:
            return
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=0.05)

    def _overall_deadline(self) -> float:
        assert self._started_at is not None
        maximum = self._configuration.budget.max_wallclock_seconds
        assert maximum is not None
        return self._started_at + maximum

    def _child_failure(
        self,
        request: bytes,
        phase: str,
        started: float,
        write_progress: _WriteProgress,
        *,
        fallback_code: str,
        fallback_message: str,
    ) -> AdapterFailure:
        assert self._process is not None
        returncode = self._process.poll()
        detail: dict[str, Any] = self._stderr_detail()
        if returncode is not None:
            detail["returncode"] = returncode
        code = "child_exit" if returncode not in (None, 0) else fallback_code
        message = (
            "external agent process exited non-zero"
            if code == "child_exit"
            else fallback_message
        )
        trace = self._failure_trace(
            request,
            "" if fallback_code == "unexpected_eof" else None,
            max(0.0, self._clock() - started),
            code,
            write_progress.status(),
        )
        if returncode not in (None, 0):
            return AdapterFailure(
                code, phase, message, detail=detail, trace=trace
            )
        return AdapterFailure(code, phase, message, detail=detail, trace=trace)

    @staticmethod
    def _request_document(request: bytes) -> dict[str, Any]:
        value: Any = json.loads(request)
        assert isinstance(value, dict)
        return value

    def _failure_trace(
        self,
        request: bytes,
        response: Any,
        latency: float,
        finish_reason: str,
        request_write: Literal["complete", "partial"],
    ) -> dict[str, Any]:
        return {
            "request_hash": hashlib.sha256(request).hexdigest(),
            "latency_ms": max(0, int(latency * 1000)),
            "finish_reason": finish_reason,
            "request_write": request_write,
            "request_body": self._request_document(request),
            "response_body": response,
        }

    def _decode(
        self,
        request: bytes,
        response: bytes,
        latency: float,
        *,
        phase: str,
    ) -> Any:
        try:
            if phase == "initialize":
                return decode_initialized(
                    response,
                    request_id=self._request_id,
                    expected_identity=(self.name, self.version, self.model),
                )
            return decode_step(response, request_id=self._request_id)
        except StdioProtocolError as exc:
            raw_response = response.decode("utf-8", errors="replace")
            raise AdapterFailure(
                exc.code,
                phase,
                exc.message,
                detail={**self._stderr_detail(), "response": raw_response},
                trace=self._failure_trace(
                    request, raw_response, latency, exc.code, "complete"
                ),
            ) from exc

    @staticmethod
    def _observation_document(observation: Observation) -> dict[str, Any]:
        return {
            "tool_name": observation.tool_name,
            "content": observation.content,
            "is_error": observation.is_error,
        }

    def _initialize(self) -> None:
        self._start()
        request = self._bounded_request(
            lambda: encode_initialize(
                self._request_id,
                {
                    "instructions": self._instructions,
                    "capabilities": _CAPABILITIES,
                },
            ),
            phase="initialize",
        )
        response, latency = self._exchange(
            request,
            phase="initialize",
            phase_timeout=self._configuration.startup_timeout_seconds,
        )
        self._decode(request, response, latency, phase="initialize")
        self._request_id += 1

    def next_step(
        self,
        *,
        tools: Sequence[dict[str, Any]],
        transcript: Sequence[Observation],
    ) -> Step:
        """Send only newly appended observations to the persistent child."""
        if self._closed:
            raise AdapterFailure("adapter_closed", "step", "adapter is already closed")
        if self._process is None:
            self._initialize()
        current = tuple(transcript)
        if current[: len(self._transcript)] != self._transcript:
            raise AdapterFailure(
                "transcript_not_append_only",
                "step",
                "transcript changed instead of appending observations",
            )
        additions = current[len(self._transcript) :]
        if len(additions) > 1:
            raise AdapterFailure(
                "transcript_not_incremental",
                "step",
                "transcript appended more than one observation",
            )
        request = self._bounded_request(
            lambda: encode_next_step(
                self._request_id,
                {
                    "tools": list(tools),
                    "observation": (
                        None if not additions else self._observation_document(additions[0])
                    ),
                },
            ),
            phase="step",
        )
        response, latency = self._exchange(
            request,
            phase="step",
            phase_timeout=self._configuration.step_timeout_seconds,
        )
        message = self._decode(request, response, latency, phase="step")
        self._request_id += 1
        self._transcript = current
        response_document: Any = json.loads(response)
        trace = {
            "request_hash": hashlib.sha256(request).hexdigest(),
            "latency_ms": max(0, int(latency * 1000)),
            "finish_reason": message.kind,
            "request_write": "complete",
            "request_body": self._request_document(request),
            "response_body": response_document,
        }
        if message.kind == "tool_call":
            return Step(
                invocation=ToolInvocation(
                    tool_name=message.tool_name or "", arguments=message.arguments
                ),
                usage=message.usage,
                trace=trace,
            )
        return Step(stop=message.reason, usage=message.usage, trace=trace)

    def _bounded_request(self, build: Callable[[], bytes], *, phase: str) -> bytes:
        try:
            request = build()
        except StdioProtocolError as exc:
            raise AdapterFailure(exc.code, phase, exc.message) from exc
        if len(request) > self._configuration.max_message_bytes:
            raise AdapterFailure(
                "message_too_large",
                phase,
                "external agent request exceeds the configured byte limit",
            )
        return request

    def _cancel_blocked_writers(self) -> None:
        blocked = [thread for thread in self._writer_threads if thread.is_alive()]
        if not blocked or os.name != "nt":
            return
        # Windows pipe writes are synchronous kernel I/O. Closing the Python
        # stream from another thread waits on its lock, so cancel the operation
        # on the writer thread before closing stdin in the required order.
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_thread = kernel32.OpenThread
        open_thread.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
        open_thread.restype = ctypes.c_void_p
        cancel_io = kernel32.CancelSynchronousIo
        cancel_io.argtypes = (ctypes.c_void_p,)
        cancel_io.restype = ctypes.c_int
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (ctypes.c_void_p,)
        close_handle.restype = ctypes.c_int
        thread_terminate = 0x0001
        for thread in blocked:
            if thread.native_id is None:
                continue
            handle = open_thread(thread_terminate, False, thread.native_id)
            if handle:
                cancel_io(handle)
                close_handle(handle)
        for thread in blocked:
            thread.join(timeout=0.1)

    def close(self) -> None:
        """Close stdin, escalate if needed, and always reap the child."""
        if self._closed:
            return
        self._closed = True
        self._reader_stop.set()
        process = self._process
        if process is not None:
            if process.stdin is not None:
                writer_is_blocked = any(thread.is_alive() for thread in self._writer_threads)
                self._cancel_blocked_writers()
                if writer_is_blocked and os.name != "nt":
                    with suppress(OSError):
                        os.close(process.stdin.fileno())
                else:
                    with suppress(OSError):
                        process.stdin.close()
            try:
                process.wait(timeout=self._configuration.shutdown_grace_seconds)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=self._configuration.shutdown_grace_seconds)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            else:
                process.wait()
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    with suppress(OSError):
                        stream.close()
            if process.stdin is not None:
                with suppress(OSError):
                    process.stdin.close()
        while True:
            try:
                self._reader_events.get_nowait()
            except queue.Empty:
                break
        join_timeout = max(self._configuration.shutdown_grace_seconds, 0.1)
        for thread in (
            self._stdout_thread,
            self._stderr_thread,
            *self._writer_threads,
        ):
            if thread is not None:
                thread.join(timeout=join_timeout)
        self._temporary_directory.cleanup()
