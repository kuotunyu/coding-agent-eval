"""Gate G9 — the deterministic baseline, end to end (plan X1).

Two fixtures, two snapshots, and every headline metric asserted **by name and by
value**. A pipeline that runs is not evidence. A pipeline that produces the
number it was predicted to produce is, because a wrong denominator, a dropped
finding, or a matcher that stopped matching all show up as a different number
rather than as an error.

Every result here is scored against a **synthetic** ledger and is therefore
unpublishable by construction. These numbers validate the evaluator's arithmetic
and the wiring between the pieces. They describe no model, and no agent was
involved in producing them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from coding_agent_eval.agent.baseline import high_noise, perfect, zero_recall
from coding_agent_eval.e2e import (
    CLEAN,
    MUTATED,
    Fixture,
    Workspace,
    finding_for,
    load_fixture,
    run_snapshot,
    synthetic_ledger_for,
    write_results,
)
from coding_agent_eval.evaluator.metrics import EvaluationError
from coding_agent_eval.hygiene.policy import TRACKED_FILE_POLICY
from coding_agent_eval.trace.public_trace import project_events
from tests.conftest import DOCKER_AVAILABLE

FIXTURE_IDS = ["fx-taskq-py", "fx-ledger-ts"]

#: Prepared images, for the variant of this gate that runs under real isolation.
IMAGE_TAGS = {
    "fx-taskq-py": "cae/fx-taskq-py:1.0.3",
    "fx-ledger-ts": "cae/fx-ledger-ts:1.0.2",
}

#: The six names spec §8.6 fixes. Asserted as a set so a rename fails here.
HEADLINE_METRICS = {
    "verified_bug_recall",
    "verified_finding_precision",
    "benchmark_unsupported_findings_per_kloc",
    "localization_recall",
    "cost_per_verified_bug",
    "tokens_per_verified_bug",
}

NOISE_COUNT = 5


@pytest.fixture(params=FIXTURE_IDS)
def fixture(request: pytest.FixtureRequest) -> Fixture:
    return load_fixture(Path("fixtures") / request.param)


def prepared(fixture: Fixture, workspace: Path) -> tuple[list[dict[str, Any]], Any]:
    findings = [finding_for(bug, index=index) for index, bug in enumerate(fixture.bugs)]
    ledger = synthetic_ledger_for(fixture, findings, workspace / "ledger.jsonl")
    return findings, ledger


# ------------------------------------------------------------ mutated snapshot


def test_a_perfect_run_scores_one_on_every_recall_metric(fixture: Fixture) -> None:
    """Recall and precision are exactly 1, for every bug in both fixtures.

    Every bug, not a sample: a matcher that failed on one localisation shape
    would otherwise hide behind the others.
    """
    with Workspace() as workspace:
        findings, ledger = prepared(fixture, workspace)
        for index in range(len(fixture.bugs)):
            result = run_snapshot(
                fixture,
                adapter=perfect([findings[index]]),
                snapshot=MUTATED,
                ledger=ledger,
                workspace=workspace,
                bug_index=index,
            )
            metrics = result.scored.metrics
            bug_id = fixture.bugs[index]["bug_id"]

            assert metrics["localization_recall"] == 1.0, bug_id
            assert metrics["verified_bug_recall"] == 1.0, bug_id
            assert metrics["verified_finding_precision"] == 1.0, bug_id
            assert metrics["unsupported_findings"] == 0, bug_id
            assert metrics["benchmark_unsupported_findings_per_kloc"] == 0.0, bug_id
            assert metrics["tokens_per_verified_bug"] == 300.0, bug_id


def test_a_zero_recall_run_leaves_the_per_bug_metrics_undefined(fixture: Fixture) -> None:
    """Recall 0 with a defined precision denominator, and no division by zero.

    This is the case that separates "found nothing" from "submitted nothing":
    precision still has a denominator, and the per-bug costs are `null` with a
    stated reason rather than `0`, which would read as free.
    """
    with Workspace() as workspace:
        _, ledger = prepared(fixture, workspace)
        result = run_snapshot(
            fixture,
            adapter=zero_recall(),
            snapshot=MUTATED,
            ledger=ledger,
            workspace=workspace,
            bug_index=0,
        )
        metrics = result.scored.metrics
        reasons = result.scored.undefined_reasons

        assert metrics["verified_bug_recall"] == 0.0
        assert metrics["localization_recall"] == 0.0
        assert metrics["verified_finding_precision"] == 0.0
        assert metrics["cost_per_verified_bug"] is None
        assert metrics["tokens_per_verified_bug"] is None
        assert reasons["cost_per_verified_bug"] == "no_verified_bugs"
        assert reasons["tokens_per_verified_bug"] == "no_verified_bugs"


# -------------------------------------------------------------- clean control


def test_the_clean_control_reports_noise_and_no_recall(fixture: Fixture) -> None:
    """Every finding on a clean tree is unsupported. That is the metric's meaning.

    The per-KLOC value is computed here from the manifest's own LOC, so a
    denominator that drifted from the fixture would change the number and fail.
    """
    with Workspace() as workspace:
        _, ledger = prepared(fixture, workspace)
        result = run_snapshot(
            fixture,
            adapter=high_noise(count=NOISE_COUNT),
            snapshot=CLEAN,
            ledger=ledger,
            workspace=workspace,
        )
        metrics = result.scored.metrics
        reasons = result.scored.undefined_reasons
        in_scope_loc = fixture.spec().in_scope_loc

        assert metrics["unsupported_findings"] == NOISE_COUNT
        assert metrics["benchmark_unsupported_findings_per_kloc"] == pytest.approx(
            NOISE_COUNT / (in_scope_loc / 1000)
        )
        assert metrics["localization_recall"] is None
        assert metrics["verified_bug_recall"] is None
        assert reasons["localization_recall"] == "no_bugs_in_snapshot"
        assert reasons["verified_bug_recall"] == "no_bugs_in_snapshot"


# ------------------------------------------------------------- publishability


def test_every_result_is_synthetic_and_unpublishable(fixture: Fixture) -> None:
    """The guard that stops these numbers being mistaken for a model result."""
    with Workspace() as workspace:
        findings, ledger = prepared(fixture, workspace)
        for snapshot, adapter in (
            (MUTATED, perfect([findings[0]])),
            (CLEAN, high_noise(count=NOISE_COUNT)),
        ):
            result = run_snapshot(
                fixture,
                adapter=adapter,
                snapshot=snapshot,
                ledger=ledger,
                workspace=workspace,
                bug_index=0,
            )
            document = result.results_document()
            assert document["ledger_kind"] == "synthetic"
            assert document["publishable"] is False


def test_the_headline_metric_names_are_all_present(fixture: Fixture) -> None:
    """Asserted as a set, so a rename fails here rather than in a report."""
    with Workspace() as workspace:
        findings, ledger = prepared(fixture, workspace)
        result = run_snapshot(
            fixture,
            adapter=perfect([findings[0]]),
            snapshot=MUTATED,
            ledger=ledger,
            workspace=workspace,
            bug_index=0,
        )
        assert set(result.scored.metrics) >= HEADLINE_METRICS


# ------------------------------------------------------------------- replay


def test_two_runs_produce_byte_identical_results(fixture: Fixture) -> None:
    """The replay guarantee, at the level that matters: the committed bytes."""
    with Workspace() as workspace:
        findings, ledger = prepared(fixture, workspace)

        def once() -> str:
            result = run_snapshot(
                fixture,
                adapter=perfect([findings[0]]),
                snapshot=MUTATED,
                ledger=ledger,
                workspace=workspace,
                bug_index=0,
            )
            return write_results(result, workspace / "out").read_text(encoding="utf-8")

        assert once() == once()


def test_the_results_document_is_valid_json_with_stable_keys(fixture: Fixture) -> None:
    with Workspace() as workspace:
        findings, ledger = prepared(fixture, workspace)
        result = run_snapshot(
            fixture,
            adapter=perfect([findings[0]]),
            snapshot=MUTATED,
            ledger=ledger,
            workspace=workspace,
            bug_index=0,
        )
        text = write_results(result, workspace / "out").read_text(encoding="utf-8")
        document = json.loads(text)

        assert document["fixture_id"] == fixture.fixture_id
        assert list(document) == sorted(document), "keys are sorted, so diffs stay readable"
        assert text.endswith("\n")
        assert "\r\n" not in text


# ------------------------------------------------------------------ hygiene


def test_the_public_trace_carries_no_tool_output(fixture: Fixture) -> None:
    """The projection is what makes a run publishable; this run goes through it."""
    with Workspace() as workspace:
        findings, ledger = prepared(fixture, workspace)
        result = run_snapshot(
            fixture,
            adapter=perfect([findings[0]]),
            snapshot=MUTATED,
            ledger=ledger,
            workspace=workspace,
            bug_index=0,
        )
        for record in project_events(result.events):
            assert "content" not in record["payload"]


def test_the_committed_artifacts_pass_the_leak_scanner(fixture: Fixture) -> None:
    """A run that could not be committed is a run that cannot be published."""
    with Workspace() as workspace:
        findings, ledger = prepared(fixture, workspace)
        result = run_snapshot(
            fixture,
            adapter=perfect([findings[0]]),
            snapshot=MUTATED,
            ledger=ledger,
            workspace=workspace,
            bug_index=0,
        )
        text = write_results(result, workspace / "out").read_text(encoding="utf-8")
        assert TRACKED_FILE_POLICY.findings(text) == []


# --------------------------------------------------------------- fail-closed


# -------------------------------------------------- G9 under real isolation


@pytest.mark.docker
@pytest.mark.skipif(not DOCKER_AVAILABLE, reason="Docker daemon is not reachable")
def test_a_run_scored_in_the_measure_container_matches_the_host_run(fixture: Fixture) -> None:
    """The point of putting the tools in a container: identical scores, real isolation.

    If these differed, choosing isolation would change the numbers and there
    would be no way to say which set was right. Because they do not, a measured
    run can be sandboxed at no cost to what it reports — and `tool_backend`
    records which of the two produced a result, since the metrics themselves
    cannot tell a reader that.
    """
    from coding_agent_eval.sandbox.run import resolve_digest
    from coding_agent_eval.sandbox.tool_container import tool_container

    try:
        image = resolve_digest(IMAGE_TAGS[fixture.fixture_id])
    except RuntimeError as exc:  # pragma: no cover - environment dependent
        pytest.skip(str(exc))

    with Workspace() as workspace:
        findings, ledger = prepared(fixture, workspace)

        def once(backend: Any) -> Any:
            return run_snapshot(
                fixture,
                adapter=perfect([findings[0]]),
                snapshot=MUTATED,
                ledger=ledger,
                workspace=workspace,
                bug_index=0,
                **({} if backend is None else {"backend": backend}),
            )

        on_host = once(None)
        contained = once(lambda tree: tool_container(image, tree))

        assert contained.scored.metrics == on_host.scored.metrics
        assert contained.scored.undefined_reasons == on_host.scored.undefined_reasons
        assert contained.scored.metrics["verified_bug_recall"] == 1.0, "not a vacuous comparison"

        assert contained.results_document()["tool_backend"] == f"measure_container:{image}"
        assert on_host.results_document()["tool_backend"] == "host_process"


def test_an_unadjudicated_pair_refuses_to_score(fixture: Fixture) -> None:
    """The end-to-end path must inherit the evaluator's refusal, not bypass it.

    An empty ledger with a finding that matches means one candidate pair and no
    ruling, which is exactly the condition §13.4 says must stop scoring.
    """
    from coding_agent_eval.evaluator.ledger import LedgerKind, load_ledger

    with Workspace() as workspace:
        findings, _ = prepared(fixture, workspace)
        empty = workspace / "empty.jsonl"
        empty.write_text("", encoding="utf-8")
        ledger = load_ledger(empty, kind=LedgerKind.SYNTHETIC)

        with pytest.raises(EvaluationError, match="unadjudicated"):
            run_snapshot(
                fixture,
                adapter=perfect([findings[0]]),
                snapshot=MUTATED,
                ledger=ledger,
                workspace=workspace,
                bug_index=0,
            )
