"""What an agent adapter is, and how a run ends (design spec §13.1).

An adapter is the only thing that differs between "a model under test" and "a
scripted baseline". Everything else — the tool surface, the budget accounting,
the trace — is the harness, and is identical for both. That is the point: a
baseline that took a different path through the harness would not be a control.

The adapter is deliberately small. It is handed the tool schemas and a
transcript, and returns the next step. It does not execute tools, count budget,
or write the trace, because an adapter that could do those could also get them
wrong in ways the harness would then attribute to the model.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class TerminationReason(StrEnum):
    """Why a run stopped. Spec §13.1.

    The three groups are scored differently and must stay distinguishable:
    `completed`/`partial` are scored normally, the exhaustion reasons score as
    zero recall with null precision, and the four `*_error` reasons are
    excluded from aggregates and counted in `n_invalid`. Collapsing an error
    into an exhaustion would turn a broken harness into a bad-looking model.
    """

    COMPLETED = "completed"
    PARTIAL = "partial"
    BUDGET_EXHAUSTED_TOKENS = "budget_exhausted_tokens"
    BUDGET_EXHAUSTED_COST = "budget_exhausted_cost"
    BUDGET_EXHAUSTED_WALLCLOCK = "budget_exhausted_wallclock"
    STEP_EXHAUSTED = "step_exhausted"
    LOOP_DETECTED = "loop_detected"
    NO_OUTPUT = "no_output"
    ADAPTER_ERROR = "adapter_error"
    PROVIDER_ERROR = "provider_error"
    SANDBOX_ERROR = "sandbox_error"
    HARNESS_ERROR = "harness_error"

    @property
    def is_invalid(self) -> bool:
        """Whether a run ending this way is excluded from aggregate metrics."""
        return self in _INVALID

    @property
    def is_scored(self) -> bool:
        """Whether findings from a run ending this way are scored normally."""
        return self in {TerminationReason.COMPLETED, TerminationReason.PARTIAL}


_INVALID = frozenset(
    {
        TerminationReason.ADAPTER_ERROR,
        TerminationReason.PROVIDER_ERROR,
        TerminationReason.SANDBOX_ERROR,
        TerminationReason.HARNESS_ERROR,
    }
)


@dataclass(frozen=True)
class Budget:
    """Limits a run may not exceed. Every dimension is optional but bounded.

    All four are enforced, and each has its own termination reason, because
    "ran out" is not one fact: a run that exhausted its step budget and one
    that burned its cost budget fail for different reasons and are fixed
    differently.
    """

    max_tokens: int | None = None
    max_tool_calls: int | None = None
    max_wallclock_seconds: float | None = None
    max_estimated_cost_usd: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_tokens": self.max_tokens,
            "max_tool_calls": self.max_tool_calls,
            "max_wallclock_seconds": self.max_wallclock_seconds,
            "max_estimated_cost_usd": self.max_estimated_cost_usd,
        }


@dataclass(frozen=True)
class ToolInvocation:
    """The adapter's request to run one tool."""

    tool_name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Step:
    """One decision from the adapter.

    Exactly one of `invocation` or `stop` is set. A step that did neither would
    leave the loop with nothing to do and no reason to stop, which is a bug in
    the adapter rather than a state the harness should invent a meaning for.
    """

    invocation: ToolInvocation | None = None
    stop: TerminationReason | None = None
    #: Reported to the trace, never to scoring.
    usage: dict[str, Any] = field(default_factory=dict)
    #: Provider-call evidence. Public metadata and explicitly private bodies
    #: share one event so the sanitizer must classify every field.
    trace: dict[str, Any] = field(default_factory=dict)
    #: Why a `PROVIDER_ERROR` stop happened. Empty for every other outcome.
    #:
    #: A run that failed and cannot say why is a run nobody can act on. The
    #: first live run this repository attempted stopped with `provider_error`
    #: after two seconds and left a trace of one line with no diagnostic in it,
    #: because the exception had been caught and discarded.
    error: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (self.invocation is None) == (self.stop is None):
            raise ValueError("a step must either invoke one tool or stop, not both or neither")


@dataclass(frozen=True)
class Observation:
    """What the harness reports back after running a tool."""

    tool_name: str
    content: str
    is_error: bool


@runtime_checkable
class AgentAdapter(Protocol):
    """The interface a model runner or a scripted baseline must satisfy.

    `name` and `version` land in the run header, so two runs of different
    adapters are never silently comparable.
    """

    name: str
    version: str

    def next_step(
        self,
        *,
        tools: Sequence[dict[str, Any]],
        transcript: Sequence[Observation],
    ) -> Step:
        """Choose the next tool call, or stop.

        `tools` is the model-facing schema list — the same object handed to a
        provider — so an adapter cannot see runtime resources the model cannot.
        """
        ...
