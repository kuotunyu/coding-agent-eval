"""Deterministic scripted baselines (plan F2).

These are controls, not agents. Each one replays a fixed sequence of steps, so
what the harness does with a given behaviour can be checked without a provider,
a key, or a network — which is what lets gates G6 and G9 run offline and in CI.

Three of them exist to pin the metrics at their extremes, because a metric that
is never observed at its boundary is a metric nobody has checked:

* `perfect` submits exactly the findings it is given, so recall is 1 and the
  unsupported count is 0.
* `zero_recall` explores and submits findings that match nothing, so recall is 0
  while precision still has a denominator — which is the case that separates
  "found nothing" from "submitted nothing".
* `high_noise` submits many unsupported findings, driving
  `benchmark_unsupported_findings_per_kloc` up on purpose.

The rest exist so every termination reason in spec §13.1 is produced by
something, rather than being an enum member no test has ever seen.

**Determinism is the contract.** Two runs of one script produce identical public
traces apart from `run_id` and timestamps. Nothing here reads a clock, a random
source, the environment, or the filesystem outside the tree it is given.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from coding_agent_eval.agent.protocol import Observation, Step, TerminationReason, ToolInvocation

BASELINE_VERSION = "0.1.0"

#: Usage reported per step. Fixed, because a baseline that varied its usage
#: would make cost metrics depend on which control was run.
_USAGE: dict[str, Any] = {"total_tokens": 100, "estimated_cost_usd": 0.0}


def _finding(index: int, *, file: str, category: str = "correctness") -> dict[str, Any]:
    """A syntactically valid finding that matches nothing in any fixture.

    Deliberately generic. These stand in for an agent's wrong answers, so they
    have to be well-formed — a malformed one would be refused by the tool and
    would test validation rather than scoring.
    """
    return {
        "id": f"noise-{index}",
        "file": file,
        "line_start": 1,
        "line_end": 1,
        "category": category,
        "severity": "low",
        "claim": f"Placeholder finding {index} produced by a scripted baseline.",
        "root_cause": (
            f"Baseline {index} emits this without analysis; it corresponds to no seeded defect."
        ),
        "evidence": f"Scripted baseline step {index}; no observation supports this.",
        "suggested_verification": "None. This finding exists to exercise scoring.",
    }


@dataclass(frozen=True)
class ScriptedAdapter:
    """Replays a fixed list of steps, then stops.

    Ignores the transcript entirely. That is the point of a control: its
    behaviour cannot depend on what the tree happens to contain, so two runs
    against the same tree are identical by construction rather than by luck.
    """

    name: str
    steps: tuple[Step, ...]
    version: str = BASELINE_VERSION
    exhausted_stop: TerminationReason = TerminationReason.COMPLETED
    #: Repeat the final step instead of stopping, so a script can outlast any
    #: budget. Used to check that something other than the adapter ends a run.
    repeat_last: bool = False

    def next_step(
        self,
        *,
        tools: Sequence[dict[str, Any]],
        transcript: Sequence[Observation],
    ) -> Step:
        index = len(transcript)
        # `transcript` grows by one per tool call, so it doubles as the cursor
        # without the adapter holding mutable state — which is what keeps a
        # second run of the same script identical to the first.
        for consumed, step in enumerate(self.steps):
            if step.invocation is None or consumed == index:
                return step
        if self.repeat_last and self.steps:
            return self.steps[-1]
        return Step(stop=self.exhausted_stop)


class FailingAdapter:
    """Raises on its first step, producing `adapter_error`."""

    name = "baseline-adapter-error"
    version = BASELINE_VERSION

    def next_step(self, **_: Any) -> Step:
        raise RuntimeError("scripted adapter failure")


def _explore(paths: Sequence[str] = ("src", ".")) -> list[Step]:
    return [
        Step(invocation=ToolInvocation("list_directory", {"path": p}), usage=_USAGE) for p in paths
    ]


def perfect(findings: Sequence[dict[str, Any]]) -> ScriptedAdapter:
    """Explores briefly, then submits exactly `findings`. Recall 1, noise 0."""
    steps = [
        *_explore(),
        Step(
            invocation=ToolInvocation("write_findings", {"findings": list(findings)}),
            usage=_USAGE,
        ),
        Step(stop=TerminationReason.COMPLETED),
    ]
    return ScriptedAdapter(name="baseline-perfect", steps=tuple(steps))


def zero_recall(*, file: str = "README.md") -> ScriptedAdapter:
    """Submits one well-formed finding that matches nothing. Recall 0, precision defined."""
    steps = [
        *_explore(),
        Step(
            invocation=ToolInvocation("write_findings", {"findings": [_finding(0, file=file)]}),
            usage=_USAGE,
        ),
        Step(stop=TerminationReason.COMPLETED),
    ]
    return ScriptedAdapter(name="baseline-zero-recall", steps=tuple(steps))


def high_noise(*, count: int = 25, file: str = "README.md") -> ScriptedAdapter:
    """Submits many unsupported findings, to drive the noise metric up on purpose."""
    steps = [
        *_explore(),
        Step(
            invocation=ToolInvocation(
                "write_findings",
                {"findings": [_finding(index, file=file) for index in range(count)]},
            ),
            usage=_USAGE,
        ),
        Step(stop=TerminationReason.COMPLETED),
    ]
    return ScriptedAdapter(name="baseline-high-noise", steps=tuple(steps))


def no_output() -> ScriptedAdapter:
    """Explores and stops without ever submitting. Distinct from finding nothing."""
    return ScriptedAdapter(
        name="baseline-no-output",
        steps=(*_explore(), Step(stop=TerminationReason.COMPLETED)),
    )


def stops_with(reason: TerminationReason) -> ScriptedAdapter:
    """A baseline that ends with `reason`, so no enum member is untested."""
    return ScriptedAdapter(
        name=f"baseline-{reason.value.replace('_', '-')}",
        steps=(*_explore(("src",)), Step(stop=reason)),
    )


def runs_forever() -> ScriptedAdapter:
    """Never stops. Used to exercise budgets and the loop ceiling."""
    return ScriptedAdapter(
        name="baseline-runs-forever",
        steps=(Step(invocation=ToolInvocation("list_directory", {"path": "."}), usage=_USAGE),),
        repeat_last=True,
    )


#: Every script that needs no ground truth, by name.
SCRIPTS: dict[str, Any] = {
    "zero_recall": zero_recall,
    "high_noise": high_noise,
    "no_output": no_output,
    "runs_forever": runs_forever,
}
