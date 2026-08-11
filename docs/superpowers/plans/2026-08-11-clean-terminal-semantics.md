# Clean Terminal Semantics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make an explicit provider final response a valid `completed` clean-control outcome with zero findings while preserving a genuinely absent response as `no_output`.

**Architecture:** Provider adapters classify API-specific response shapes into explicit terminal reasons. The provider-neutral loop preserves that classification rather than inferring completion from the findings list, and scripted controls state `NO_OUTPUT` directly.

**Tech Stack:** Python 3.12, dataclasses/protocols, httpx `MockTransport`, pytest, Ruff, strict mypy.

## Global Constraints

- Work only in the existing repository, `main`, and sole worktree.
- Preserve attempts 1--5 and the frozen reference suite byte-for-byte.
- Advance both current provider adapter versions from 0.3.0 to 0.4.0; keep prompt 0.2.0.
- Do not make a provider request, create a suite registration, or create any release object.
- Use regression-first TDD and retain the failed attempt 5 outcome without a retry.

---

### Task 1: Specify terminal behavior with failing regressions

**Files:**
- Modify: `tests/agent/test_tool_surface.py`
- Modify: `tests/agent/test_responses_provider.py`
- Modify: `tests/agent/test_provider.py`

**Interfaces:**
- Consumes: `Step(stop=TerminationReason.COMPLETED)` and both provider adapters.
- Produces: executable expectations for final-message completion and missing-message `NO_OUTPUT`.

- [ ] **Step 1: Change the loop-level clean completion expectation**

Replace the old inference test with an explicit completion contract and keep an explicit
no-output control:

```python
def test_an_explicit_completed_stop_can_have_zero_findings(context: ToolContext) -> None:
    result = run_agent(ScriptedSteps([Step(stop=TerminationReason.COMPLETED)]), context=context)
    assert result.termination_reason is TerminationReason.COMPLETED
    assert result.findings == []


def test_an_explicit_no_output_stop_stays_no_output(context: ToolContext) -> None:
    result = run_agent(ScriptedSteps([Step(stop=TerminationReason.NO_OUTPUT)]), context=context)
    assert result.termination_reason is TerminationReason.NO_OUTPUT
```

- [ ] **Step 2: Add Responses shape regressions**

Add one `run_agent` test whose final mock response contains a `message` item and no
findings, expecting `COMPLETED`; add parameterized empty and reasoning-only outputs,
expecting adapter `NO_OUTPUT`.

- [ ] **Step 3: Add Chat Completions shape regressions**

Add one `run_agent` test whose final choice contains an assistant message and no
findings, expecting `COMPLETED`; add missing-choice and missing/non-object-message cases,
expecting adapter `NO_OUTPUT`.

- [ ] **Step 4: Verify RED**

Run:

```powershell
uv run pytest -q tests/agent/test_tool_surface.py `
  tests/agent/test_responses_provider.py tests/agent/test_provider.py
```

Expected: the new clean-completion assertion fails as `no_output`; the missing-message
adapter assertions fail as `completed`. Existing finding-producing completion tests stay
green.

---

### Task 2: Implement explicit adapter and loop classifications

**Files:**
- Modify: `src/coding_agent_eval/agent/loop.py`
- Modify: `src/coding_agent_eval/agent/baseline.py`
- Modify: `src/coding_agent_eval/agent/responses_provider.py`
- Modify: `src/coding_agent_eval/agent/provider.py`
- Test: `tests/agent/test_tool_surface.py`
- Test: `tests/agent/test_responses_provider.py`
- Test: `tests/agent/test_provider.py`

**Interfaces:**
- Consumes: provider-native `output` / `choices[*].message` shapes.
- Produces: `Step.stop` that already distinguishes `COMPLETED` from `NO_OUTPUT`.

- [ ] **Step 1: Preserve explicit loop reasons**

Change `_final_reason` to depend only on the stated reason:

```python
def _final_reason(reason: TerminationReason | None) -> TerminationReason:
    return TerminationReason.NO_OUTPUT if reason is None else reason
```

Update the caller and make `no_output()` return `Step(stop=TerminationReason.NO_OUTPUT)`.

- [ ] **Step 2: Classify Responses final output**

When `calls` is empty, return `COMPLETED` only when `output` contains a dictionary with
`type == "message"`; otherwise return `NO_OUTPUT`. Do not change status, multiple-call,
call-ID, replay, usage, or trace handling.

- [ ] **Step 3: Classify Chat Completions final output**

Before reading tool calls, require a first choice whose `message` is a dictionary. Return
`NO_OUTPUT` when absent/malformed; otherwise preserve the existing assistant-message
completion and tool-call branches.

- [ ] **Step 4: Advance adapter identities**

Set both `ADAPTER_VERSION` constants to `"0.4.0"`. Keep
`SYSTEM_PROMPT_VERSION = "0.2.0"`.

- [ ] **Step 5: Verify GREEN and regressions**

Run:

```powershell
uv run pytest -q tests/agent/test_tool_surface.py `
  tests/agent/test_responses_provider.py tests/agent/test_provider.py `
  tests/test_live_run.py tests/test_suite.py
```

Expected: all selected tests pass with zero failures.

- [ ] **Step 6: Commit the semantic correction**

```powershell
git add src/coding_agent_eval/agent tests/agent tests/test_live_run.py tests/test_suite.py `
  docs/superpowers/specs/2026-08-11-clean-terminal-semantics-design.md `
  docs/superpowers/plans/2026-08-11-clean-terminal-semantics.md
git commit -m "fix(agent): distinguish clean completion from no output"
```

---

### Task 3: Retain attempt 5 and propose—but do not run—the next gate

**Files:**
- Add: `runs/smoke/smoke-2026-08-11-attempt-5/clean/*`
- Modify: `README.md`
- Modify: `docs/PAID_SMOKE_PLAN.md`
- Modify: `docs/BENCHMARK_CARD.md`
- Modify: `docs/DATA_CARD.md`
- Modify: `docs/RELEASE_READINESS.md`
- Modify: `release-manifest.json`

**Interfaces:**
- Consumes: immutable public attempt 5 artifacts and adapter 0.4.0 identity.
- Produces: historically honest release documentation and a non-authorized attempt 6 plan.

- [ ] **Step 1: Record attempt 5 exactly**

Document run `manual-clean-6caee1ee24de`: TaskQ 1.0.6, adapter 0.3.0/prompt
0.2.0, `budget_exhausted_tokens`, 0 findings, 12/12 LLM/tool calls, 84,035 input,
66,663 cached input, 515 output, 238 reasoning tokens, and USD 0.005426 estimated cost.
State that no mutated task or human-review worksheet exists.

- [ ] **Step 2: Define attempt 6 without authorization**

Use adapter 0.4.0/prompt 0.2.0, the same TaskQ 1.0.6 OCI, 1,024 output tokens per
request, 120,000 observed aggregate tokens, 12 tool calls, 300 seconds, and USD 0.035
harness cost stop. Request USD 0.05 maximum provider-side exposure for clean and a
separate conditional USD 0.05 for B-001 only if clean passes. Do not read `.env` or run it.

- [ ] **Step 3: Re-sanitize and validate evidence without printing raw content**

Assert exact call-ID linkage before terminal exhaustion, `store:false`, absent
`previous_response_id`, byte-exact sanitizer replay, zero public private fields, complete
usage, absent mutated output, ignored `.env`/`.run-store`, and no tracked private path.

- [ ] **Step 4: Regenerate deterministic release manifest twice**

Run `uv run python scripts/build_release_manifest.py` twice and compare SHA-256.

---

### Task 4: Full release verification and handoff

**Files:**
- Modify only if generated: `release-manifest.json`

**Interfaces:**
- Consumes: the exact committed adapter correction and retained public outcome.
- Produces: clean pushed `main` and a CI-backed paid-approval boundary.

- [ ] **Step 1: Run local release gates**

Run Ruff, format, strict mypy, full non-Docker pytest, wheel/sdist build, fixture schema
and rebuild, both fixture witness cycles, tracked leak scan, offline and online publication
audits, Docker tests, and `scripts/verify_release.sh`.

- [ ] **Step 2: Verify evidence lineage**

Confirm attempts 1--4 and `runs/reference/` are byte-unchanged, all Git author/committer
identities are only `kuotunyu`, no `Co-authored-by` exists, and only one branch/worktree
exists.

- [ ] **Step 3: Commit and push**

Commit retained evidence/documentation with the official identity, push `main`, and wait
for Ubuntu, Windows, and Docker GitHub CI jobs to pass.

- [ ] **Step 4: Stop at authorization boundaries**

Request explicit attempt 6 authorization. Do not execute paid API calls, full suite,
verified metrics, annotated tag, GitHub Release, Zenodo draft, or Zenodo publication.
