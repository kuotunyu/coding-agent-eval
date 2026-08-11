# TaskQ Lease Ownership Correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent expired or superseded TaskQ workers from acknowledging or failing a lease they no longer own.

**Architecture:** Persist a monotonic `lease_generation` on every task, increment it inside the existing immediate leasing transaction, and require the returned generation for every completion. Validate ownership, state, and expiry atomically in an immediate write transaction before mutating the task.

**Tech Stack:** Python 3.12, SQLite, pytest, Docker/OCI, YAML/JSON fixture contracts.

## Global Constraints

- Work only in the existing `main` worktree and preserve owner-only Git provenance.
- Treat smoke attempt 3 as failed 1.0.4 evidence; do not relabel it or create `verified_*` metrics.
- Use TDD: every behavior change begins with a focused failing test using real components.
- Advance `fx-taskq-py` to 1.0.5 and preserve every seeded bug's apply/witness/revert semantics.
- Do not make a paid request, create a Git tag, GitHub Release, or Zenodo object.

---

### Task 1: Persist a monotonic lease generation

**Files:**
- Modify: `fixtures/fx-taskq-py/tree/src/taskq/models.py`
- Modify: `fixtures/fx-taskq-py/tree/src/taskq/storage.py`
- Modify: `fixtures/fx-taskq-py/tree/tests/test_storage.py`

**Interfaces:**
- Produces: `Task.lease_generation: int`, schema version 2, and `Storage.mark_leased(..., lease_generation: int)`.

- [ ] Add a migration test that creates a schema-1 database with one task, opens `Storage`, and asserts schema version 2, preserved task data, and generation zero. Add a fresh-database test asserting the same column exists.
- [ ] Run the two tests and observe failure because schema version is 1 and `Task` has no generation.
- [ ] Add `lease_generation` to `Task` and `as_dict`; keep the base schema at version 1, migrate with `ALTER TABLE tasks ADD COLUMN lease_generation INTEGER NOT NULL DEFAULT 0`, then record version 2. Extend insert, row mapping, and `mark_leased`.
- [ ] Run `uv run pytest fixtures/fx-taskq-py/tree/tests/test_storage.py -q` and expect all storage tests to pass.
- [ ] Commit with `fix(fixture): persist lease generations`.

### Task 2: Enforce lease ownership atomically in queue policy

**Files:**
- Modify: `fixtures/fx-taskq-py/tree/src/taskq/queue.py`
- Modify: `fixtures/fx-taskq-py/tree/src/taskq/storage.py`
- Modify: `fixtures/fx-taskq-py/tree/tests/test_queue.py`
- Modify: queue callers in the remaining fixture tests and `witness/B-003/test_witness.py`

**Interfaces:**
- Produces: `acknowledge(task_id: str, lease_generation: int) -> Task` and `fail(task_id: str, lease_generation: int, error: str | None = None) -> Task`.

- [ ] Add focused tests proving generation 1 cannot ack or fail after generation 2 is leased, an expired generation is rejected before re-lease, and a dead-letter requeue does not reuse its earlier generation. Assert the state/generation remain unchanged after each conflict.
- [ ] Run those tests and observe the stale calls change the task instead of raising `Conflict`.
- [ ] Increment generation in `lease`. Add storage completion methods that run the read, state/generation/expiry validation, and update within one `write_transaction`; queue completion delegates to them and computes retry policy only after validated state is loaded.
- [ ] Update every direct queue caller to pass the generation from the exact leased `Task`; never infer it by re-reading current state after work finishes.
- [ ] Run all fixture queue, dead-letter, limit, metric, admin, and worker-adjacent tests; expect green.
- [ ] Commit with `fix(fixture): bind completion to the active lease`.

### Task 3: Carry lease ownership through HTTP, client, and worker boundaries

**Files:**
- Modify: `fixtures/fx-taskq-py/tree/src/taskq/api.py`
- Modify: `fixtures/fx-taskq-py/tree/src/taskq/client.py`
- Modify: `fixtures/fx-taskq-py/tree/src/taskq/worker.py`
- Modify: `fixtures/fx-taskq-py/tree/tests/test_api.py`
- Modify: `fixtures/fx-taskq-py/tree/tests/test_worker.py`
- Modify: `fixtures/fx-taskq-py/tree/tests/test_server.py`

**Interfaces:**
- Produces: ack body `{ "lease_generation": N }`, fail body `{ "lease_generation": N, "error": ... }`, and matching client methods.

- [ ] Add API tests for missing, boolean, zero, stale, expired, and correct generations. Add worker tests where a handler advances the clock/re-leases and verify the old worker records a conflict rather than completing the new lease. Add a client boundary test using a local server and the real client.
- [ ] Run the focused tests and observe failures because completion bodies are ignored and the client/worker omit generation.
- [ ] Validate `lease_generation` as a positive non-boolean integer in the API, require it in both client methods, and forward `task.lease_generation` from worker completion paths.
- [ ] Update README API examples and all affected server tests.
- [ ] Run `uv run pytest fixtures/fx-taskq-py/tree/tests -q`; expect the updated full count and no failures.
- [ ] Commit with `fix(fixture): carry lease ownership across clients`.

### Task 4: Advance the fixture contract to 1.0.5

**Files:**
- Modify: `fixtures/fx-taskq-py/defects.md`
- Modify: `fixtures/fx-taskq-py/fixture.yaml`
- Modify: `fixtures/fx-taskq-py/known_residual_defects.yaml`
- Modify: `fixtures/fx-taskq-py/bugs/B-001.yaml` through `B-004.yaml`
- Modify: `fixtures/fx-taskq-py/witness/clean_suite.yaml`
- Modify as required: `fixtures/fx-taskq-py/patches/*.patch`, `fixtures/fx-taskq-py/witness/**`
- Modify: `tasks/v0.1.json`
- Modify: current-version references in README/docs/tests; preserve historical run files unchanged.

**Interfaces:**
- Produces: a committed 1.0.5 tree with exact checksum, LOC, test count, and valid seeded mutations.

- [ ] Record the new defect and AI-assisted assessment boundary in `defects.md`; update fixture/manifests/registry to 1.0.5 and the observed fixture test count.
- [ ] Commit the changed tree before deriving G3 identity, then run `uv run cae fixture rebuild fixtures/fx-taskq-py` to obtain exact committed checksum/LOC failures.
- [ ] Update the manifest and task registry with the reported checksum/LOC, commit, and rerun G3 until clean.
- [ ] Run `uv run cae validate fixtures`, then `uv run cae fixture verify fixtures/fx-taskq-py`. If a patch no longer applies or a witness uses the old completion API, update only its mechanical context/call signature and confirm clean/mutated/reverted expectations remain distinct.
- [ ] Run the answer-leak tests and full non-Docker suite.
- [ ] Commit with `chore(fixture): advance TaskQ fixture to 1.0.5`.

### Task 5: Build and publish the immutable 1.0.5 environment

**Files:**
- Modify: `fixtures/fx-taskq-py/fixture.yaml`
- Modify: current OCI references in README/docs/tests and `.github/workflows/ci.yml`

**Interfaces:**
- Produces: public `ghcr.io/kuotunyu/coding-agent-eval-fx-taskq-py:1.0.5`, pinned manifest/config digests, and recomputed environment fingerprint.

- [ ] Build the Linux/amd64 image from `fixtures/fx-taskq-py/env/Dockerfile` using the manifest's pinned `BASE_DIGEST`; run the clean suite in the local image.
- [ ] Push only the new versioned `1.0.5` tag to GHCR; never repoint 1.0.4. Inspect the registry manifest and config digests.
- [ ] Record the new tag/digests and use `uv run cae fixture environment fixtures/fx-taskq-py --online --json` to derive/verify the environment fingerprint.
- [ ] Update all current OCI identity tests and workflow pulls while retaining historical run identities unchanged.
- [ ] Run offline and online publication audits plus Docker fixture/sandbox/baseline gates.
- [ ] Commit with `chore(fixture): pin TaskQ 1.0.5 OCI identity`.

### Task 6: Close offline release verification and request paid-smoke approval

**Files:**
- Modify: `docs/PAID_SMOKE_PLAN.md`
- Modify: `docs/BENCHMARK_CARD.md`
- Modify: `docs/DATA_CARD.md`
- Modify: `docs/RELEASE_READINESS.md`
- Modify: `release-manifest.json`

**Interfaces:**
- Produces: a zero-warning release candidate ready for a newly approved 1.0.5 clean smoke, not a completed benchmark result.

- [ ] Document why 1.0.4 attempt 3 remains failed and why 1.0.5 requires a new smoke identity and approval.
- [ ] Regenerate `release-manifest.json`.
- [ ] Run Ruff, format, configured strict mypy, 975+ repository tests with the new fixture count, build, fixture validation, leak scan, offline/online publication audits, and all Docker gates.
- [ ] Verify owner-only author/committer history, one main worktree, clean tree, and HEAD/origin alignment; push main and wait for all GitHub CI jobs.
- [ ] Stop and request explicit approval for a new paid clean smoke. Do not run the provider or mutated task in this plan.
