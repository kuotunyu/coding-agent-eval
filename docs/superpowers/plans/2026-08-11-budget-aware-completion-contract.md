# Budget-aware Completion Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make clean reviews terminate deliberately within a registered tool budget while binding the exact rendered prompt to every new suite identity.

**Architecture:** A pure prompt renderer in `agent/provider.py` turns the registered maximum tool-call count into the shared system prompt used by both OpenAI adapters. `live.build_adapter` is the only composition point. Suite registration records the prompt version and SHA-256, and suite execution recomputes those values before any provider request.

**Tech Stack:** Python 3.12+, dataclasses, hashlib, JSON Schema 2020-12, pytest, httpx MockTransport, Ruff, strict mypy.

## Global Constraints

- Operate only in the existing repository and `main` worktree; create no branch, clone, or worktree.
- Use only `kuotunyu <61350295+kuotunyu@users.noreply.github.com>` as author and committer; add no co-author trailer.
- Do not overwrite either retained smoke attempt or the historical reference suite.
- Do not call a paid API while implementing or verifying this plan.
- Keep `write_findings` non-empty and preserve no-tool-call provider completion as the terminal signal.
- New adapters use version `0.3.0`; the new system prompt uses version `0.2.0`.
- Schema-1.0 registrations remain readable but cannot execute with current adapters.

---

### Task 1: Preserve attempt-specific raw-store identity

**Files:**
- Modify: `src/coding_agent_eval/cli.py`
- Test: `tests/test_cli_smoke.py`

**Interfaces:**
- Consumes: an operator-selected `Path` passed to `cae run --out`.
- Produces: `manual_run_id(output: Path) -> str`, a stable `manual-<leaf>-<12 hex>` identifier.

- [ ] **Step 1: Retain the failing regression test**

```python
def test_manual_run_ids_do_not_collide_when_output_parents_differ() -> None:
    first = manual_run_id(Path("runs/smoke/attempt-1/clean"))
    second = manual_run_id(Path("runs/smoke/attempt-2/clean"))
    assert first != second
    assert first == manual_run_id(Path("runs/smoke/attempt-1/clean"))
```

- [ ] **Step 2: Verify path privacy**

```python
def test_manual_run_id_does_not_disclose_an_absolute_output_path() -> None:
    run_id = manual_run_id(Path("C:/Users/private-name/evidence/clean"))
    assert "users" not in run_id
    assert "private" not in run_id
```

- [ ] **Step 3: Keep the minimal implementation and CLI wiring**

```python
def manual_run_id(output: Path) -> str:
    leaf = re.sub(r"[^a-z0-9]+", "-", output.name.lower()).strip("-") or "run"
    digest = hashlib.sha256(output.as_posix().encode("utf-8")).hexdigest()[:12]
    return f"manual-{leaf}-{digest}"
```

Pass `manual_run_id(args.out)` to `live.execute`; never use `args.out.name` directly.

- [ ] **Step 4: Run the focused verification**

Run: `uv run pytest -q tests/test_cli_smoke.py tests/test_live_run.py tests/trace/test_raw_store.py`

Expected: `102 passed` or more, with no network request.

- [ ] **Step 5: Commit**

```powershell
git add -- src/coding_agent_eval/cli.py tests/test_cli_smoke.py
git commit -m "fix: isolate manual raw run identities"
```

### Task 2: Render a finite, explicit completion contract

**Files:**
- Modify: `src/coding_agent_eval/agent/provider.py`
- Modify: `src/coding_agent_eval/agent/responses_provider.py`
- Modify: `src/coding_agent_eval/live.py`
- Test: `tests/agent/test_provider.py`
- Test: `tests/agent/test_responses_provider.py`
- Test: `tests/test_live_run.py`

**Interfaces:**
- Consumes: `max_tool_calls: int | None` from `RunConfiguration.budget`.
- Produces: `render_system_prompt(max_tool_calls: int | None) -> str`.
- Produces: adapters whose `system_prompt` is the rendered text and whose versions are `0.3.0`.

- [ ] **Step 1: Write failing renderer tests**

```python
def test_system_prompt_defines_both_completion_paths_and_the_tool_budget() -> None:
    prompt = render_system_prompt(12)
    assert "at most 12 tool calls" in prompt
    assert "without calling write_findings" in prompt
    assert "one write_findings call" in prompt
    assert "final response without a tool call" in prompt


def test_unbounded_prompt_does_not_invent_a_tool_count() -> None:
    prompt = render_system_prompt(None)
    assert "at most" not in prompt
    assert "finite tool budget" in prompt
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run pytest -q tests/agent/test_provider.py -k system_prompt`

Expected: FAIL because `render_system_prompt` does not exist and the static prompt lacks completion instructions.

- [ ] **Step 3: Implement the pure renderer and version bumps**

```python
SYSTEM_PROMPT_VERSION = "0.2.0"


def render_system_prompt(max_tool_calls: int | None) -> str:
    budget = (
        f"You have at most {max_tool_calls} tool calls; stop before using all of them. "
        if max_tool_calls is not None
        else "You have a finite tool budget; use tools selectively. "
    )
    return (
        "You are reviewing a code tree for defects. This is a selective review, not "
        "a proof that no defect exists. "
        + budget
        + "Report only defects supported by evidence and cite file and line range. "
        "If supported defects exist, submit all of them in one write_findings call, "
        "then return a final response without a tool call. If none were found, return "
        "a final response without calling write_findings. Do not keep reading solely "
        "to prove the absence of defects."
    )
```

Set both adapter versions to `0.3.0`. In `live.build_adapter`, compute the prompt once
with `render_system_prompt(configuration.budget.max_tool_calls)` and pass it to either
adapter.

- [ ] **Step 4: Prove both API shapes receive the same prompt**

Add MockTransport assertions that the first Responses `input` system item and the first
Chat Completions `messages` system item equal `render_system_prompt(12)` when the adapter
is constructed with that prompt.

- [ ] **Step 5: Run focused tests and confirm GREEN**

Run: `uv run pytest -q tests/agent/test_provider.py tests/agent/test_responses_provider.py tests/test_live_run.py`

Expected: all pass without a real API key.

- [ ] **Step 6: Commit**

```powershell
git add -- src/coding_agent_eval/agent/provider.py src/coding_agent_eval/agent/responses_provider.py src/coding_agent_eval/live.py tests/agent/test_provider.py tests/agent/test_responses_provider.py tests/test_live_run.py
git commit -m "fix: define a budget-aware review completion contract"
```

### Task 3: Bind the rendered prompt to suite identity

**Files:**
- Modify: `src/coding_agent_eval/suite.py`
- Modify: `src/coding_agent_eval/cli.py`
- Modify: `schemas/suite-registration.schema.json`
- Test: `tests/test_suite.py`
- Test: `tests/schemas/test_schemas.py`

**Interfaces:**
- Consumes: `adapter.system_prompt` and `SYSTEM_PROMPT_VERSION`.
- Produces: `system_prompt_version: str | None` and `system_prompt_sha256: str | None` on `SuiteRegistration`.

- [ ] **Step 1: Write failing registration tests**

```python
assert document["system_prompt_version"] == "0.2.0"
assert document["system_prompt_sha256"] == (
    "sha256:" + hashlib.sha256(adapter.system_prompt.encode("utf-8")).hexdigest()
)
```

Add a canonical-identity test that changes only `system_prompt_sha256`, writes the
document, and expects `load_registration` to reject its `suite_id`.

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run pytest -q tests/test_suite.py -k 'registration or prompt'`

Expected: FAIL because schema and dataclass lack prompt bindings.

- [ ] **Step 3: Add schema-1.1 fields and registration wiring**

Add these schema properties:

```json
"system_prompt_version": {"type": "string", "minLength": 1, "maxLength": 64},
"system_prompt_sha256": {"$ref": "#/$defs/sha256"}
```

Require both for schema `1.1.0`, omit them when serializing legacy `1.0.0`, and map
missing legacy values to `None` when loading. Build the SHA-256 from the fully rendered
adapter prompt and include both fields in the canonical suite ID.

- [ ] **Step 4: Refuse runtime prompt drift before provider execution**

In `cli.py`, compare the registration fields to the current prompt version and
`sha256:<hex>` digest of `runtime_adapter.system_prompt` in the existing configuration
mismatch guard.

- [ ] **Step 5: Run schema and suite tests and confirm GREEN**

Run: `uv run pytest -q tests/test_suite.py tests/schemas/test_schemas.py`

Expected: all pass; the repository's schema-1.0 reference registration remains
readable and execution remains refused because it lacks current method bindings.

- [ ] **Step 6: Commit**

```powershell
git add -- src/coding_agent_eval/suite.py src/coding_agent_eval/cli.py schemas/suite-registration.schema.json tests/test_suite.py tests/schemas/test_schemas.py
git commit -m "feat: bind system prompts to suite registration"
```

### Task 4: Retain smoke outcomes and close offline verification

**Files:**
- Modify: `docs/PAID_SMOKE_PLAN.md`
- Modify: `docs/MANUAL_RUN.md`
- Modify: `docs/REFERENCE_SUITE.md`
- Modify: `docs/BENCHMARK_CARD.md`
- Modify: `docs/RELEASE_READINESS.md`
- Modify: `release-manifest.json` using `scripts/build_release_manifest.py`
- Preserve: `runs/smoke/smoke-2026-08-11/clean/*`
- Preserve: `runs/smoke/smoke-2026-08-11-attempt-2/clean/*`

**Interfaces:**
- Consumes: two retained terminal outcomes and new adapter/prompt versions.
- Produces: an honest evidence boundary and a green offline release contract.

- [ ] **Step 1: Document attempt 2 without changing attempt 1**

Record exactly: `budget_exhausted_tokens`, 12 LLM/tool calls, 80,522 input tokens,
61,312 cached input tokens, 441 output tokens, zero findings, and USD 0.027987 estimated
cost. State that B-001 was not started and cumulative observed smoke cost is USD
0.041026.

- [ ] **Step 2: Correct the live-method boundary**

Document that adapter `0.3.0` plus prompt `0.2.0` has offline/mock evidence only until a
new paid smoke is explicitly approved. Do not treat either failed smoke or the old
reference suite as evidence for the new method.

- [ ] **Step 3: Run the complete secret-free gate set**

```powershell
$env:CAE_PROVIDER_API_KEY = ''
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv build
uv run cae validate fixtures
uv run cae hygiene leak-scan --tracked
```

Expected: all pass; no paid request.

- [ ] **Step 4: Rebuild and verify the release manifest**

```powershell
uv run python scripts/build_release_manifest.py
uv run cae release audit --publication
git diff --check
```

Expected: publication audit clean except explicitly retained legacy warnings; no stale
manifest finding.

- [ ] **Step 5: Commit**

```powershell
git add -- docs/PAID_SMOKE_PLAN.md docs/MANUAL_RUN.md docs/REFERENCE_SUITE.md docs/BENCHMARK_CARD.md docs/RELEASE_READINESS.md runs/smoke release-manifest.json
git commit -m "docs: retain failed Luna smoke evidence"
```

- [ ] **Step 6: Stop at the next paid boundary**

Report the exact cumulative USD 0.041026, proposed third-attempt configuration, and
remaining exposure. Obtain explicit owner approval before reading the API key for any
new provider request.
