"""One live provider run: fixture snapshot in, evidence out (`cae run`).

Deliberately **does not score**. A live run produces findings; turning findings
into `verified_*` numbers requires a person to adjudicate blinded finding/bug
pairs, and a command that ran an agent and printed a recall figure in one breath
would make that step look optional. Scoring is `cae evaluate` and happens after
a human ruling exists.

What it writes is evidence: private raw events first, then a public trace
projection and findings only after fail-closed sanitization. Nothing here
decides whether any finding is correct.

**The dollar budget is refused when it cannot bind.** See `runconfig`: a cost cap
enforced against placeholder pricing can never be reached, and an operator who
set one would believe they were protected.

**Isolation is opt-in and recorded.** `--isolate` runs the agent's tools inside
the measure container (spec §9.1). Without it they run in this process, and the
run header says `host_process` so a reader is never left inferring it.

**Which adapter is opt-in and recorded too.** `configuration.api` picks between
`/v1/chat/completions` (default) and `/v1/responses`. Current adapters have mock
coverage; retained live observations identify older adapter versions. The run
header names the adapter that actually built the request, not a literal.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Mapping
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

import httpx

from coding_agent_eval import BENCHMARK_VERSION, REDACTION_MANIFEST_VERSION, TRACE_SCHEMA_VERSION
from coding_agent_eval.agent.backend import LocalTree, TreeBackend
from coding_agent_eval.agent.loop import Recorder, RunResult, run_agent
from coding_agent_eval.agent.protocol import AgentAdapter, Budget, TerminationReason
from coding_agent_eval.agent.provider import (
    DEFAULT_SYSTEM_PROMPT,
    SYSTEM_PROMPT_VERSION,
    OpenAICompatibleAdapter,
)
from coding_agent_eval.agent.tools import ToolContext
from coding_agent_eval.e2e import CLEAN, MUTATED, Fixture, load_fixture
from coding_agent_eval.fixtures.image_identity import (
    ImageIdentityError,
    PreparedImageIdentity,
)
from coding_agent_eval.fixtures.patcher import apply_patch, materialise
from coding_agent_eval.runconfig import RunConfiguration, StdioRunConfiguration
from coding_agent_eval.trace.raw_store import RawStore
from coding_agent_eval.trace.sanitizer import sanitize_events


@dataclass(frozen=True)
class ExecutionMetadata:
    """Provider-neutral identity and disclosure policy for one live execution."""

    provider: str | None
    model: str | None
    public_configuration: dict[str, object]
    private_parameters: dict[str, object]
    pricing_table_version: str
    transport: str
    protocol_version: str | None
    usage_source: str
    system_prompt_version: str

    def __post_init__(self) -> None:
        """Snapshot and recursively freeze caller-owned configuration containers."""
        object.__setattr__(
            self,
            "public_configuration",
            _deep_freeze(deepcopy(self.public_configuration)),
        )
        object.__setattr__(
            self,
            "private_parameters",
            _deep_freeze(deepcopy(self.private_parameters)),
        )


@dataclass(frozen=True)
class LiveRun:
    """What one live run produced, and everything needed to interpret it."""

    run_id: str
    fixture: Fixture
    snapshot: str
    bug_ids: tuple[str, ...]
    result: RunResult
    events: list[dict[str, Any]]
    tool_backend: str
    metadata: ExecutionMetadata
    budget: Budget
    started_at: str
    adapter_name: str
    adapter_version: str
    raw_store: RawStore
    image_identity: PreparedImageIdentity | None
    system_prompt: str

    def header(self) -> dict[str, Any]:
        """The run header. Carries no key and no scores.

        `scored` is absent on purpose rather than present and null: this file is
        not a result, and a null metric invites someone to fill it in.

        `agent_adapter`/`agent_adapter_version` were a hardcoded literal until
        a second adapter existed to expose it: every live run before that would
        have reported `"openai-compatible"` even one built with
        `OpenAIResponsesAdapter`. The `AgentAdapter` protocol's own docstring
        says these two fields exist so two runs of different adapters are never
        silently comparable — that promise held for `cae fixture` output and
        broke here. Fixed by reading them off the adapter that was actually
        constructed, once, at the point `execute` builds it.
        """
        return {
            "schema_version": "0.1",
            "run_id": self.run_id,
            "started_at": self.started_at,
            "fixture_id": self.fixture.fixture_id,
            "fixture_version": self.fixture.version,
            "tree_checksum": self.fixture.manifest["clean_control"]["tree_checksum"],
            "snapshot": self.snapshot,
            "bugs_in_snapshot": list(self.bug_ids),
            "agent_adapter": self.adapter_name,
            "agent_adapter_version": self.adapter_version,
            "trace_schema_version": TRACE_SCHEMA_VERSION,
            "tool_backend": self.tool_backend,
            "image_ref": (
                self.image_identity.immutable_ref if self.image_identity is not None else None
            ),
            "image_manifest_digest": (
                self.image_identity.manifest_digest if self.image_identity is not None else None
            ),
            "image_config_digest": (
                self.image_identity.config_digest if self.image_identity is not None else None
            ),
            "termination_reason": self.result.termination_reason.value,
            "steps": self.result.steps,
            "tool_calls": self.result.tool_calls,
            "wall_clock_ms": self.result.wall_clock_ms,
            "findings_submitted": len(self.result.findings),
            "provider": _defensive_copy(self.metadata.public_configuration),
            "adjudication": (
                "Not scored. `verified_*` metrics require a human ruling on blinded "
                "finding/bug pairs; run `cae evaluate` once the ledger has one."
            ),
        }

    @property
    def failure(self) -> dict[str, Any]:
        """The provider's own classification of a failure, or empty.

        Read back off the recorded termination event rather than carried
        separately, so what a reader sees in `run.json` is what the trace holds.
        """
        for event in reversed(self.events):
            if event["event"] == "termination":
                payload = event["payload"]
                detail = dict(payload.get("provider_error") or {})
                message = payload.get("provider_error_message") or ""
                if message:
                    detail["message"] = message
                return detail
        return {}

    def usage_total(self) -> dict[str, Any]:
        """Tokens and estimated cost, summed across the run's provider calls.

        `cached_input_tokens` is here because without it the summary cannot be
        reconciled with itself. The first live run billed 199,181 input tokens
        and cost $0.0115 — a factor of 3.7 below the headline rate — because 87%
        of the input was a cache hit priced at a tenth of it. A reader given only
        the input count and the cost would conclude one of them was wrong.
        """
        input_tokens = cached_tokens = output_tokens = reasoning_tokens = 0
        cost = 0.0
        completeness = "complete"
        for event in self.result.events:
            if event["event"] != "llm_call":
                continue
            usage = event["payload"].get("usage") or {}
            if self.metadata.usage_source == "agent_reported_unverified" and not usage:
                completeness = "partial"
            input_tokens += int(usage.get("input_tokens") or 0)
            cached_tokens += int(usage.get("cached_input_tokens") or 0)
            output_tokens += int(usage.get("output_tokens") or 0)
            reasoning_tokens += int(usage.get("reasoning_tokens") or 0)
            cost += float(usage.get("estimated_cost_usd") or 0.0)
            if usage.get("completeness") == "partial":
                completeness = "partial"
        return {
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_tokens,
            "output_tokens": output_tokens,
            "reasoning_tokens": reasoning_tokens,
            "estimated_cost_usd": round(cost, 6),
            "completeness": completeness,
            "pricing_table_version": self.metadata.pricing_table_version,
        }

    def trace_header(self) -> dict[str, Any]:
        """Replay provenance for the public trace, with private inputs classified explicitly."""
        params = _defensive_copy(self.metadata.private_parameters)
        prompt = self.system_prompt
        return {
            "run_id": self.run_id,
            "benchmark_version": BENCHMARK_VERSION,
            "fixture_id": self.fixture.fixture_id,
            "fixture_version": self.fixture.version,
            "fixture_tree_checksum": self.fixture.manifest["clean_control"]["tree_checksum"],
            "snapshot": self.snapshot,
            "bug_set_hash": _canonical_hash(list(self.bug_ids)),
            "agent_adapter": self.adapter_name,
            "agent_adapter_version": self.adapter_version,
            "provider": self.metadata.provider,
            "model": self.metadata.model,
            "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "system_prompt_version": self.metadata.system_prompt_version,
            "params_hash": _canonical_hash(params),
            "seed": None,
            "image_ref": (
                self.image_identity.immutable_ref if self.image_identity is not None else None
            ),
            "image_manifest_digest": (
                self.image_identity.manifest_digest if self.image_identity is not None else None
            ),
            "image_config_digest": (
                self.image_identity.config_digest if self.image_identity is not None else None
            ),
            "env_fingerprint": self.fixture.manifest["environment"]["fingerprint"],
            "sandbox_profile": "measure" if self.image_identity is not None else "host_process",
            "tool_backend": self.tool_backend,
            "budget": self.budget.as_dict(),
            "redaction_manifest_version": REDACTION_MANIFEST_VERSION,
            "system_prompt": prompt,
            "params": params,
        }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


def _deep_freeze(value: Any) -> Any:
    """Recursively replace mutable JSON containers with immutable snapshots."""
    if isinstance(value, dict):
        return MappingProxyType({key: _deep_freeze(child) for key, child in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(child) for child in value)
    return value


def _defensive_copy(value: Any) -> Any:
    """Return ordinary JSON containers with no references into frozen metadata."""
    if isinstance(value, Mapping):
        return {key: _defensive_copy(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_defensive_copy(child) for child in value]
    return deepcopy(value)


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def write_evidence(run: LiveRun, directory: Path) -> Path:
    """Write the run header, public trace, and findings. Returns the directory."""
    # Sanitize before writing the companion files. A rejected private record
    # must not leave a plausible-looking partial public run directory.
    sanitize_events(run.raw_store.read_events(), directory / "trace.jsonl")

    header: dict[str, Any] = {**run.header(), "usage": run.usage_total()}
    failure = run.failure
    if failure:
        # The evidence directory is publishable as a unit. Provider free text
        # remains only in the owner-only raw store and immediate CLI diagnostic.
        header["provider_error"] = {
            key: value for key, value in failure.items() if key != "message"
        }
    _write_json(directory / "run.json", header)
    _write_json(directory / "findings.json", {"findings": run.result.findings})

    return directory


def build_adapter(configuration: RunConfiguration, *, client: httpx.Client | None) -> AgentAdapter:
    """Construct the adapter `configuration.api` names.

    A separate function so `execute` stays one path regardless of which
    adapter it drives, and so a test can build one without going through a
    full run.
    """
    from coding_agent_eval.agent.provider import render_system_prompt

    system_prompt = render_system_prompt(configuration.budget.max_tool_calls)
    if configuration.api == "responses":
        from coding_agent_eval.agent.responses_provider import OpenAIResponsesAdapter

        return OpenAIResponsesAdapter(
            model=configuration.model,
            api_key=configuration.api_key,
            base_url=configuration.base_url,
            pricing=configuration.pricing,
            reasoning_effort=configuration.reasoning_effort,
            max_output_tokens_per_request=configuration.max_output_tokens_per_request,
            system_prompt=system_prompt,
            client=client,
        )
    return OpenAICompatibleAdapter(
        model=configuration.model,
        api_key=configuration.api_key,
        base_url=configuration.base_url,
        pricing=configuration.pricing,
        reasoning_effort=configuration.reasoning_effort,
        max_output_tokens_per_request=configuration.max_output_tokens_per_request,
        system_prompt=system_prompt,
        client=client,
    )


def _bind_image_identity(
    fixture: Fixture, isolate_image: str | None
) -> PreparedImageIdentity | None:
    """Bind isolation to the fixture declaration before a provider can be initialized."""
    if isolate_image is None:
        return None

    environment = fixture.manifest["environment"]
    if "prepared_image_repository" not in environment:
        raise ImageIdentityError(
            "--isolate requires a fixture with the current OCI identity contract"
        )

    identity = PreparedImageIdentity.from_environment(environment)
    if isolate_image != identity.immutable_ref:
        raise ImageIdentityError(
            "--isolate must equal the fixture-derived immutable image reference "
            f"{identity.immutable_ref!r}"
        )
    return identity


def execute_with_adapter(
    fixture_dir: Path,
    *,
    adapter: AgentAdapter,
    metadata: ExecutionMetadata,
    budget: Budget,
    system_prompt: str,
    snapshot: str,
    bug_index: int = 0,
    isolate_image: str | None = None,
    run_id: str | None = None,
    workspace: Path | None = None,
    raw_store_root: Path | None = None,
) -> LiveRun:
    """Run one adapter through the shared fixture, tool, trace, and evidence core."""
    fixture = load_fixture(fixture_dir)
    if snapshot not in (CLEAN, MUTATED):
        raise ValueError(f"snapshot must be {CLEAN!r} or {MUTATED!r}, not {snapshot!r}")
    image_identity = _bind_image_identity(fixture, isolate_image)

    root = Path(workspace or tempfile.mkdtemp(prefix="cae-live-"))
    tree = materialise(fixture.directory / "tree", root / fixture.fixture_id)

    bug_ids: tuple[str, ...] = ()
    if snapshot == MUTATED:
        bug = fixture.bugs[bug_index]
        apply_patch(tree, fixture.directory / bug["patch"])
        bug_ids = (str(bug["bug_id"]),)

    started_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "+00:00"
    effective_run_id = run_id or f"live-{fixture.fixture_id}-{snapshot}"
    store = RawStore(raw_store_root or root.parent / ".run-store", run_id=effective_run_id)
    recorder = Recorder(sink=store.append_record)

    backend: Any
    if image_identity is None:
        backend = nullcontext(LocalTree(tree))
    else:
        from coding_agent_eval.sandbox.tool_container import tool_container

        backend = tool_container(image_identity.immutable_ref, tree)

    with backend as view:
        view_backend: TreeBackend = view
        tool_backend = (
            view_backend.description
            if image_identity is None
            else f"measure_container:{image_identity.manifest_digest}"
        )
        provisional = LiveRun(
            run_id=effective_run_id,
            fixture=fixture,
            snapshot=snapshot,
            bug_ids=bug_ids,
            result=RunResult(
                termination_reason=TerminationReason.NO_OUTPUT,
                findings=[],
                steps=0,
                tool_calls=0,
                wall_clock_ms=0,
            ),
            events=recorder.events,
            tool_backend=tool_backend,
            metadata=metadata,
            budget=budget,
            started_at=started_at,
            adapter_name=adapter.name,
            adapter_version=adapter.version,
            raw_store=store,
            image_identity=image_identity,
            system_prompt=system_prompt,
        )
        recorder.emit("run_header", provisional.trace_header())
        result = run_agent(
            adapter,
            context=ToolContext(backend=view_backend),
            budget=budget,
            recorder=recorder,
        )

    return LiveRun(
        run_id=effective_run_id,
        fixture=fixture,
        snapshot=snapshot,
        bug_ids=bug_ids,
        result=result,
        events=recorder.events,
        tool_backend=tool_backend,
        metadata=metadata,
        budget=budget,
        started_at=started_at,
        adapter_name=adapter.name,
        adapter_version=adapter.version,
        raw_store=store,
        image_identity=image_identity,
        system_prompt=system_prompt,
    )


def _provider_metadata(configuration: RunConfiguration) -> ExecutionMetadata:
    """Map the existing provider configuration without changing its artifacts."""
    redacted = configuration.redacted()
    return ExecutionMetadata(
        provider=configuration.api,
        model=configuration.model,
        public_configuration=redacted,
        private_parameters=redacted,
        pricing_table_version=configuration.pricing.version,
        transport="https-json",
        protocol_version=None,
        usage_source="provider_reported",
        system_prompt_version=SYSTEM_PROMPT_VERSION,
    )


def execute(
    fixture_dir: Path,
    *,
    configuration: RunConfiguration,
    snapshot: str,
    bug_index: int = 0,
    isolate_image: str | None = None,
    run_id: str | None = None,
    workspace: Path | None = None,
    raw_store_root: Path | None = None,
    client: httpx.Client | None = None,
) -> LiveRun:
    """Run one snapshot against a live provider. Makes real, billed requests.

    `client` permits the complete path to be exercised with an
    `httpx.MockTransport`, without a network, key, or bill.
    """
    # Preserve the refusal boundary: malformed snapshot/OCI identity must be
    # rejected before even constructing a provider adapter.
    fixture = load_fixture(fixture_dir)
    if snapshot not in (CLEAN, MUTATED):
        raise ValueError(f"snapshot must be {CLEAN!r} or {MUTATED!r}, not {snapshot!r}")
    _bind_image_identity(fixture, isolate_image)

    adapter = build_adapter(configuration, client=client)
    system_prompt = getattr(adapter, "system_prompt", DEFAULT_SYSTEM_PROMPT)
    return execute_with_adapter(
        fixture_dir,
        adapter=adapter,
        metadata=_provider_metadata(configuration),
        budget=configuration.budget,
        system_prompt=system_prompt,
        snapshot=snapshot,
        bug_index=bug_index,
        isolate_image=isolate_image,
        run_id=run_id,
        workspace=workspace,
        raw_store_root=raw_store_root,
    )


def execute_stdio(
    fixture_dir: Path,
    *,
    configuration: StdioRunConfiguration,
    snapshot: str,
    bug_index: int = 0,
    isolate_image: str | None = None,
    run_id: str | None = None,
    workspace: Path | None = None,
    raw_store_root: Path | None = None,
) -> LiveRun:
    """Run one external JSONL stdio agent through the shared live executor."""
    from coding_agent_eval.agent.provider import render_system_prompt
    from coding_agent_eval.agent.stdio_adapter import StdioAgentAdapter
    from coding_agent_eval.agent.stdio_protocol import PROTOCOL_VERSION

    system_prompt = render_system_prompt(configuration.budget.max_tool_calls)
    metadata = ExecutionMetadata(
        provider=None,
        model=configuration.agent_model,
        public_configuration=configuration.redacted(),
        private_parameters=configuration.private_parameters(),
        pricing_table_version="external-self-reported/1.0",
        transport="stdio-jsonl",
        protocol_version=PROTOCOL_VERSION,
        usage_source="agent_reported_unverified",
        system_prompt_version=SYSTEM_PROMPT_VERSION,
    )
    with StdioAgentAdapter(configuration, instructions=system_prompt) as adapter:
        return execute_with_adapter(
            fixture_dir,
            adapter=adapter,
            metadata=metadata,
            budget=configuration.budget,
            system_prompt=system_prompt,
            snapshot=snapshot,
            bug_index=bug_index,
            isolate_image=isolate_image,
            run_id=run_id,
            workspace=workspace,
            raw_store_root=raw_store_root,
        )
