"""Adapter protocol and tool surface (design spec §6.4, §13.1, §13.3).

The properties here are the ones that decide whether a score means anything:
the schema the model is given is strict, the tools cannot be pointed outside the
tree, findings that do not fit their schema are refused with a pointer, and a
harness that is throwing stops rather than producing a number.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from coding_agent_eval.agent.loop import UNEXPECTED_LIMIT, Recorder, run_agent
from coding_agent_eval.agent.protocol import (
    AgentAdapter,
    Budget,
    Observation,
    Step,
    TerminationReason,
    ToolInvocation,
)
from coding_agent_eval.agent.tools import (
    TOOLS,
    TOOLS_BY_NAME,
    ToolContext,
    ToolFailure,
    model_schemas,
)

VALID_FINDING: dict[str, Any] = {
    "id": "f1",
    "file": "src/auth.py",
    "line_start": 10,
    "line_end": 12,
    "category": "security",
    "severity": "high",
    "claim": "The token comparison is not constant time.",
    "root_cause": "It uses == on strings, which returns at the first differing byte.",
    "evidence": "src/auth.py line 11 compares the presented token with ==.",
    "suggested_verification": "Replace with compare_digest and re-run the auth tests.",
}


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    (root / "src").mkdir(parents=True)
    (root / "src" / "auth.py").write_bytes(b"def verify(a, b):\n    return a == b\n")
    (root / "src" / "util.py").write_bytes(b"LIMIT = 100\n")
    (root / "README.md").write_bytes(b"# demo\n")
    return root


@pytest.fixture
def context(tree: Path) -> ToolContext:
    return ToolContext(root=tree)


def call(context: ToolContext, name: str, **arguments: Any) -> str:
    return TOOLS_BY_NAME[name].handler(context, arguments)


# ------------------------------------------------------------- strict schemas


@pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t.name)
def test_every_tool_schema_is_strict(tool: Any) -> None:
    """A loose schema costs steps: the model sends something plausible and pays
    for the rejection out of its budget."""
    schema = tool.parameters
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"]), tool.name


def test_the_nested_finding_schema_is_strict_too() -> None:
    """The array items are where a loose schema would actually hurt."""
    items = TOOLS_BY_NAME["write_findings"].parameters["properties"]["findings"]["items"]
    assert items["additionalProperties"] is False
    assert set(items["required"]) == set(items["properties"])


def test_runtime_resources_are_absent_from_the_model_schema() -> None:
    """A tool whose root is an argument is a tool the model can point elsewhere.

    The resources meant here are the ones the harness supplies and the model
    must not choose: where the tree is, and how much a call may return.
    `findings` is not one of them — submitting findings is the whole purpose of
    `write_findings`, and its parameter is the model's own output, not a
    resource it was handed.
    """
    for schema in model_schemas():
        properties = set(schema["parameters"]["properties"])
        assert not properties & {"root", "context", "max_file_bytes"}, schema["name"]


def test_the_context_is_not_reachable_through_any_tool_argument() -> None:
    """Stated as the complement: every declared parameter is named here.

    A new tool that took its root as an argument would have to be added to this
    list to pass, which is the point — the change would be deliberate and
    visible rather than silent.
    """
    declared = {
        "read_file": {"path"},
        "list_directory": {"path"},
        "search_code": {"path", "pattern"},
        "write_findings": {"findings"},
    }
    assert {s["name"]: set(s["parameters"]["properties"]) for s in model_schemas()} == declared


def test_the_model_schema_carries_no_handler() -> None:
    for schema in model_schemas():
        assert set(schema) == {"name", "description", "parameters"}


# ------------------------------------------------------------- path handling


@pytest.mark.parametrize(
    "path",
    ["../outside.txt", "src/../../outside.txt", "/etc/passwd", "..", "src\\auth.py"],
)
@pytest.mark.parametrize("tool", ["read_file", "list_directory", "search_code"])
def test_a_path_leaving_the_tree_is_refused(context: ToolContext, tool: str, path: str) -> None:
    arguments: dict[str, Any] = {"path": path}
    if tool == "search_code":
        arguments["pattern"] = "x"
    with pytest.raises(ToolFailure):
        TOOLS_BY_NAME[tool].handler(context, arguments)


def test_a_symlink_out_of_the_tree_is_refused(context: ToolContext, tmp_path: Path) -> None:
    """Lexical checks cannot see this one; only resolving the path can."""
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"private\n")
    link = context.root / "escape.txt"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available to this user")
    with pytest.raises(ToolFailure):
        call(context, "read_file", path="escape.txt")


def test_reading_a_file_inside_the_tree_works(context: ToolContext) -> None:
    output = call(context, "read_file", path="src/auth.py")
    assert "def verify" in output
    assert output.startswith("     1\t"), "lines are numbered so a finding can cite a range"


# ------------------------------------------------------- expected tool errors


def test_a_missing_file_is_an_expected_failure(context: ToolContext) -> None:
    with pytest.raises(ToolFailure):
        call(context, "read_file", path="src/nope.py")


def test_an_invalid_regular_expression_is_an_expected_failure(context: ToolContext) -> None:
    with pytest.raises(ToolFailure):
        call(context, "search_code", path=".", pattern="(unclosed")


def test_search_reports_file_and_line(context: ToolContext) -> None:
    assert "src/auth.py:2:" in call(context, "search_code", path=".", pattern="return a == b")


def test_search_with_no_matches_says_so(context: ToolContext) -> None:
    assert call(context, "search_code", path=".", pattern="zzz-not-here") == "no matches"


# ------------------------------------------------------------ write_findings


def test_a_valid_finding_is_recorded(context: ToolContext) -> None:
    assert "recorded 1" in call(context, "write_findings", findings=[VALID_FINDING])
    assert context.findings == [VALID_FINDING]


def test_an_invalid_finding_is_refused_with_its_pointer(context: ToolContext) -> None:
    """ "invalid finding" costs a step and teaches nothing; the pointer does not."""
    broken = {**VALID_FINDING, "category": "not-a-category"}
    with pytest.raises(ToolFailure) as failure:
        call(context, "write_findings", findings=[broken])
    message = str(failure.value)
    assert "/findings/0/category" in message


def test_a_missing_required_field_is_refused(context: ToolContext) -> None:
    incomplete = {key: value for key, value in VALID_FINDING.items() if key != "root_cause"}
    with pytest.raises(ToolFailure) as failure:
        call(context, "write_findings", findings=[incomplete])
    assert "root_cause" in str(failure.value)


def test_nothing_is_recorded_when_one_finding_is_invalid(context: ToolContext) -> None:
    """All or nothing: a partial accept leaves the agent unsure what landed."""
    broken = {**VALID_FINDING, "id": "f2", "line_start": 0}
    with pytest.raises(ToolFailure):
        call(context, "write_findings", findings=[VALID_FINDING, broken])
    assert context.findings == []


def test_a_repeated_finding_id_is_refused(context: ToolContext) -> None:
    call(context, "write_findings", findings=[VALID_FINDING])
    with pytest.raises(ToolFailure) as failure:
        call(context, "write_findings", findings=[VALID_FINDING])
    assert "already submitted" in str(failure.value)


def test_an_empty_submission_is_refused(context: ToolContext) -> None:
    with pytest.raises(ToolFailure):
        call(context, "write_findings", findings=[])


# ------------------------------------------------------ overlapping locations


def test_an_overlapping_finding_is_recorded_not_refused(context: ToolContext) -> None:
    """§8.5's own position: two distinct findings at one location both count.

    A tool cannot tell a resubmission from a second, genuinely different
    defect that happens to share lines, so it must not silently drop either.
    """
    call(context, "write_findings", findings=[VALID_FINDING])
    second = {**VALID_FINDING, "id": "f2", "line_start": 11, "line_end": 13}
    result = call(context, "write_findings", findings=[second])
    assert context.findings == [VALID_FINDING, second]
    assert "recorded 1" in result


def test_an_overlapping_finding_is_noted_by_the_id_it_overlaps(context: ToolContext) -> None:
    call(context, "write_findings", findings=[VALID_FINDING])
    second = {**VALID_FINDING, "id": "f2", "line_start": 11, "line_end": 13}
    result = call(context, "write_findings", findings=[second])
    assert "overlaps" in result
    assert "f1" in result


def test_a_non_overlapping_finding_carries_no_note(context: ToolContext) -> None:
    call(context, "write_findings", findings=[VALID_FINDING])
    elsewhere = {**VALID_FINDING, "id": "f2", "file": "src/util.py", "line_start": 1, "line_end": 1}
    result = call(context, "write_findings", findings=[elsewhere])
    assert "overlaps" not in result


def test_category_does_not_exempt_an_overlap_from_the_note(context: ToolContext) -> None:
    """The evidence this rule was written from: one location, resubmitted seven
    times across a live run, tagged three different categories along the way.
    Requiring a category match would have caught fewer than half of them."""
    call(context, "write_findings", findings=[VALID_FINDING])
    different_category = {
        **VALID_FINDING,
        "id": "f2",
        "category": "correctness",
        "line_start": 11,
        "line_end": 13,
    }
    result = call(context, "write_findings", findings=[different_category])
    assert "overlaps" in result


def test_adjacent_but_not_overlapping_lines_carry_no_note(context: ToolContext) -> None:
    """Interval overlap, not proximity — the same test the evaluator's own
    matcher uses, so the two never quietly disagree about what "overlaps" means."""
    call(context, "write_findings", findings=[VALID_FINDING])  # lines 10-12
    adjacent = {**VALID_FINDING, "id": "f2", "line_start": 13, "line_end": 15}
    result = call(context, "write_findings", findings=[adjacent])
    assert "overlaps" not in result


def test_two_overlapping_findings_in_the_same_call_are_both_noted(context: ToolContext) -> None:
    """The pool grows as the batch is walked, so an overlap within one call is
    caught too, not only overlaps against an earlier, separate call."""
    first = {**VALID_FINDING, "id": "f1"}
    second = {**VALID_FINDING, "id": "f2", "line_start": 11, "line_end": 13}
    result = call(context, "write_findings", findings=[first, second])
    assert context.findings == [first, second], "both are still recorded"
    assert "overlaps" in result
    assert "f1" in result


# ------------------------------------------------------------------ the loop


class ScriptedSteps:
    """A minimal adapter that returns a fixed list of steps."""

    name = "test-scripted"
    version = "0.0.0"

    def __init__(self, steps: list[Step]) -> None:
        self._steps = list(steps)
        self.seen: list[Observation] = []
        self.tool_names_by_step: list[tuple[str, ...]] = []

    def next_step(
        self, *, tools: Sequence[dict[str, Any]], transcript: Sequence[Observation]
    ) -> Step:
        self.tool_names_by_step.append(tuple(str(tool["name"]) for tool in tools))
        self.seen = list(transcript)
        if not self._steps:
            return Step(stop=TerminationReason.COMPLETED)
        return self._steps.pop(0)


def test_the_adapter_protocol_is_satisfied_structurally() -> None:
    assert isinstance(ScriptedSteps([]), AgentAdapter)


def test_an_expected_failure_is_fed_back_without_ending_the_run(context: ToolContext) -> None:
    """Failing to find a file is something agents do, not a reason to stop."""
    adapter = ScriptedSteps(
        [
            Step(invocation=ToolInvocation("read_file", {"path": "src/nope.py"})),
            Step(invocation=ToolInvocation("write_findings", {"findings": [VALID_FINDING]})),
            Step(stop=TerminationReason.COMPLETED),
        ]
    )
    result = run_agent(adapter, context=context)

    assert result.termination_reason is TerminationReason.COMPLETED
    assert result.tool_calls == 2
    assert any(observation.is_error for observation in adapter.seen)


def test_three_consecutive_unexpected_exceptions_end_the_run(context: ToolContext) -> None:
    """A harness that is throwing produces numbers nobody should trust."""

    def explode(_context: ToolContext, _args: dict[str, Any]) -> str:
        raise RuntimeError("harness is broken")

    original = TOOLS_BY_NAME["read_file"]
    TOOLS_BY_NAME["read_file"] = ToolSpecWithHandler(original, explode)
    try:
        adapter = ScriptedSteps(
            [Step(invocation=ToolInvocation("read_file", {"path": "src/auth.py"}))] * 5
        )
        result = run_agent(adapter, context=context)
    finally:
        TOOLS_BY_NAME["read_file"] = original

    assert result.termination_reason is TerminationReason.HARNESS_ERROR
    assert result.termination_reason.is_invalid
    assert result.tool_calls == UNEXPECTED_LIMIT


def test_an_expected_failure_resets_the_unexpected_count(context: ToolContext) -> None:
    """Three in a row, not three in total: an intervening ordinary failure is
    evidence the harness is still working."""
    calls = {"n": 0}

    def sometimes(_context: ToolContext, _args: dict[str, Any]) -> str:
        calls["n"] += 1
        if calls["n"] == 3:
            raise ToolFailure("ordinary failure")
        raise RuntimeError("unexpected")

    original = TOOLS_BY_NAME["read_file"]
    TOOLS_BY_NAME["read_file"] = ToolSpecWithHandler(original, sometimes)
    try:
        adapter = ScriptedSteps(
            [Step(invocation=ToolInvocation("read_file", {"path": "src/auth.py"}))] * 4
            + [Step(stop=TerminationReason.COMPLETED)]
        )
        result = run_agent(adapter, context=context)
    finally:
        TOOLS_BY_NAME["read_file"] = original

    assert result.termination_reason is not TerminationReason.HARNESS_ERROR


def test_naming_an_unoffered_tool_is_a_scored_capacity_failure(
    context: ToolContext,
) -> None:
    adapter = ScriptedSteps([Step(invocation=ToolInvocation("no_such_tool", {}))])
    result = run_agent(adapter, context=context)
    assert result.termination_reason is TerminationReason.STEP_EXHAUSTED
    assert not result.termination_reason.is_invalid
    assert result.tool_calls == 0


def test_a_failing_adapter_is_an_adapter_error(context: ToolContext) -> None:
    class Broken:
        name = "broken"
        version = "0.0.0"

        def next_step(self, **_: Any) -> Step:
            raise RuntimeError("adapter blew up")

    result = run_agent(Broken(), context=context)
    assert result.termination_reason is TerminationReason.ADAPTER_ERROR
    assert result.termination_reason.is_invalid


def test_an_explicit_completed_stop_can_have_zero_findings(context: ToolContext) -> None:
    result = run_agent(ScriptedSteps([Step(stop=TerminationReason.COMPLETED)]), context=context)
    assert result.termination_reason is TerminationReason.COMPLETED
    assert result.findings == []


def test_an_explicit_no_output_stop_stays_no_output(context: ToolContext) -> None:
    result = run_agent(ScriptedSteps([Step(stop=TerminationReason.NO_OUTPUT)]), context=context)
    assert result.termination_reason is TerminationReason.NO_OUTPUT


def test_the_tool_call_budget_ends_the_run(context: ToolContext) -> None:
    adapter = ScriptedSteps(
        [
            Step(invocation=ToolInvocation("read_file", {"path": "src/auth.py"})),
            Step(invocation=ToolInvocation("write_findings", {"findings": [VALID_FINDING]})),
            Step(invocation=ToolInvocation("read_file", {"path": "src/auth.py"})),
        ]
    )
    result = run_agent(adapter, context=context, budget=Budget(max_tool_calls=2))
    assert result.termination_reason is TerminationReason.STEP_EXHAUSTED
    assert result.tool_calls == 2


def test_zero_tool_capacity_starts_in_tool_free_finalization(context: ToolContext) -> None:
    adapter = ScriptedSteps([Step(stop=TerminationReason.COMPLETED)])
    result = run_agent(adapter, context=context, budget=Budget(max_tool_calls=0))
    assert result.termination_reason is TerminationReason.COMPLETED
    assert result.tool_calls == 0
    assert adapter.tool_names_by_step == [()]


def test_the_last_executable_slot_is_reserved_for_write_findings(
    context: ToolContext,
) -> None:
    adapter = ScriptedSteps(
        [
            Step(invocation=ToolInvocation("read_file", {"path": "src/auth.py"})),
            Step(stop=TerminationReason.COMPLETED),
        ]
    )
    run_agent(adapter, context=context, budget=Budget(max_tool_calls=2))
    assert adapter.tool_names_by_step[0] == tuple(tool["name"] for tool in model_schemas())
    assert adapter.tool_names_by_step[1] == ("write_findings",)


@pytest.mark.parametrize("findings", [[VALID_FINDING], []], ids=["accepted", "rejected"])
def test_any_write_findings_attempt_forces_tool_free_finalization(
    context: ToolContext, findings: list[dict[str, Any]]
) -> None:
    adapter = ScriptedSteps(
        [
            Step(invocation=ToolInvocation("write_findings", {"findings": findings})),
            Step(stop=TerminationReason.COMPLETED),
        ]
    )
    run_agent(adapter, context=context, budget=Budget(max_tool_calls=12))
    assert adapter.tool_names_by_step[1] == ()


def test_a_registered_tool_withheld_by_the_phase_is_never_executed(
    context: ToolContext,
) -> None:
    adapter = ScriptedSteps(
        [Step(invocation=ToolInvocation("read_file", {"path": "src/auth.py"}))]
    )
    result = run_agent(adapter, context=context, budget=Budget(max_tool_calls=1))
    assert adapter.tool_names_by_step == [("write_findings",)]
    assert result.termination_reason is TerminationReason.STEP_EXHAUSTED
    assert result.tool_calls == 0


def test_the_token_budget_ends_the_run(context: ToolContext) -> None:
    adapter = ScriptedSteps(
        [
            Step(
                invocation=ToolInvocation("read_file", {"path": "src/auth.py"}),
                usage={"total_tokens": 60},
            )
        ]
        * 10
    )
    result = run_agent(adapter, context=context, budget=Budget(max_tokens=100))
    assert result.termination_reason is TerminationReason.BUDGET_EXHAUSTED_TOKENS


def test_the_wallclock_budget_ends_the_run(context: ToolContext) -> None:
    ticks = iter([0.0, 0.0, 5.0, 5.0, 5.0, 5.0])

    adapter = ScriptedSteps(
        [Step(invocation=ToolInvocation("read_file", {"path": "src/auth.py"}))] * 10
    )
    result = run_agent(
        adapter,
        context=context,
        budget=Budget(max_wallclock_seconds=1.0),
        clock=lambda: next(ticks, 99.0),
    )
    assert result.termination_reason is TerminationReason.BUDGET_EXHAUSTED_WALLCLOCK


# ----------------------------------------------------------------- reasons


def test_error_reasons_are_invalid_and_scored_reasons_are_not() -> None:
    for reason in (
        TerminationReason.ADAPTER_ERROR,
        TerminationReason.PROVIDER_ERROR,
        TerminationReason.SANDBOX_ERROR,
        TerminationReason.HARNESS_ERROR,
    ):
        assert reason.is_invalid and not reason.is_scored

    for reason in (TerminationReason.COMPLETED, TerminationReason.PARTIAL):
        assert reason.is_scored and not reason.is_invalid

    for reason in (
        TerminationReason.NO_OUTPUT,
        TerminationReason.STEP_EXHAUSTED,
        TerminationReason.BUDGET_EXHAUSTED_TOKENS,
    ):
        assert not reason.is_scored and not reason.is_invalid


def test_a_step_must_either_act_or_stop() -> None:
    with pytest.raises(ValueError):
        Step()
    with pytest.raises(ValueError):
        Step(invocation=ToolInvocation("read_file", {}), stop=TerminationReason.COMPLETED)


# ------------------------------------------------------------------- trace


def test_the_trace_records_the_call_and_its_result(context: ToolContext) -> None:
    recorder = Recorder()
    adapter = ScriptedSteps(
        [
            Step(invocation=ToolInvocation("read_file", {"path": "src/auth.py"})),
            Step(invocation=ToolInvocation("write_findings", {"findings": [VALID_FINDING]})),
            Step(stop=TerminationReason.COMPLETED),
        ]
    )
    run_agent(adapter, context=context, recorder=recorder)

    events = [event["event"] for event in recorder.events]
    assert events.count("tool_call") == 2
    assert events.count("tool_result") == 2
    assert events[-1] == "termination"
    assert "findings_submitted" in events


def test_findings_arguments_are_not_projected_into_args_safe(context: ToolContext) -> None:
    """Findings reach the trace through their own event, which is reviewed.

    Copying them into `args_safe` as well would publish them by a second route
    that nothing decided to publish.
    """
    recorder = Recorder()
    adapter = ScriptedSteps(
        [
            Step(invocation=ToolInvocation("write_findings", {"findings": [VALID_FINDING]})),
            Step(stop=TerminationReason.COMPLETED),
        ]
    )
    run_agent(adapter, context=context, recorder=recorder)

    call_event = next(e for e in recorder.events if e["event"] == "tool_call")
    assert call_event["payload"]["args_safe"] == {}
    assert call_event["payload"]["args_hash"]


class ToolSpecWithHandler:
    """A ToolSpec with its handler swapped, for exercising failure paths."""

    def __init__(self, original: Any, handler: Any) -> None:
        self.name = original.name
        self.description = original.description
        self.parameters = original.parameters
        self.handler = handler
