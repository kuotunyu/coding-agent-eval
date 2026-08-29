"""Deterministic scripted baselines (plan F2).

These are controls. What is checked here is that each one produces the outcome
it exists to produce, that every termination reason in spec §13.1 is reachable
by something, and — the contract the offline gates rest on — that running the
same script twice yields the same public trace.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

import pytest

from coding_agent_eval.agent.baseline import (
    BASELINE_VERSION,
    SCRIPTS,
    FailingAdapter,
    ScriptedAdapter,
    high_noise,
    no_output,
    perfect,
    runs_forever,
    stops_with,
    zero_recall,
)
from coding_agent_eval.agent.loop import Recorder, run_agent
from coding_agent_eval.agent.protocol import AgentAdapter, Budget, TerminationReason
from coding_agent_eval.agent.tools import ToolContext
from coding_agent_eval.trace.public_trace import project_events

GROUND_TRUTH: list[dict[str, Any]] = [
    {
        "id": "gt-1",
        "file": "src/auth.py",
        "line_start": 2,
        "line_end": 2,
        "category": "security",
        "severity": "high",
        "claim": "Token comparison is not constant time.",
        "root_cause": "Uses == on strings, returning at the first differing byte.",
        "evidence": "src/auth.py line 2 compares with ==.",
        "suggested_verification": "Swap for compare_digest and re-run the auth tests.",
    }
]


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    (root / "src").mkdir(parents=True)
    (root / "src" / "auth.py").write_bytes(b"def verify(a, b):\n    return a == b\n")
    (root / "README.md").write_bytes(b"# demo\n")
    return root


def drive(adapter: Any, tree: Path, **kwargs: Any) -> Any:
    return run_agent(adapter, context=ToolContext(root=tree), **kwargs)


def fixed_clock() -> Any:
    """A monotonic clock that never advances far enough to trip a budget."""
    counter = itertools.count()
    return lambda: next(counter) * 0.001


# ------------------------------------------------------------ the behaviours


def test_the_perfect_baseline_submits_exactly_what_it_was_given(tree: Path) -> None:
    result = drive(perfect(GROUND_TRUTH), tree)
    assert result.termination_reason is TerminationReason.COMPLETED
    assert result.findings == GROUND_TRUTH


def test_the_zero_recall_baseline_submits_something_that_matches_nothing(tree: Path) -> None:
    """Zero recall with a defined precision denominator.

    Distinct from `no_output`, which submits nothing at all — the two produce
    the same recall and different precision, and a control that conflated them
    would leave that difference unobserved.
    """
    result = drive(zero_recall(), tree)
    assert result.termination_reason is TerminationReason.COMPLETED
    assert len(result.findings) == 1
    assert result.findings[0]["id"].startswith("noise-")


def test_the_high_noise_baseline_submits_many_unsupported_findings(tree: Path) -> None:
    result = drive(high_noise(count=25), tree)
    assert result.termination_reason is TerminationReason.COMPLETED
    assert len(result.findings) == 25
    assert len({finding["id"] for finding in result.findings}) == 25


def test_the_no_output_baseline_submits_nothing(tree: Path) -> None:
    result = drive(no_output(), tree)
    assert result.termination_reason is TerminationReason.NO_OUTPUT
    assert result.findings == []


@pytest.mark.parametrize(
    ("make", "expected"),
    [(zero_recall, 1), (lambda: high_noise(count=3), 3)],
    ids=["zero_recall", "high_noise"],
)
def test_every_baseline_finding_is_accepted_by_the_tool(
    tree: Path, make: Any, expected: int
) -> None:
    """The noise findings stand in for wrong answers, not malformed ones.

    A malformed finding would be refused by `write_findings`, and the control
    would then be exercising validation rather than scoring. Checked by counting
    what actually landed and by requiring the submission itself not to error.
    """
    recorder = Recorder(timestamp=lambda: "1970-01-01T00:00:00.000+00:00")
    result = run_agent(make(), context=ToolContext(root=tree), recorder=recorder)

    assert len(result.findings) == expected

    submissions = [
        event
        for index, event in enumerate(recorder.events)
        if event["event"] == "tool_result"
        and recorder.events[index - 1]["payload"].get("tool_name") == "write_findings"
    ]
    assert submissions, "the baseline never submitted anything"
    assert all(not event["payload"]["is_error"] for event in submissions)


# ------------------------------------------------------- termination reasons


@pytest.mark.parametrize(
    "reason",
    [
        TerminationReason.PARTIAL,
        TerminationReason.BUDGET_EXHAUSTED_TOKENS,
        TerminationReason.BUDGET_EXHAUSTED_COST,
        TerminationReason.BUDGET_EXHAUSTED_WALLCLOCK,
        TerminationReason.STEP_EXHAUSTED,
        TerminationReason.LOOP_DETECTED,
        TerminationReason.PROVIDER_ERROR,
        TerminationReason.SANDBOX_ERROR,
        TerminationReason.HARNESS_ERROR,
    ],
)
def test_a_baseline_exists_for_each_declared_reason(tree: Path, reason: Any) -> None:
    """No enum member should be one nothing has ever produced."""
    result = drive(stops_with(reason), tree)
    assert result.termination_reason is reason


def test_the_failing_adapter_produces_adapter_error(tree: Path) -> None:
    result = drive(FailingAdapter(), tree)
    assert result.termination_reason is TerminationReason.ADAPTER_ERROR


def test_completed_and_no_output_are_both_reachable(tree: Path) -> None:
    assert drive(perfect(GROUND_TRUTH), tree).termination_reason is TerminationReason.COMPLETED
    assert drive(no_output(), tree).termination_reason is TerminationReason.NO_OUTPUT


def test_every_termination_reason_is_covered_by_this_file() -> None:
    """Stated as a set comparison so a new reason fails here rather than silently."""
    covered = {
        TerminationReason.COMPLETED,
        TerminationReason.NO_OUTPUT,
        TerminationReason.ADAPTER_ERROR,
        TerminationReason.PARTIAL,
        TerminationReason.BUDGET_EXHAUSTED_TOKENS,
        TerminationReason.BUDGET_EXHAUSTED_COST,
        TerminationReason.BUDGET_EXHAUSTED_WALLCLOCK,
        TerminationReason.STEP_EXHAUSTED,
        TerminationReason.LOOP_DETECTED,
        TerminationReason.PROVIDER_ERROR,
        TerminationReason.SANDBOX_ERROR,
        TerminationReason.HARNESS_ERROR,
    }
    assert covered == set(TerminationReason)


def test_a_runaway_baseline_cannot_use_the_reserved_report_slot(tree: Path) -> None:
    result = drive(runs_forever(), tree, budget=Budget(max_tool_calls=4))
    assert result.termination_reason is TerminationReason.STEP_EXHAUSTED
    assert result.tool_calls == 3


def test_a_runaway_baseline_is_stopped_by_the_loop_ceiling(tree: Path) -> None:
    """Without a budget, something still has to stop it."""
    result = drive(runs_forever(), tree, max_steps=12)
    assert result.termination_reason is TerminationReason.LOOP_DETECTED


# ---------------------------------------------------------------- determinism


def public_trace(adapter: Any, tree: Path) -> list[dict[str, Any]]:
    recorder = Recorder(timestamp=lambda: "1970-01-01T00:00:00.000+00:00")
    run_agent(adapter, context=ToolContext(root=tree), recorder=recorder, clock=fixed_clock())
    return project_events(recorder.events)


@pytest.mark.parametrize(
    "make",
    [lambda: perfect(GROUND_TRUTH), zero_recall, lambda: high_noise(count=5), no_output],
    ids=["perfect", "zero_recall", "high_noise", "no_output"],
)
def test_two_runs_of_one_script_produce_the_same_public_trace(tree: Path, make: Any) -> None:
    """The contract the offline gates rest on.

    Timestamps are held still because they are the one field that legitimately
    differs. Everything else — order, hashes, payloads — must match, or a replay
    gate cannot tell a real change from noise.
    """
    assert public_trace(make(), tree) == public_trace(make(), tree)


def test_the_public_trace_carries_no_tool_output(tree: Path) -> None:
    """The projection drops it, and this is the run that proves the run loop's
    payloads are classified rather than merely assumed to be."""
    events = public_trace(perfect(GROUND_TRUTH), tree)
    for record in events:
        assert "content" not in record["payload"]


def test_the_public_trace_ends_with_a_termination_record(tree: Path) -> None:
    events = public_trace(perfect(GROUND_TRUTH), tree)
    assert events[-1]["event"] == "termination"
    assert events[-1]["payload"]["reason"] == "completed"


# ------------------------------------------------------------------ hygiene


def test_baselines_satisfy_the_adapter_protocol() -> None:
    assert isinstance(zero_recall(), AgentAdapter)
    assert isinstance(FailingAdapter(), AgentAdapter)


def test_every_registered_script_builds_and_runs(tree: Path) -> None:
    for name, make in SCRIPTS.items():
        adapter = make()
        assert isinstance(adapter, ScriptedAdapter), name
        assert adapter.version == BASELINE_VERSION
        drive(adapter, tree, max_steps=20)


@pytest.mark.parametrize(
    "make",
    [lambda: perfect(GROUND_TRUTH), zero_recall, lambda: high_noise(count=3), no_output],
    ids=["perfect", "zero_recall", "high_noise", "no_output"],
)
def test_no_script_asks_for_a_path_outside_the_tree(tree: Path, make: Any) -> None:
    """Controls use the same tool surface as anything else.

    The surface would refuse an escaping path anyway, so what this checks is
    that no script *asks* for one — a script that did would spend its steps on
    rejections and quietly stop being the control it claims to be.
    """
    for record in public_trace(make(), tree):
        path = record["payload"].get("args_safe", {}).get("path")
        if path is not None:
            assert not path.startswith("/")
            assert ".." not in path
