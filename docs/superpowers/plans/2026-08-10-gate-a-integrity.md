# Gate A Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing v0.1 methodology preview internally consistent, schema-valid,
privacy-preserving, replayable, and release-auditable without expanding its corpus or publishing it.

**Architecture:** Add a repository-level contract around the existing fixture/evaluator modules,
then route live evidence through the existing raw store and fail-closed sanitizer. Keep benchmark
data declarative: the task registry references immutable fixture artifacts, and release metadata
references a generated checksum manifest rather than duplicating benchmark content.

**Tech Stack:** Python 3.12+, JSON Schema draft 2020-12, pytest, Docker-backed fixture gates,
GitHub Actions, YAML/JSON/CFF metadata.

## Global Constraints

- Preserve the user's existing `.gitignore` and `ledger/adjudications.jsonl` modifications.
- Do not reset, discard, rewrite history, stage, commit, push, tag, publish, use secrets, or call a
  paid/external provider.
- Do not add fixtures, bugs, languages, providers, dashboards, leaderboards, or model runs.
- Keep all generated public evidence fail-closed and atomic.
- Keep every model-capability result unpublishable until independent adjudication exists.
- Existing historical co-author trailers are reported, never modified.
- Commit steps from the generic planning workflow are intentionally omitted because contributor
  provenance is a release blocker and the user requires GitHub Contributors to contain only
  `kuotunyu`.

---

### Task 1: Repository contract, task registry, and result schema

**Files:**
- Create: `schemas/task.schema.json`
- Create: `tasks/v0.1.json`
- Create: `src/coding_agent_eval/tasks.py`
- Create: `tests/test_tasks.py`
- Modify: `src/coding_agent_eval/schemas/loader.py`
- Modify: `tests/schemas/test_schemas.py`
- Modify: `schemas/results.schema.json`
- Modify: `tests/evaluator/test_ledger.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `validate_task_registry(path: Path, fixture_root: Path) -> list[TaskProblem]`.
- Produces: schema name `task` and a ten-entry v0.1 task registry.
- Preserves: `load_ledger(..., LedgerKind.FORMAL)` as the authority for human-ledger integrity.

- [ ] **Step 1: Write failing task-registry and committed-result tests**

  Add tests that require ten unique tasks, resolve every fixture/bug/patch/witness reference,
  reject a stale fixture version, and validate all four committed `results.json` files with the
  public schema. Replace the obsolete empty-ledger assertion with a test that loads the committed
  formal ledger, proves every adjudicator is non-synthetic, and proves both entries hash-validate.

- [ ] **Step 2: Verify RED**

  Run:
  `uv run pytest -q tests/test_tasks.py tests/schemas/test_schemas.py tests/evaluator/test_ledger.py`

  Expected: missing task schema/registry/validator and committed result schema failures.

- [ ] **Step 3: Implement the minimal declarative contract**

  Define a closed task object with `task_id`, fixture identity, `snapshot`, optional `bug_id`,
  `patch`, `witness`, `tree_checksum`, and `split`. Make `tasks.py` validate JSON Schema first,
  then resolve references through `load_fixture`. Add `schema_version` and `tool_backend` to the
  result schema while retaining `additionalProperties: false`. Include `tasks` in the sdist.

- [ ] **Step 4: Verify GREEN and mutation cases**

  Re-run the focused tests. Manually mutate a copied registry in tests to a stale fixture version,
  duplicate task id, missing patch, and wrong checksum; each must fail for its named reason.

---

### Task 2: Complete private-to-public live evidence

**Files:**
- Modify: `src/coding_agent_eval/agent/protocol.py`
- Modify: `src/coding_agent_eval/agent/provider.py`
- Modify: `src/coding_agent_eval/agent/responses_provider.py`
- Modify: `src/coding_agent_eval/agent/loop.py`
- Modify: `src/coding_agent_eval/trace/raw_store.py`
- Modify: `src/coding_agent_eval/live.py`
- Modify: `src/coding_agent_eval/cli.py`
- Modify: `tests/agent/test_provider.py`
- Modify: `tests/agent/test_responses_provider.py`
- Modify: `tests/agent/test_loop.py`
- Modify: `tests/trace/test_raw_store.py`
- Modify: `tests/test_live_run.py`

**Interfaces:**
- `Step.trace: dict[str, Any]` carries request hash, latency, finish reason, and private bodies.
- `Recorder(..., sink: Callable[[dict[str, Any]], None] | None)` writes each completed event to an
  append-only sink without changing its public event shape.
- `execute(..., raw_store_root: Path = Path('.run-store')) -> LiveRun` persists raw evidence.
- `write_evidence` sanitizes the persisted raw event list and never calls `project_events`.

- [ ] **Step 1: Write failing provider and recorder tests**

  Require both provider adapters to return a canonical SHA-256 request hash, integer latency,
  finish reason, full private request body, and full private response body. Require Recorder to
  preserve its timestamp and sequence when appending to RawStore.

- [ ] **Step 2: Verify provider/recorder RED**

  Run the exact new tests and confirm failures are missing `Step.trace`, missing sink support, and
  absent metadata—not fixture or network errors.

- [ ] **Step 3: Implement provider call metadata and raw sink**

  Hash canonical request JSON before POST, measure elapsed monotonic time around POST, store the
  parsed response privately, and classify error calls with the same metadata. Extend Recorder with
  an event-record sink and RawStore with `append_record` so sequence and timestamp are preserved.

- [ ] **Step 4: Write failing live evidence tests**

  A mock-transport live run must leave a private `.run-store/<run-id>/events.jsonl`, emit exactly
  one `run_header`, one aggregate `cost`, and one `termination`, and write a public trace through
  `sanitize_run`. Inject an unclassified field and assert `trace.jsonl` is not created or replaced.

- [ ] **Step 5: Verify live RED**

  Run the focused live tests and confirm they fail because execute currently emits neither header
  nor cost and write_evidence bypasses the sanitizer.

- [ ] **Step 6: Implement the evidence sequence**

  Emit a header before the first provider call, write cost immediately before termination in the
  final evidence ordering, and sanitize only the RawStore event list. Hash prompt/params/bug set
  with canonical JSON, record fixture/environment/image/backend provenance without exposing the
  API key, and keep raw provider/tool content private.

- [ ] **Step 7: Verify GREEN**

  Run provider, loop, raw-store, sanitizer, and live tests together. Inspect the public trace and
  assert no request/response bodies, tool content, key material, or host paths survive.

---

### Task 3: Multi-call replay and aggregate correctness

**Files:**
- Modify: `src/coding_agent_eval/evaluator/replay.py`
- Modify: `tests/evaluator/test_replay_determinism.py`
- Modify: `tests/evaluator/golden/raw_trace.jsonl`
- Modify: `tests/evaluator/golden/public_trace.jsonl`
- Modify: `tests/evaluator/golden/expected_results.json`

**Interfaces:**
- Produces: `_single_payload(records, event, required=True)` for singleton events.
- Produces: `_sum_llm_usage(records) -> dict[str, int]` across every `llm_call`.

- [ ] **Step 1: Write failing two-call replay tests**

  Use hand-derived token totals from two LLM calls. Add missing-header, duplicate-header,
  duplicate-cost, and aggregate-cost-mismatch cases.

- [ ] **Step 2: Verify RED**

  Run `uv run pytest -q tests/evaluator/test_replay_determinism.py`; the two-call total must expose
  the existing first-event selection bug.

- [ ] **Step 3: Implement strict singleton and sum behavior**

  Require one header, termination, and cost event; sum input/output usage across all calls; compare
  the summed estimated call cost with the aggregate cost using decimal-safe normalization; reject
  malformed event ordering or provenance instead of defaulting.

- [ ] **Step 4: Regenerate deterministic golden bytes through the checked-in builder**

  Run the existing golden builder, inspect its diff, then run the replay tests twice to prove
  byte-identical output.

---

### Task 4: Honest CI gates and repository release audit

**Files:**
- Create: `src/coding_agent_eval/release_audit.py`
- Create: `tests/test_release_audit.py`
- Modify: `src/coding_agent_eval/cli.py`
- Modify: `tests/test_cli_smoke.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `scripts/check.sh`
- Modify: `scripts/verify_release.sh`
- Modify: `fixtures/fx-taskq-py/witness/clean_suite.yaml`
- Modify: `tests/fixtures/test_witness_contract.py`
- Modify: `tests/test_e2e_baseline.py`

**Interfaces:**
- Produces: `audit_repository(root: Path, *, check_git_history: bool) -> list[AuditFinding]`.
- Produces CLI: `cae release audit [--check-git-history]`.

- [ ] **Step 1: Write failing audit and all-fixture witness tests**

  Require repository audit to validate tasks, formal ledger, every committed result, every public
  trace, documentation links, and release metadata. Require fixture verification to enumerate all
  eight bug cycles and both clean suites. Require current fixture versions in Docker image maps.

- [ ] **Step 2: Verify RED**

  Run the focused tests; failures must identify missing audit behavior, stale `191 passed`, and the
  taskq `1.0.2` image tag.

- [ ] **Step 3: Implement the repository audit and repair gate commands**

  Reuse schema/ledger/task loaders rather than grep. Make default audit pass implementable artifact
  checks. With `--check-git-history`, enumerate every non-kuotunyu author/committer and every
  co-author trailer, returning commit ids and a blocking status without changing Git.

- [ ] **Step 4: Wire CI and scripts to what their labels claim**

  Quality CI runs `cae release audit`; Docker CI runs `cae fixture verify` for each fixture, then
  sandbox and E2E tests. `check.sh` stops claiming every gate unless it executes the Docker gates.
  Release verification adds doc-link, artifact-size, metadata, and Git provenance checks.

- [ ] **Step 5: Verify GREEN except immutable provenance**

  Default audit must pass. History audit must fail with exactly twenty existing co-author commits
  and no non-kuotunyu author/committer. This expected blocker is reported, not suppressed.

---

### Task 5: Claims, citation metadata, and checksum manifest

**Files:**
- Create: `CITATION.cff`
- Create: `.zenodo.json`
- Create: `release-manifest.json`
- Modify: `README.md`
- Modify: `docs/BENCHMARK_CARD.md`
- Modify: `docs/DATA_CARD.md`
- Modify: `docs/HANDOFF.md`
- Modify: `docs/MANUAL_RUN.md`
- Modify: `docs/THREAT_MODEL.md`
- Modify: `docs/METRICS.md`
- Modify: `runs/README.md`
- Modify: `ledger/README.md`
- Modify: `fixtures/fx-taskq-py/defects.md`
- Modify: `.env.example`

**Interfaces:**
- `release-manifest.json` maps stable public artifacts to SHA-256 hashes.
- Citation files name `kuotunyu` as the only creator and carry no DOI or publication claim.

- [ ] **Step 1: Reconcile facts from artifacts**

  State eight attempts, six billable runs, four completed runs, `$0.399115` total estimated cost,
  two formal human rulings, zero independently verified/publishable model results, 205/174 fixture
  tests, and `host_process` for all committed live attempts.

- [ ] **Step 2: Add preservation metadata**

  Add parseable CFF and Zenodo JSON for a methodology preview. Build a deterministic checksum
  manifest for schemas, tasks, fixture manifests/bugs/patches/witnesses, ledger, committed public
  traces/results, and documentation cards. Do not include `.run-store`, `.env`, or caches.

- [ ] **Step 3: Validate claims and metadata**

  Run release audit, tracked-file leak scan, metadata parsers, and doc-link audit. Search for stale
  empty-ledger, 191-test, no-live-run, and incorrect live-07 cost claims and resolve every hit by
  context rather than blind replacement.

---

### Task 6: Full verification and handoff

**Files:**
- Verify only; no new implementation file is created by this task.

**Interfaces:**
- Consumes every Gate A deliverable and produces a factual pass/fail report.

- [ ] **Step 1: Run static and non-Docker gates**

  Run `ruff check`, `ruff format --check`, strict mypy, the complete non-Docker pytest suite,
  fixture validation, repository audit, and tracked-file leak scan with bytecode/cache writes
  disabled where supported.

- [ ] **Step 2: Run Docker gates when Docker is reachable**

  Verify both fixture images and all bug cycles, sandbox observed behavior, and isolated baseline
  parity. If Docker is unavailable, report that fact and do not infer a pass.

- [ ] **Step 3: Run build and clean-export verification**

  Build wheel/sdist and run `scripts/verify_release.sh` from the current tree only if it can preserve
  user changes; otherwise reproduce its non-destructive checks manually and report the limitation.

- [ ] **Step 4: Re-audit Git state**

  Confirm the original `.gitignore` and ledger additions remain present, enumerate all files added
  by Gate A, and confirm nothing is staged, committed, pushed, tagged, or externally published.

- [ ] **Step 5: Report the one owner-only decision if still blocking**

  Present the exact contributor-history evidence and ask only for the release-lineage decision
  needed to satisfy GitHub Contributors. Do not ask about implementation details already resolved
  by this plan.
