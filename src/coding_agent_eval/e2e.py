"""End-to-end wiring: fixture on disk to `results.json` (plan X1, gate G9).

Everything else in this package is a piece. This is the first thing that puts
them in a line — materialise a tree, drive an adapter across it with the real
tool surface, score the findings against a ledger, and emit a result.

It exists so gate G9 can assert *known numbers*. A pipeline that runs is not
evidence; a pipeline that produces the value it was predicted to produce is.

**Every result this module can currently produce is unpublishable.** It scores
against a synthetic ledger, so `ledger_kind` is `synthetic` and `publishable` is
`false`. That is not a limitation to be lifted by writing more code — it is what
the numbers are, until a person adjudicates.

**Isolation is a parameter, and the result records which one was used.** Passing
a `backend` runs the agent's tools inside the measure container (spec §9.1),
where they reach no host path at all; the default runs them in-process on the
host, isolated by the tool surface's own path checks. Both produce the same
scores — the Docker-marked G9 variant asserts exactly that — so choosing the
container costs nothing in behaviour and a great deal in blast radius.

The default is the host backend because the fast suite cannot start a container
per test. That means **a result is only as isolated as its `tool_backend` field
says**, which is why the field is written into `results.json` rather than left to
be inferred from the fact that a sandbox exists somewhere in the repository.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from coding_agent_eval.agent.backend import LocalTree, TreeBackend
from coding_agent_eval.agent.loop import Recorder, RunResult, run_agent
from coding_agent_eval.agent.protocol import AgentAdapter
from coding_agent_eval.agent.tools import ToolContext
from coding_agent_eval.evaluator.hashing import finding_hash
from coding_agent_eval.evaluator.ledger import (
    Ledger,
    LedgerKey,
    LedgerKind,
    build_entry,
    load_ledger,
    write_entries,
)
from coding_agent_eval.evaluator.metrics import (
    FixtureSpec,
    RunContext,
    ScoredRun,
    Usage,
    score_run,
)
from coding_agent_eval.fixtures.patcher import apply_patch, materialise

#: The trace schema a run here declares. Kept beside the runner because the
#: evaluator refuses a version it does not support, and a silent bump would turn
#: that refusal into a confusing failure somewhere else.
TRACE_SCHEMA_VERSION = "0.1.0"

CLEAN = "clean"
MUTATED = "mutated"

#: Supplies the tool surface's view of a materialised tree, and tears it down.
#:
#: A factory rather than a backend, because the tree does not exist until
#: `run_snapshot` has built it — and for a mutated snapshot, not until the patch
#: has applied. A context manager rather than a plain callable, because the
#: container backend owns a container that has to be removed however the run
#: ends, including when scoring raises.
BackendFactory = Callable[[Path], AbstractContextManager[TreeBackend]]


@dataclass(frozen=True)
class Fixture:
    """A fixture as loaded from disk."""

    directory: Path
    manifest: dict[str, Any]
    bugs: list[dict[str, Any]]

    @property
    def fixture_id(self) -> str:
        return str(self.manifest["fixture_id"])

    @property
    def version(self) -> str:
        return str(self.manifest["fixture_version"])

    def spec(self) -> FixtureSpec:
        scope = self.manifest["scope"]
        return FixtureSpec(
            fixture_id=self.fixture_id,
            fixture_version=self.version,
            tree_checksum=self.manifest["clean_control"]["tree_checksum"],
            in_scope_paths=list(scope["in_scope_paths"]),
            out_of_scope_paths=list(scope["out_of_scope_paths"]),
            in_scope_loc=int(scope["in_scope_loc"]),
        )


def load_fixture(directory: Path) -> Fixture:
    manifest = yaml.safe_load((directory / "fixture.yaml").read_text(encoding="utf-8"))
    bugs = [
        yaml.safe_load(
            (directory / "bugs" / f"{bug_id.split('/')[-1]}.yaml").read_text(encoding="utf-8")
        )
        for bug_id in manifest["bugs"]
    ]
    return Fixture(directory=directory, manifest=manifest, bugs=bugs)


def finding_for(bug: dict[str, Any], *, index: int) -> dict[str, Any]:
    """Build the finding a perfect agent would submit for `bug`.

    Derived from the bug's own localisation and category, which is what makes
    the expected metric values predictable rather than discovered. It is not a
    claim that any agent would write this: it is the input that makes recall
    exactly 1 so a wrong denominator shows up as a wrong number.
    """
    primary = bug["localization"]["primary"]
    return {
        "id": f"e2e-{index}",
        "file": primary["file"],
        "line_start": primary["line_start"],
        "line_end": primary["line_end"],
        "category": bug["category"],
        "severity": bug["severity"],
        "claim": f"Defect at {primary['file']}:{primary['line_start']}.",
        "root_cause": "Baseline finding constructed from the bug's own localisation.",
        "evidence": f"The code at {primary['file']} lines "
        f"{primary['line_start']}-{primary['line_end']}.",
        "suggested_verification": "Run the bug's witness contract.",
    }


def synthetic_ledger_for(fixture: Fixture, findings: list[dict[str, Any]], path: Path) -> Ledger:
    """Write and load a synthetic ledger ruling each finding on its bug.

    `SYNTHETIC-` prefixed by construction. The evaluator rejects such an entry
    in the formal ledger, so this can never be mistaken for a human ruling by
    being copied into the wrong file.
    """
    entries = [
        build_entry(
            key=LedgerKey(
                fixture_version=fixture.version,
                bug_id=bug["bug_id"],
                finding_hash=finding_hash(finding),
            ),
            decision="same_root_cause",
            rationale="Synthetic: exercises evaluator arithmetic, not a human ruling.",
            adjudicator_id="SYNTHETIC-e2e",
            decided_at="2026-08-06",
        )
        for bug, finding in zip(fixture.bugs, findings, strict=True)
    ]
    write_entries(path, entries)
    return load_ledger(path, kind=LedgerKind.SYNTHETIC)


@dataclass
class E2EResult:
    """One end-to-end run: what the agent did, what it scored, and how it was isolated."""

    fixture_id: str
    snapshot: str
    run: RunResult
    scored: ScoredRun
    events: list[dict[str, Any]]
    tool_backend: str

    def results_document(self) -> dict[str, Any]:
        """The `results.json` payload, deterministic apart from nothing.

        No timestamp, no run id, no path. Two runs of the same script over the
        same fixture produce the same bytes, which is what makes the replay
        assertion meaningful rather than a tautology about clocks.

        `tool_backend` is here because two runs with identical metrics can have
        been produced under very different containment, and a reader cannot tell
        which from the numbers. `host_process` means the tools ran unsandboxed.
        """
        return self.scored.as_dict()


def local_backend(tree: Path) -> AbstractContextManager[TreeBackend]:
    """The default: the tools read the materialised tree in this process.

    Nothing to set up and nothing to tear down, which is why the fast suite can
    use it several hundred times. It is also the weaker of the two, and a run
    that used it says so in `tool_backend`.
    """
    return nullcontext(LocalTree(tree))


def run_snapshot(
    fixture: Fixture,
    *,
    adapter: AgentAdapter,
    snapshot: str,
    ledger: Ledger,
    workspace: Path,
    bug_index: int = 0,
    backend: BackendFactory = local_backend,
) -> E2EResult:
    """Materialise one snapshot, run the adapter over it, and score the result.

    `bug_index` selects which bug's patch is applied for a mutated snapshot.
    One bug at a time, because a tree carrying several would make a recall
    denominator that no single bug's manifest describes.

    `backend` decides where the agent's tools read from. It is a factory rather
    than a backend because the tree does not exist until this function has made
    it — the mutated snapshot in particular is only a tree after the patch has
    applied. Pass `sandbox.tool_container.tool_container` bound to an image to
    run the tools under the measure profile (spec §9.1).
    """
    # A fresh directory per call. `materialise` refuses to overwrite, which is
    # right — a run that inherited the previous run's tree would be scored
    # against something it did not produce — so the caller has to give it
    # somewhere new each time rather than the same name twice.
    slot = Path(tempfile.mkdtemp(prefix=f"{snapshot}-", dir=workspace))
    tree = materialise(fixture.directory / "tree", slot / fixture.fixture_id)
    bugs_in_snapshot: list[dict[str, Any]] = []

    if snapshot == MUTATED:
        bug = fixture.bugs[bug_index]
        apply_patch(tree, fixture.directory / bug["patch"])
        bugs_in_snapshot = [bug]

    with backend(tree) as view:
        # No `root=`: when the backend is a container there is no host root the
        # tools address, and offering one would invite something downstream to
        # read the tree by a path the agent was never able to use.
        context = ToolContext(backend=view)
        recorder = Recorder(timestamp=lambda: "1970-01-01T00:00:00.000+00:00")
        result = run_agent(adapter, context=context, recorder=recorder)
        tool_backend = view.description

    scored = score_run(
        findings=result.findings,
        bugs=bugs_in_snapshot,
        ledger=ledger,
        fixture=fixture.spec(),
        context=RunContext(
            run_id="e2e",
            fixture_version=fixture.version,
            tree_checksum=fixture.manifest["clean_control"]["tree_checksum"],
            trace_schema_version=TRACE_SCHEMA_VERSION,
            snapshot=snapshot,
            tool_backend=tool_backend,
            pricing_table_version="none-offline",
            agent_adapter=adapter.name,
            agent_adapter_version=adapter.version,
            termination_reason=result.termination_reason.value,
        ),
        usage=_usage_from(result),
    )

    return E2EResult(
        fixture_id=fixture.fixture_id,
        snapshot=snapshot,
        run=result,
        scored=scored,
        events=recorder.events,
        tool_backend=tool_backend,
    )


def _usage_from(result: RunResult) -> Usage:
    """Total the usage the adapter reported across the run."""
    input_tokens = 0
    output_tokens = 0
    cost = 0.0
    reported_cost = False
    for event in result.events:
        if event["event"] != "llm_call":
            continue
        usage = event["payload"].get("usage", {})
        input_tokens += int(usage.get("input_tokens") or 0)
        output_tokens += int(usage.get("output_tokens") or 0)
        if usage.get("estimated_cost_usd") is not None:
            cost += float(usage["estimated_cost_usd"])
            reported_cost = True
        # The scripted baselines report a single `total_tokens` rather than a
        # split, so it is attributed to input. The split matters to pricing,
        # never to `tokens_per_verified_bug`, which sums the two.
        input_tokens += int(usage.get("total_tokens") or 0)

    return Usage(
        estimated_cost_usd=cost if reported_cost else None,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def write_results(result: E2EResult, directory: Path) -> Path:
    """Write `results.json` with stable key order and LF endings."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "results.json"
    payload = json.dumps(result.results_document(), indent=2, sort_keys=True, ensure_ascii=False)
    path.write_text(payload + "\n", encoding="utf-8", newline="\n")
    return path


class Workspace:
    """A temporary directory that cleans up even when a run raises."""

    def __init__(self) -> None:
        self.path = Path(tempfile.mkdtemp(prefix="cae-e2e-"))

    def __enter__(self) -> Path:
        return self.path

    def __exit__(self, *_: object) -> None:
        shutil.rmtree(self.path, ignore_errors=True)
