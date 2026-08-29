"""The run loop: drive an adapter, run its tools, record what happened.

The loop owns everything that decides a score — the budget, the termination
reason, the trace — so that swapping the adapter swaps the agent and nothing
else. An adapter that could end its own run on its own terms would be reporting
its own result.

Tool errors are handled per spec §13.3. An expected failure is content: it goes
back to the agent and the run continues, because failing to find a file is
something agents do. An unexpected exception is also fed back, but it is
counted, and three in a row end the run as `harness_error` — a harness that is
throwing is a harness whose scores mean nothing, and producing a plausible
number from one is worse than producing none.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from coding_agent_eval.agent.protocol import (
    AgentAdapter,
    Budget,
    Observation,
    TerminationReason,
)
from coding_agent_eval.agent.tools import TOOLS_BY_NAME, ToolContext, ToolFailure, model_schemas

#: Consecutive unexpected exceptions that end a run. Spec §13.3.
UNEXPECTED_LIMIT = 3

#: A hard ceiling so a loop cannot spin for ever when no budget was given.
DEFAULT_MAX_STEPS = 1000


@dataclass(frozen=True)
class _ToolInterface:
    """The executable capabilities advertised for one provider decision."""

    mode: str
    tools: tuple[dict[str, Any], ...]
    limit: int | None
    remaining: int | None

    @property
    def names(self) -> frozenset[str]:
        return frozenset(str(tool["name"]) for tool in self.tools)


def _select_tool_interface(
    *,
    all_tools: tuple[dict[str, Any], ...],
    max_tool_calls: int | None,
    tool_calls: int,
    write_findings_attempted: bool,
) -> _ToolInterface:
    """Synchronize the advertised interface with executable capacity."""
    remaining = None if max_tool_calls is None else max(max_tool_calls - tool_calls, 0)
    if write_findings_attempted or remaining == 0:
        return _ToolInterface("finalization", (), max_tool_calls, remaining)
    if remaining == 1:
        report_tool = tuple(tool for tool in all_tools if tool["name"] == "write_findings")
        if len(report_tool) != 1:
            raise RuntimeError("write_findings must be registered exactly once")
        return _ToolInterface("report_only", report_tool, max_tool_calls, remaining)
    return _ToolInterface("review", all_tools, max_tool_calls, remaining)


@dataclass
class RunResult:
    """What a run produced, and how it ended."""

    termination_reason: TerminationReason
    findings: list[dict[str, Any]]
    steps: int
    tool_calls: int
    wall_clock_ms: int
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_scored(self) -> bool:
        return self.termination_reason.is_scored


def _args_hash(arguments: dict[str, Any]) -> str:
    encoded = json.dumps(arguments, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _safe_args(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """The argument projection that may appear in a public trace.

    An allowlist per tool rather than a denylist: a new tool with a sensitive
    argument would otherwise be public by default, which is the wrong direction
    for a mistake to run in.
    """
    allowed = {
        "read_file": ("path",),
        "list_directory": ("path",),
        "search_code": ("path", "pattern"),
        "write_findings": (),
    }.get(tool_name, ())
    return {key: arguments[key] for key in allowed if key in arguments}


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="milliseconds")


class Recorder:
    """Collects trace events. Kept separate so a run can be driven without one.

    Records are emitted in the shape the public projection consumes, so a run's
    events can be published without an intermediate translation step that could
    disagree with the allowlist.

    `timestamp` is injected because it is the one field that legitimately
    differs between two runs of the same script; a determinism check needs to be
    able to hold it still.
    """

    def __init__(
        self,
        *,
        timestamp: Callable[[], str] = _utc_now,
        sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.events: list[dict[str, Any]] = []
        self._seq = 0
        self._timestamp = timestamp
        self._sink = sink

    def emit(self, event: str, payload: dict[str, Any]) -> None:
        record = {"seq": self._seq, "ts": self._timestamp(), "event": event, "payload": payload}
        if self._sink is not None:
            self._sink(record)
        self.events.append(record)
        self._seq += 1


def _aggregate_cost(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-call price reports without inventing missing provenance."""
    estimated = round(sum(float(report.get("estimated_cost_usd") or 0.0) for report in reports), 6)
    completeness = (
        "partial"
        if not reports or any(report.get("completeness") == "partial" for report in reports)
        else "complete"
    )
    unknown_fields = sorted(
        {str(field) for report in reports for field in report.get("unknown_fields", [])}
    )
    if not reports:
        unknown_fields.append("usage")
    limitations = list(
        dict.fromkeys(
            str(note) for report in reports for note in report.get("estimator_limitations", [])
        )
    )

    def first(name: str) -> Any:
        return next((report[name] for report in reports if report.get(name) is not None), None)

    return {
        "estimated_cost_usd": estimated,
        "completeness": completeness,
        "unknown_fields": unknown_fields,
        "pricing_table_version": first("pricing_table_version") or "none-offline",
        "pricing_effective_date": first("pricing_effective_date"),
        "pricing_source": first("pricing_source"),
        "estimator_limitations": limitations,
        "provider_raw_usage": [report.get("raw_normalized", {}) for report in reports],
    }


def run_agent(
    adapter: AgentAdapter,
    *,
    context: ToolContext,
    budget: Budget | None = None,
    recorder: Recorder | None = None,
    clock: Callable[[], float] = time.monotonic,
    max_steps: int = DEFAULT_MAX_STEPS,
) -> RunResult:
    """Drive `adapter` against the tool surface until it stops or runs out.

    `clock` is injected so a wall-clock budget can be tested at its boundary
    rather than approximately by sleeping.
    """
    budget = budget or Budget()
    recorder = recorder or Recorder()
    all_tools = tuple(model_schemas())
    transcript: list[Observation] = []
    started = clock()

    steps = 0
    tool_calls = 0
    tokens = 0
    cost = 0.0
    cost_reports: list[dict[str, Any]] = []
    consecutive_unexpected = 0
    write_findings_attempted = False
    reason: TerminationReason | None = None
    #: Populated only by a provider failure, so an operator can act on it.
    failure: dict[str, Any] = {}

    def elapsed_ms() -> int:
        return int((clock() - started) * 1000)

    while reason is None:
        if steps >= max_steps:
            reason = TerminationReason.LOOP_DETECTED
            break
        if (
            budget.max_wallclock_seconds is not None
            and clock() - started >= budget.max_wallclock_seconds
        ):
            reason = TerminationReason.BUDGET_EXHAUSTED_WALLCLOCK
            break

        interface = _select_tool_interface(
            all_tools=all_tools,
            max_tool_calls=budget.max_tool_calls,
            tool_calls=tool_calls,
            write_findings_attempted=write_findings_attempted,
        )

        try:
            step = adapter.next_step(tools=interface.tools, transcript=transcript)
        except Exception:
            # The adapter itself failed. That is not the model's result and not
            # the harness's, so it is neither scored nor blamed on the tools.
            reason = TerminationReason.ADAPTER_ERROR
            break

        steps += 1
        tokens += int(step.usage.get("total_tokens", 0) or 0)
        cost += float(step.usage.get("estimated_cost_usd", 0.0) or 0.0)
        if step.usage or step.trace:
            recorder.emit("llm_call", {**step.trace, "usage": dict(step.usage)})
        if step.usage:
            cost_reports.append(dict(step.usage))

        if step.stop is not None:
            reason = step.stop
            failure = dict(step.error)
            break

        invocation = step.invocation
        assert invocation is not None  # Step.__post_init__ guarantees one or the other

        if invocation.tool_name not in interface.names:
            reason = TerminationReason.STEP_EXHAUSTED
            break

        if budget.max_tool_calls is not None and tool_calls >= budget.max_tool_calls:
            reason = TerminationReason.STEP_EXHAUSTED
            break

        if invocation.tool_name == "write_findings":
            write_findings_attempted = True

        tool_calls += 1
        recorder.emit(
            "tool_call",
            {
                "tool_name": invocation.tool_name,
                "args_safe": _safe_args(invocation.tool_name, invocation.arguments),
                "args_hash": _args_hash(invocation.arguments),
            },
        )

        content, is_error, unexpected = _run_one_tool(context, invocation)
        if unexpected:
            consecutive_unexpected += 1
        else:
            consecutive_unexpected = 0

        recorder.emit(
            "tool_result",
            {
                "is_error": is_error,
                "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "content_bytes": len(content.encode("utf-8")),
                "content": content,
            },
        )
        transcript.append(
            Observation(
                tool_name=invocation.tool_name,
                content=content,
                is_error=is_error,
                assistant_turn=step.assistant_turn,
            )
        )

        if consecutive_unexpected >= UNEXPECTED_LIMIT:
            reason = TerminationReason.HARNESS_ERROR
            break

        # Budgets are checked after the call that consumed them, so a run is
        # never stopped for a cost it has not yet incurred.
        if budget.max_tokens is not None and tokens >= budget.max_tokens:
            reason = TerminationReason.BUDGET_EXHAUSTED_TOKENS
        elif budget.max_estimated_cost_usd is not None and cost >= budget.max_estimated_cost_usd:
            reason = TerminationReason.BUDGET_EXHAUSTED_COST

    reason = _final_reason(reason)
    if context.findings:
        recorder.emit("findings_submitted", {"findings": list(context.findings)})
    recorder.emit("cost", _aggregate_cost(cost_reports))
    recorder.emit(
        "termination",
        {
            "reason": reason.value,
            "steps": steps,
            "tool_calls": tool_calls,
            "wall_clock_ms": elapsed_ms(),
            # Split by the allowlist: the provider's structural classification is
            # public, the free text it wrote is not, because a provider may quote
            # the request back and the request carries the tree the agent read.
            "provider_error": {k: v for k, v in failure.items() if k != "message"},
            "provider_error_message": failure.get("message", ""),
        },
    )

    return RunResult(
        termination_reason=reason,
        findings=list(context.findings),
        steps=steps,
        tool_calls=tool_calls,
        wall_clock_ms=elapsed_ms(),
        events=recorder.events,
    )


def _run_one_tool(context: ToolContext, invocation: Any) -> tuple[str, bool, bool]:
    """Run one tool. Returns its content, whether it errored, and whether that was unexpected."""
    tool = TOOLS_BY_NAME.get(invocation.tool_name)
    if tool is None:
        # Naming a tool that does not exist is an ordinary mistake, not a
        # harness fault, so it does not count toward the unexpected limit.
        return (
            f"no tool named {invocation.tool_name!r}; available: "
            + ", ".join(sorted(TOOLS_BY_NAME)),
            True,
            False,
        )
    try:
        return tool.handler(context, invocation.arguments), False, False
    except ToolFailure as failure:
        return str(failure), True, False
    except Exception as exc:
        # Fed back like any other error, but the type is recorded and the run
        # will end if this keeps happening.
        return f"{type(exc).__name__}: {exc}", True, True


def _final_reason(reason: TerminationReason | None) -> TerminationReason:
    """Resolve what a run's ending actually was.

    Adapters classify the provider's final output shape explicitly. A clean
    conclusion with zero findings is still completed; absent or malformed final
    output is no_output.
    """
    if reason is None:
        return TerminationReason.NO_OUTPUT
    return reason
