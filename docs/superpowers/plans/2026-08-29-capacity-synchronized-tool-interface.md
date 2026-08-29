# Capacity-Synchronized Tool Interface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every provider call expose only executable tools supported by the remaining registered capacity, reserve the final executable call for `write_findings`, and retain public evidence of that interface state.

**Architecture:** The harness loop owns a deterministic three-phase tool-interface state machine and annotates every `llm_call` with its pre-call state. Both live adapters translate an empty schema list into a tool-free provider request, while the evaluator preserves each run's redaction-manifest provenance. The paid smoke sequence remains a separately authorized, one-clean/one-B-001 procedure after all offline gates pass.

**Tech Stack:** Python 3.12+, dataclasses, `httpx` mock transports, pytest, Ruff, strict mypy, JSONL trace sanitizer, uv.

## Global Constraints

- Formal executable tool-call limit remains exactly 12; aggregate token stop remains 120,000; wall-clock stop remains 300 seconds; estimated-cost stop remains USD 0.035 per run; provider-side exposure remains USD 0.05 per run.
- Formal provider identity remains OpenAI Responses, `gpt-5.6-luna`, reasoning `low`, maximum output tokens per request 1,024, TaskQ 1.0.6 and its existing immutable OCI identity.
- New behavior identity is adapter `0.5.0`, prompt `0.3.0`, redaction manifest `0.2.0`; trace schema remains `0.2.0`.
- Six historical paid attempts are immutable: never delete, rewrite, regenerate, relabel, or backfill them.
- No credential lookup or paid provider request occurs during implementation or offline verification.
- Paid order is exactly `fx-taskq-py/clean` once, then only after a completed zero-finding clean gate `fx-taskq-py/B-001` once; no other mutation, fallback, changed task, or rerun.
- B-001 automated success requires `completed` plus at least one successful `write_findings` tool result; candidate findings are not human-verified detections.
- Codex, LLMs, and scripts never act as primary reviewer, independent reviewer, resolver, or adjudicator.
- Do not push, tag, release, create a remote, modify another repository, or publish external artifacts.

---

## File responsibility map

- `src/coding_agent_eval/agent/loop.py`: select and enforce the phase-specific tool interface; annotate calls with capacity state.
- `tests/agent/test_tool_surface.py`: state-machine, reservation, enforcement, finalization, and raw trace contracts.
- `src/coding_agent_eval/agent/provider.py`: prompt `0.3.0`, chat adapter `0.5.0`, and tool-free Chat Completions request shape.
- `src/coding_agent_eval/agent/responses_provider.py`: Responses adapter `0.5.0` and tool-free Responses request shape.
- `tests/agent/test_provider.py`: prompt and Chat Completions request-shape regression tests.
- `tests/agent/test_responses_provider.py`: Responses request-shape regression tests.
- `src/coding_agent_eval/trace/allowlist.py`: classify the four interface-state fields as public.
- `src/coding_agent_eval/__init__.py`: advance the redaction manifest to `0.2.0` without changing trace or benchmark identity.
- `src/coding_agent_eval/evaluator/metrics.py`: report the source run's redaction-manifest version rather than the current writer constant.
- `src/coding_agent_eval/evaluator/replay.py`: carry `run_header.redaction_manifest_version` into `RunContext`.
- `tests/trace/test_public_trace.py`: public projection of interface state and continued removal of provider bodies.
- `tests/trace/test_sanitizer_failclosed.py`: sanitizer acceptance of safe fields and rejection of adjacent unknown/private content.
- `tests/evaluator/test_metrics.py` and `tests/evaluator/test_replay_determinism.py`: current-version default and historical-version preservation.
- `docs/PAID_SMOKE_PLAN.md`: append the frozen attempt-7 identity, budgets, order, stop rules, and authorization boundary without editing attempts 1-6.
- `release-manifest.json`: mechanically refresh the changed paid-plan artifact hash; this is an index update, not a tag or release action.

### Task 1: Harness-owned phase state machine

**Files:**
- Modify: `tests/agent/test_tool_surface.py`
- Modify: `src/coding_agent_eval/agent/loop.py`

**Interfaces:**
- Consumes: `Budget.max_tool_calls`, the deterministic list from `model_schemas()`, executed `tool_calls`, and whether `write_findings` has been attempted.
- Produces: private `_ToolInterface` with `mode: str`, `tools: tuple[dict[str, Any], ...]`, `limit: int | None`, `remaining: int | None`, and `names: frozenset[str]`; `_select_tool_interface(...) -> _ToolInterface`.

- [ ] **Step 1: Extend the scripted adapter to retain each advertised interface**

Add to `ScriptedSteps.__init__` and `next_step`:

```python
self.tool_names_by_step: list[tuple[str, ...]] = []

# at the start of next_step
self.tool_names_by_step.append(tuple(str(tool["name"]) for tool in tools))
```

- [ ] **Step 2: Write failing tests for all three phases**

Add tests using `all_names = tuple(tool["name"] for tool in model_schemas())`:

```python
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
```

- [ ] **Step 3: Write the failing enforcement test**

```python
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
```

Replace the existing tool-budget test body with:

```python
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
```

Replace `test_naming_an_unknown_tool_does_not_count_as_a_harness_fault` with an explicit unoffered-interface contract:

```python
def test_naming_an_unoffered_tool_is_a_scored_capacity_failure(
    context: ToolContext,
) -> None:
    adapter = ScriptedSteps([Step(invocation=ToolInvocation("no_such_tool", {}))])
    result = run_agent(adapter, context=context)
    assert result.termination_reason is TerminationReason.STEP_EXHAUSTED
    assert not result.termination_reason.is_invalid
    assert result.tool_calls == 0
```

- [ ] **Step 4: Run the new loop tests and confirm the expected red state**

Run:

```powershell
uv run pytest tests/agent/test_tool_surface.py -k "capacity or reserved or finalization or withheld or tool_call_budget" -v
```

Expected: the new assertions fail because every provider step currently receives all four schemas.

- [ ] **Step 5: Implement `_ToolInterface` and `_select_tool_interface`**

Add beside the loop constants:

```python
@dataclass(frozen=True)
class _ToolInterface:
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
    remaining = (
        None if max_tool_calls is None else max(max_tool_calls - tool_calls, 0)
    )
    if write_findings_attempted or remaining == 0:
        return _ToolInterface("finalization", (), max_tool_calls, remaining)
    if remaining == 1:
        report_tool = tuple(tool for tool in all_tools if tool["name"] == "write_findings")
        if len(report_tool) != 1:
            raise RuntimeError("write_findings must be registered exactly once")
        return _ToolInterface("report_only", report_tool, max_tool_calls, remaining)
    return _ToolInterface("review", all_tools, max_tool_calls, remaining)
```

In `run_agent`, freeze `all_tools = tuple(model_schemas())`, initialize `write_findings_attempted = False`, select the interface before every `next_step`, and pass `interface.tools` to the adapter.

After extracting `invocation`, reject `invocation.tool_name not in interface.names` as `STEP_EXHAUSTED` before incrementing `tool_calls` or calling `_run_one_tool`. Set `write_findings_attempted = True` before executing an offered `write_findings` handler.

- [ ] **Step 6: Run the loop contract tests**

Run:

```powershell
uv run pytest tests/agent/test_tool_surface.py -v
```

Expected: all selected tests pass, including the pre-existing completion, tool-error, and budget contracts.

- [ ] **Step 7: Commit the state machine**

```powershell
git add src/coding_agent_eval/agent/loop.py tests/agent/test_tool_surface.py
git commit -m "fix: synchronize tools with executable capacity"
```

### Task 2: Explicit provider finalization requests and version identity

**Files:**
- Modify: `tests/agent/test_provider.py`
- Modify: `tests/agent/test_responses_provider.py`
- Modify: `src/coding_agent_eval/agent/provider.py`
- Modify: `src/coding_agent_eval/agent/responses_provider.py`

**Interfaces:**
- Consumes: the phase-selected `Sequence[dict[str, Any]]` passed through the existing `AgentAdapter.next_step` protocol.
- Produces: request payloads with tool members only when the sequence is non-empty; adapter `0.5.0`; prompt `0.3.0`.

- [ ] **Step 1: Write failing Chat Completions request-shape tests**

```python
def test_chat_finalization_request_omits_every_tool_member() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=completion(tool_name=None))

    step = adapter_with(handler).next_step(tools=[], transcript=[])
    assert step.stop is TerminationReason.COMPLETED
    assert not {"tools", "tool_choice", "parallel_tool_calls"} & captured.keys()


def test_chat_review_request_keeps_the_single_action_tool_contract() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=completion(tool_name=None))

    adapter_with(handler).next_step(tools=model_schemas(), transcript=[])
    assert len(captured["tools"]) == 4
    assert captured["tool_choice"] == "auto"
    assert captured["parallel_tool_calls"] is False
```

Import `model_schemas` in the test module.

- [ ] **Step 2: Write failing Responses request-shape tests**

Import `model_schemas` and add:

```python
def test_responses_finalization_request_omits_every_tool_member() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=response(tool_name=None))

    step = adapter_with(handler).next_step(tools=[], transcript=[])
    assert step.stop is TerminationReason.COMPLETED
    assert not {"tools", "tool_choice", "parallel_tool_calls"} & captured.keys()


def test_responses_review_request_keeps_the_single_action_tool_contract() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=response(tool_name=None))

    adapter_with(handler).next_step(tools=model_schemas(), transcript=[])
    assert len(captured["tools"]) == 4
    assert all(tool["type"] == "function" for tool in captured["tools"])
    assert captured["tool_choice"] == "auto"
    assert captured["parallel_tool_calls"] is False
```

- [ ] **Step 3: Tighten the prompt contract test**

Extend `test_system_prompt_defines_both_completion_paths_and_the_tool_budget` to assert these exact phrases:

```python
assert "only write_findings" in prompt
assert "no tools are available" in prompt
assert "return a final response" in prompt
```

Keep assertions that a bounded prompt records 12 calls and an unbounded prompt invents no count.

- [ ] **Step 4: Run provider tests and confirm red state**

```powershell
uv run pytest tests/agent/test_provider.py tests/agent/test_responses_provider.py -k "finalization or review_request or system_prompt" -v
```

Expected: empty-tool payload assertions fail because both adapters currently emit tool members, and prompt assertions fail under prompt `0.2.0`.

- [ ] **Step 5: Implement conditional request members and new identity**

In each adapter, build the base payload first, then add tool members only when `tools` is truthy:

```python
# Chat Completions
payload: dict[str, Any] = {
    "model": self.model,
    "messages": build_messages(transcript, system_prompt=self.system_prompt),
}
if tools:
    payload.update(
        tools=[{"type": "function", "function": schema} for schema in tools],
        tool_choice="auto",
        parallel_tool_calls=False,
    )

# Responses
payload = {
    "model": self.model,
    "input": build_input(transcript, system_prompt=self.system_prompt),
    "store": False,
}
if tools:
    payload.update(
        tools=responses_tool_schemas(tools),
        tool_choice="auto",
        parallel_tool_calls=False,
    )
```

Set both adapter constants to `0.5.0` and `SYSTEM_PROMPT_VERSION` to `0.3.0`. Render a static explanatory prompt that states:

```text
The available tool interface reflects the current phase. If only write_findings is
available, submit all supported defects once or return a clean final response. If no
tools are available, return the final response now and do not request a tool.
```

Implement the complete prompt body as:

```python
return (
    "You are reviewing a code tree for defects. This is a selective review, not "
    "a proof that no defect exists. "
    + budget
    + "Report only defects supported by evidence and cite the file and line range. "
    "The available tool interface reflects the current phase. If inspection tools "
    "are available, use them selectively. If only write_findings is available, "
    "submit all supported defects in one write_findings call, then return a final "
    "response without a tool call; if none were found, return a final response "
    "without calling write_findings. If no tools are available, return a final "
    "response now without a tool call and do not request a tool. Do not keep reading "
    "solely to prove the absence of defects."
)
```

- [ ] **Step 6: Run both complete adapter suites**

```powershell
uv run pytest tests/agent/test_provider.py tests/agent/test_responses_provider.py -v
```

Expected: all mock-only tests pass; no network or key is used.

- [ ] **Step 7: Commit provider finalization behavior**

```powershell
git add src/coding_agent_eval/agent/provider.py src/coding_agent_eval/agent/responses_provider.py tests/agent/test_provider.py tests/agent/test_responses_provider.py
git commit -m "fix: provide a tool-free final response phase"
```

### Task 3: Capacity evidence and redaction provenance

**Files:**
- Modify: `tests/agent/test_tool_surface.py`
- Modify: `src/coding_agent_eval/agent/loop.py`
- Modify: `tests/trace/test_public_trace.py`
- Modify: `tests/trace/test_sanitizer_failclosed.py`
- Modify: `src/coding_agent_eval/trace/allowlist.py`
- Modify: `src/coding_agent_eval/__init__.py`
- Modify: `tests/evaluator/test_metrics.py`
- Modify: `tests/evaluator/test_replay_determinism.py`
- Modify: `src/coding_agent_eval/evaluator/metrics.py`
- Modify: `src/coding_agent_eval/evaluator/replay.py`

**Interfaces:**
- Consumes: `_ToolInterface` selected before each adapter call and `run_header.redaction_manifest_version`.
- Produces: four public `llm_call` fields and `RunContext.redaction_manifest_version` that preserves source-run provenance.

- [ ] **Step 1: Write the failing raw loop trace test**

```python
def test_each_llm_call_records_pre_call_capacity_and_interface(context: ToolContext) -> None:
    recorder = Recorder()
    adapter = ScriptedSteps(
        [
            Step(
                invocation=ToolInvocation("read_file", {"path": "src/auth.py"}),
                trace={"request_hash": "a" * 64},
            ),
            Step(stop=TerminationReason.COMPLETED, trace={"request_hash": "b" * 64}),
        ]
    )
    run_agent(adapter, context=context, budget=Budget(max_tool_calls=2), recorder=recorder)
    calls = [event["payload"] for event in recorder.events if event["event"] == "llm_call"]
    assert calls[0]["executable_tool_call_limit"] == 2
    assert calls[0]["executable_tool_calls_remaining"] == 2
    assert calls[0]["interface_mode"] == "review"
    assert calls[0]["tools_offered"] == [tool["name"] for tool in model_schemas()]
    assert calls[1]["executable_tool_calls_remaining"] == 1
    assert calls[1]["interface_mode"] == "report_only"
    assert calls[1]["tools_offered"] == ["write_findings"]
```

- [ ] **Step 2: Write failing public projection and sanitizer tests**

Add a raw `llm_call` payload containing the four safe fields plus `request_body` and `response_body`. Assert that `project_record` retains exactly the four fields plus existing public metadata and drops both bodies. Add the same event to `clean_events()` and assert `sanitize_events` writes a public JSONL containing the capacity fields but not a marker placed inside either private body.

Use these concrete tests:

```python
def test_interface_capacity_is_public_and_provider_bodies_stay_private() -> None:
    safe = {
        "request_hash": "a" * 64,
        "latency_ms": 10,
        "finish_reason": "completed",
        "usage": {},
        "executable_tool_call_limit": 12,
        "executable_tool_calls_remaining": 0,
        "interface_mode": "finalization",
        "tools_offered": [],
    }
    public = project_record(
        record(
            "llm_call",
            {**safe, "request_body": {"private": "request"}, "response_body": {"private": "response"}},
        )
    )
    assert public["payload"] == safe


def test_sanitizer_keeps_interface_capacity_but_drops_provider_bodies(
    tmp_path: Path,
) -> None:
    raw = clean_events()
    raw[1]["seq"] = 2
    raw[2]["seq"] = 3
    raw.insert(
        1,
        event(
            1,
            "llm_call",
            {
                "request_hash": "a" * 64,
                "latency_ms": 10,
                "finish_reason": "completed",
                "usage": {},
                "executable_tool_call_limit": 12,
                "executable_tool_calls_remaining": 0,
                "interface_mode": "finalization",
                "tools_offered": [],
                "request_body": {"marker": "PRIVATE_REQUEST_BODY"},
                "response_body": {"marker": "PRIVATE_RESPONSE_BODY"},
            },
        ),
    )
    output = tmp_path / "trace.jsonl"
    sanitize_events(raw, output)
    text = output.read_text(encoding="utf-8")
    assert "PRIVATE_REQUEST_BODY" not in text
    assert "PRIVATE_RESPONSE_BODY" not in text
    call = next(
        json.loads(line)["payload"]
        for line in text.splitlines()
        if json.loads(line)["event"] == "llm_call"
    )
    assert call["interface_mode"] == "finalization"
    assert call["tools_offered"] == []
```

Add `json` to `test_sanitizer_failclosed.py` imports.

- [ ] **Step 3: Write failing redaction-version provenance tests**

In `test_metrics.py`:

```python
def test_results_use_the_context_redaction_manifest_version() -> None:
    context = deepcopy(CONTEXT)
    context.redaction_manifest_version = "0.1.0"
    result = score([], [], ledger_of([]), context=context)
    assert result.as_dict()["redaction_manifest_version"] == "0.1.0"
```

Also import `REDACTION_MANIFEST_VERSION` and assert the default current context uses it:

```python
def test_new_context_defaults_to_the_current_redaction_manifest() -> None:
    assert CONTEXT.redaction_manifest_version == REDACTION_MANIFEST_VERSION
```

In `test_replay_determinism.py`, extend the historical golden replay assertion:

```python
assert result.context.redaction_manifest_version == "0.1.0"
assert result.as_dict()["redaction_manifest_version"] == "0.1.0"
```

- [ ] **Step 4: Run focused evidence tests and confirm red state**

```powershell
uv run pytest tests/agent/test_tool_surface.py tests/trace/test_public_trace.py tests/trace/test_sanitizer_failclosed.py tests/evaluator/test_metrics.py tests/evaluator/test_replay_determinism.py -k "capacity or interface or redaction_manifest" -v
```

Expected: missing `llm_call` fields, unknown-field sanitizer failures, and missing `RunContext` provenance cause failures.

- [ ] **Step 5: Annotate every emitted provider call**

Add to `_ToolInterface`:

```python
def trace_payload(self) -> dict[str, Any]:
    return {
        "executable_tool_call_limit": self.limit,
        "executable_tool_calls_remaining": self.remaining,
        "interface_mode": self.mode,
        "tools_offered": [str(tool["name"]) for tool in self.tools],
    }
```

Merge this after `step.trace` and before `usage` in the `llm_call` payload, so adapter-supplied data cannot override harness-owned capacity evidence:

```python
recorder.emit(
    "llm_call",
    {**step.trace, **interface.trace_payload(), "usage": dict(step.usage)},
)
```

- [ ] **Step 6: Classify the fields and advance only the redaction identity**

Add these exact public fields under `PUBLIC_FIELDS["llm_call"]`:

```python
"executable_tool_call_limit",
"executable_tool_calls_remaining",
"interface_mode",
"tools_offered",
```

Set `REDACTION_MANIFEST_VERSION = "0.2.0"`. Do not change `TRACE_SCHEMA_VERSION`, `BENCHMARK_VERSION`, or historical JSONL files.

- [ ] **Step 7: Preserve source-run redaction identity through replay**

Add to `RunContext`:

```python
redaction_manifest_version: str = REDACTION_MANIFEST_VERSION
```

Change `ScoredRun.as_dict()` to emit `self.context.redaction_manifest_version`. In replay construction, pass:

```python
redaction_manifest_version=str(header["redaction_manifest_version"]),
```

This keeps old `0.1.0` traces replayable as `0.1.0` while new runs default to `0.2.0`.

- [ ] **Step 8: Run complete evidence and evaluator test groups**

```powershell
uv run pytest tests/trace tests/evaluator tests/agent/test_tool_surface.py tests/test_live_run.py -v
```

Expected: all pass; historical golden files and six paid evidence directories remain unchanged.

- [ ] **Step 9: Commit evidence contract changes**

```powershell
git add src/coding_agent_eval/agent/loop.py src/coding_agent_eval/trace/allowlist.py src/coding_agent_eval/__init__.py src/coding_agent_eval/evaluator/metrics.py src/coding_agent_eval/evaluator/replay.py tests/agent/test_tool_surface.py tests/trace/test_public_trace.py tests/trace/test_sanitizer_failclosed.py tests/evaluator/test_metrics.py tests/evaluator/test_replay_determinism.py
git commit -m "feat: trace tool interface capacity"
```

### Task 4: Freeze the one-shot formal validation record

**Files:**
- Modify: `docs/PAID_SMOKE_PLAN.md`
- Modify: `release-manifest.json` through the deterministic repository script

**Interfaces:**
- Consumes: verified adapter/prompt/redaction identities and the existing attempt-6 retained outcome.
- Produces: a repository-tracked, authorization-pending attempt-7 record that cannot be expanded after seeing outputs.

- [ ] **Step 1: Append a new section without modifying attempts 1-6**

Append `## Frozen remediation validation — attempt 7 (authorization pending)` containing:

```markdown
- Clean: `fx-taskq-py/clean`, TaskQ 1.0.6, exactly once.
- Conditional mutated: only `fx-taskq-py/B-001`, exactly once, only after clean is
  `completed` with zero candidate findings.
- No other seven mutations, fallback mutation, task substitution, or rerun.
- Adapter/prompt/redaction identity: `openai-responses@0.5.0`, prompt `0.3.0`,
  redaction manifest `0.2.0`, trace schema `0.2.0`.
- Fixed run limits: 120,000 aggregate tokens, 12 executable tool calls, 300 seconds,
  USD 0.035 estimated-cost stop, 1,024 output tokens per request, USD 0.05 maximum
  provider-side exposure per run.
- B-001 automated gate: `completed` and at least one successful `write_findings`
  tool result. Candidate findings remain unverified pending dual blinded humans.
- Any failed gate retains complete evidence and stops. Authorization is not inherited
  from attempts 1-6 and must be explicit after offline gates pass.
```

Also record that adapter `0.5.0` is a new behavior identity because request-time tool availability changes, while TaskQ and its OCI identity do not change.

- [ ] **Step 2: Verify the historical section was append-only**

```powershell
git diff --word-diff=porcelain HEAD^ -- docs/PAID_SMOKE_PLAN.md
git diff --check
```

Expected: additions occur after the complete attempt-6 retained outcome; no previous attempt text or hash changes.

- [ ] **Step 3: Refresh and verify the deterministic artifact index**

```powershell
uv run python scripts/build_release_manifest.py
uv run pytest tests/test_release_audit.py -q
git diff --check
```

Expected: only the `docs/PAID_SMOKE_PLAN.md` entry's byte count and SHA-256 change in `release-manifest.json`; the audit tests pass.

- [ ] **Step 4: Commit the frozen plan**

```powershell
git add docs/PAID_SMOKE_PLAN.md release-manifest.json
git commit -m "docs: freeze bounded TaskQ validation sequence"
```

### Task 5: Offline verification and immutable-evidence audit

**Files:**
- No source changes expected; any discovered regression returns to the owning task with a new failing test.

**Interfaces:**
- Consumes: commits from Tasks 1-4.
- Produces: secret-free evidence that implementation, privacy, typing, packaging, fixture, and release contracts pass before the paid authorization boundary.

- [ ] **Step 1: Run focused remediation tests once more**

```powershell
uv run pytest tests/agent/test_tool_surface.py tests/agent/test_provider.py tests/agent/test_responses_provider.py tests/trace/test_public_trace.py tests/trace/test_sanitizer_failclosed.py tests/evaluator/test_metrics.py tests/evaluator/test_replay_determinism.py tests/test_live_run.py -q
```

Expected: zero failures.

- [ ] **Step 2: Run the full default offline suite**

```powershell
uv run pytest -q
```

Expected: zero failures; only repository-declared skips/deselections.

- [ ] **Step 3: Run static and packaging gates**

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict src
uv build
```

Expected: all commands exit zero.

- [ ] **Step 4: Run fixture and publication audits without provider credentials**

```powershell
uv run cae validate fixtures
uv run cae fixture verify fixtures/fx-taskq-py
uv run cae fixture verify fixtures/fx-ledger-ts
uv run cae release audit --publication
```

Expected: all commands exit zero and no environment file is supplied.

- [ ] **Step 5: Run Docker-marked offline contracts when the local daemon is available**

```powershell
docker info
uv run pytest -q -m docker
```

If `docker info` itself reports that no daemon is available, record that environment limitation; do not weaken or rewrite tests. If available, all Docker tests must pass.

- [ ] **Step 6: Prove the six retained attempts were not edited**

```powershell
git diff --name-only origin/main..HEAD -- runs/smoke
git status --short
git log --oneline --decorate origin/main..HEAD
```

Expected: the first command prints nothing; the worktree is clean; only the isolated local remediation commits are ahead of `origin/main`.

- [ ] **Step 7: Stop at the paid authorization boundary**

Report the exact offline commands and results, the frozen attempt-7 maximum exposure of USD 0.05 for clean plus a conditional USD 0.05 for B-001, and request explicit authorization. Do not inspect `.env`, construct a credential-bearing adapter, or run `cae run` before that authorization.

## Execution choice

Use inline execution in this session with `superpowers:executing-plans`. The user delegated implementation judgment, and no explicit request authorizes subagent delegation. Execute task-by-task with the commits and verification checkpoints above.
