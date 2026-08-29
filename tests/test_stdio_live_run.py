"""Provider-neutral live execution through an external JSONL stdio agent."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from coding_agent_eval.agent.protocol import Budget
from coding_agent_eval.agent.provider import SYSTEM_PROMPT_VERSION, render_system_prompt
from coding_agent_eval.agent.tools import model_schemas
from coding_agent_eval.fixtures.image_identity import ImageIdentityError
from coding_agent_eval.live import ExecutionMetadata, execute_stdio, write_evidence
from coding_agent_eval.runconfig import StdioRunConfiguration
from tests.conftest import REPO_ROOT

FIXTURE = REPO_ROOT / "fixtures" / "fx-taskq-py"

CAPACITY_CHILD = r"""
import json
import os
import sys

evidence = sys.argv[1]

def receive():
    line = sys.stdin.readline()
    if not line:
        return None
    message = json.loads(line)
    with open(evidence, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(message, sort_keys=True) + "\n")
    return message

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

initialize = receive()
send(initialize["id"], "initialized", {
    "agent": {"name": "capacity-probe", "version": "1.2.3", "model": "probe-model"}
})

first = receive()
send(first["id"], "step", {
    "kind": "tool_call",
    "tool_name": "list_directory",
    "arguments": {"path": "."},
})

second = receive()
send(second["id"], "step", {
    "kind": "tool_call",
    "tool_name": "write_findings",
    "arguments": {"findings": []},
})

third = receive()
sys.stderr.write("PRIVATE_STDERR_MARKER\n")
sys.stderr.flush()
send(third["id"], "step", {
    "kind": "tool_call",
    "tool_name": "read_file",
    "arguments": {"path": "src/taskq/util.py"},
})

if sys.stdin.readline() == "":
    with open(evidence + ".closed", "w", encoding="utf-8") as stream:
        stream.write(str(os.getpid()))
"""

FAILURE_CHILD = r"""
import json
import os
import sys
import time

mode = sys.argv[1]

def receive():
    line = sys.stdin.readline()
    return None if not line else json.loads(line)

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
if mode == "broken_pipe":
    os.close(sys.stdin.fileno())
    time.sleep(0.2)
send(initialize["id"], "initialized", {
    "agent": {"name": "failure-probe", "version": "1.0", "model": "probe"}
})

if mode == "broken_pipe":
    time.sleep(60)

step = receive()
if mode == "timeout":
    time.sleep(60)
elif mode == "eof":
    raise SystemExit(0)
elif mode == "nonzero":
    sys.stderr.write("PRIVATE_FAILURE_STDERR\n")
    sys.stderr.flush()
    raise SystemExit(17)
else:
    raise AssertionError(mode)
"""

SENTINEL_CHILD = r"""
from pathlib import Path
import sys

Path(sys.argv[1]).write_text("spawned", encoding="utf-8")
raise SystemExit(99)
"""


def _configuration(tmp_path: Path) -> tuple[StdioRunConfiguration, Path]:
    script = tmp_path / "PRIVATE_ARGV_CHILD.py"
    script.write_text(CAPACITY_CHILD, encoding="utf-8")
    evidence = tmp_path / "capacity.jsonl"
    configuration = StdioRunConfiguration(
        command=(sys.executable, str(script), str(evidence)),
        inherited_environment=("PRIVATE_ENVIRONMENT_NAME",),
        agent_name="capacity-probe",
        agent_version="1.2.3",
        agent_model="probe-model",
        budget=Budget(max_tool_calls=2, max_wallclock_seconds=5.0),
        startup_timeout_seconds=1.0,
        step_timeout_seconds=1.0,
        shutdown_grace_seconds=0.2,
        _preflight_environ={**os.environ, "PRIVATE_ENVIRONMENT_NAME": "PRIVATE_ENV_VALUE"},
    )
    return configuration, evidence


def _failure_configuration(tmp_path: Path, mode: str) -> StdioRunConfiguration:
    script = tmp_path / f"failure-child-{mode}.py"
    script.write_text(FAILURE_CHILD, encoding="utf-8")
    return StdioRunConfiguration(
        command=(sys.executable, str(script), mode),
        inherited_environment=(),
        agent_name="failure-probe",
        agent_version="1.0",
        agent_model="probe",
        budget=Budget(max_tool_calls=1, max_wallclock_seconds=2.0),
        startup_timeout_seconds=1.0,
        step_timeout_seconds=0.05 if mode == "timeout" else 1.0,
        shutdown_grace_seconds=0.02,
        _preflight_environ=os.environ,
    )


def _sentinel_configuration(tmp_path: Path) -> tuple[StdioRunConfiguration, Path]:
    script = tmp_path / "sentinel-child.py"
    script.write_text(SENTINEL_CHILD, encoding="utf-8")
    marker = tmp_path / "child-started.marker"
    configuration = StdioRunConfiguration(
        command=(sys.executable, str(script), str(marker)),
        inherited_environment=(),
        agent_name="sentinel",
        agent_version="1.0",
        agent_model="sentinel",
        budget=Budget(max_tool_calls=1, max_wallclock_seconds=2.0),
        _preflight_environ=os.environ,
    )
    return configuration, marker


def _public_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in _public_values(child)]
    if isinstance(value, list):
        return [text for child in value for text in _public_values(child)]
    return []


def _public_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {
            key for child in value.values() for key in _public_keys(child)
        }
    if isinstance(value, list):
        return {key for child in value for key in _public_keys(child)}
    return set()


def test_stdio_live_run_shares_evidence_and_capacity_boundaries(tmp_path: Path) -> None:
    """Breaking shared execution or either capacity transition changes real artifacts."""
    configuration, child_evidence = _configuration(tmp_path)

    run = execute_stdio(
        FIXTURE,
        configuration=configuration,
        snapshot="mutated",
        workspace=tmp_path / "work",
        raw_store_root=tmp_path / ".run-store",
        run_id="stdio-live-run",
    )

    assert isinstance(run.metadata, ExecutionMetadata)
    assert run.metadata == ExecutionMetadata(
        provider=None,
        model="probe-model",
        public_configuration=configuration.redacted(),
        private_parameters=configuration.private_parameters(),
        pricing_table_version="external-self-reported/1.0",
        transport="stdio-jsonl",
        protocol_version="1.0.0",
        usage_source="agent_reported_unverified",
        system_prompt_version=SYSTEM_PROMPT_VERSION,
    )
    requests = [json.loads(line) for line in child_evidence.read_text().splitlines()]
    assert requests[0]["payload"]["instructions"] == render_system_prompt(2)
    tool_names = [
        [tool["name"] for tool in request["payload"]["tools"]]
        for request in requests
        if request["type"] == "next_step"
    ]
    assert tool_names == [
        [tool["name"] for tool in model_schemas()],
        ["write_findings"],
        [],
    ]
    assert child_evidence.with_suffix(".jsonl.closed").is_file(), (
        "execute_stdio must close and reap the child after any harness termination"
    )
    assert run.result.termination_reason.value == "step_exhausted"

    raw_events = run.raw_store.read_events()
    event_names = [event["event"] for event in raw_events]
    assert event_names == [
        "run_header",
        "llm_call",
        "tool_call",
        "tool_result",
        "llm_call",
        "tool_call",
        "tool_result",
        "llm_call",
        "cost",
        "termination",
    ]
    assert raw_events[0]["payload"]["provider"] is None
    assert raw_events[0]["payload"]["model"] == "probe-model"
    assert raw_events[0]["payload"]["agent_adapter"] == "capacity-probe"
    assert raw_events[0]["payload"]["params"] == configuration.private_parameters()
    assert "request_body" in raw_events[1]["payload"]
    assert run.usage_total()["completeness"] == "partial"

    directory = write_evidence(run, tmp_path / "public")
    assert sorted(path.name for path in directory.iterdir()) == [
        "findings.json",
        "run.json",
        "trace.jsonl",
    ]
    public_documents = [
        json.loads((directory / "run.json").read_text(encoding="utf-8")),
        json.loads((directory / "findings.json").read_text(encoding="utf-8")),
        *[
            json.loads(line)
            for line in (directory / "trace.jsonl").read_text(encoding="utf-8").splitlines()
        ],
    ]
    keys = {key for document in public_documents for key in _public_keys(document)}
    values = [text for document in public_documents for text in _public_values(document)]
    assert not {
        "argv",
        "cwd",
        "environment",
        "inherited_environment",
        "request_body",
        "response_body",
        "stderr",
        "stderr_tail",
        "scores",
        "metrics",
        "verified_bug_recall",
        "localization_recall",
    } & keys
    assert not any("PRIVATE_" in value for value in values)
    assert json.loads((directory / "run.json").read_text())["usage"]["completeness"] == (
        "partial"
    )


@pytest.mark.parametrize(
    ("mode", "expected_code", "expected_write"),
    [
        ("timeout", "step_timeout", "complete"),
        ("eof", "unexpected_eof", "complete"),
        ("nonzero", "child_exit", "complete"),
        ("broken_pipe", "broken_pipe", "partial"),
    ],
)
def test_failed_stdio_exchange_retains_private_request_and_public_attempt_evidence(
    tmp_path: Path,
    mode: str,
    expected_code: str,
    expected_write: str,
) -> None:
    """Every attempted request remains auditable even when no valid response exists."""
    run = execute_stdio(
        FIXTURE,
        configuration=_failure_configuration(tmp_path, mode),
        snapshot="clean",
        workspace=tmp_path / f"work-{mode}",
        raw_store_root=tmp_path / ".run-store",
        run_id=f"failure-{mode}",
    )

    raw_events = run.raw_store.read_events()
    assert [event["event"] for event in raw_events] == [
        "run_header",
        "llm_call",
        "cost",
        "termination",
    ]
    attempted = raw_events[1]["payload"]
    intended_request = attempted["request_body"]
    assert intended_request["type"] == "next_step"
    assert intended_request["payload"]["observation"] is None
    encoded = json.dumps(
        intended_request,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    assert attempted["request_hash"] == hashlib.sha256(encoded).hexdigest()
    assert attempted["request_write"] == expected_write
    assert "response_body" in attempted
    assert raw_events[-1]["payload"]["adapter_error"]["code"] == expected_code

    directory = write_evidence(run, tmp_path / f"public-{mode}")
    public_events = [
        json.loads(line)
        for line in (directory / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [event["event"] for event in public_events] == [
        "run_header",
        "llm_call",
        "cost",
        "termination",
    ]
    public_attempt = public_events[1]["payload"]
    assert public_attempt["request_hash"] == attempted["request_hash"]
    assert public_attempt["request_write"] == expected_write
    assert public_attempt["interface_mode"] == "report_only"
    assert public_attempt["tools_offered"] == ["write_findings"]
    assert "request_body" not in public_attempt
    assert "response_body" not in public_attempt
    assert "PRIVATE_FAILURE_STDERR" not in json.dumps(public_events)


def test_invalid_snapshot_is_refused_before_real_stdio_child_spawn(tmp_path: Path) -> None:
    configuration, marker = _sentinel_configuration(tmp_path)

    with pytest.raises(ValueError, match="snapshot must be"):
        execute_stdio(
            FIXTURE,
            configuration=configuration,
            snapshot="invalid",
            workspace=tmp_path / "work-invalid-snapshot",
        )

    assert not marker.exists()


def test_invalid_oci_identity_is_refused_before_real_stdio_child_spawn(tmp_path: Path) -> None:
    configuration, marker = _sentinel_configuration(tmp_path)

    with pytest.raises(ImageIdentityError, match="immutable image reference"):
        execute_stdio(
            FIXTURE,
            configuration=configuration,
            snapshot="clean",
            isolate_image="invalid.example/fixture@sha256:" + "f" * 64,
            workspace=tmp_path / "work-invalid-oci",
        )

    assert not marker.exists()
