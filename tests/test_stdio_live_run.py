"""Provider-neutral live execution through an external JSONL stdio agent."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from coding_agent_eval.agent.protocol import Budget
from coding_agent_eval.agent.provider import SYSTEM_PROMPT_VERSION, render_system_prompt
from coding_agent_eval.agent.tools import model_schemas
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
        if request["type"] == "next-step"
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
