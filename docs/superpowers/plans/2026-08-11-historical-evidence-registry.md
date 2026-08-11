# Historical Evidence Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve exact validation of the TaskQ 1.0.4 reference suite while the current task registry and fixture advance to TaskQ 1.0.5.

**Architecture:** Keep current suite registration strict against `fixtures/` and `tasks/v0.1.json`. Add a read-only historical registration loader that binds `runs/reference/registration.json` to an adjacent exact registry snapshot and validates canonical identity without resolving against current fixture files; publication audit uses only that snapshot for historical traces.

**Tech Stack:** Python 3.12, JSON Schema, pytest, deterministic release manifest.

## Global Constraints

- Work only on the existing `main` worktree with owner-only Git identity.
- Do not change historical reference status, trace, finding, review, or registration bytes.
- Do not weaken current task-registry or fixture validation.
- Do not run a paid provider request, create a Git tag, GitHub Release, or Zenodo object.

---

### Task 1: Load a frozen registration without current-fixture coupling

**Files:**
- Modify: `tests/test_suite.py`
- Modify: `src/coding_agent_eval/suite.py`
- Create: `runs/reference/task-registry.json`

**Interfaces:**
- Produces: `load_registration_snapshot(path: Path, *, task_registry_path: Path) -> SuiteRegistration`.

- [ ] Change the legacy registration test to call `load_registration_snapshot` with `runs/reference/task-registry.json`; add assertions that its TaskQ identity/tag are 1.0.4 and that changing one registry byte raises `SuiteError` containing `hash drifted`.
- [ ] Run the focused tests and observe failure because `load_registration_snapshot` does not exist.
- [ ] Add the exact 1.0.4 registry bytes from commit `7afef9d` beside the reference registration; confirm its SHA-256 equals `sha256:cd595c76e1ec3454b207ec54de2d82c4ebee9a82f40ca403f170cbf3701cc57a`.
- [ ] Implement the loader by validating the registration and task schemas, ISO date, canonical suite ID, exact registry hash/order/count, unique task IDs, ten-times budget aggregate, one fixture version per fixture ID, and exact identity/fingerprint key coverage. Construct each `PreparedImageIdentity` with the archived fixture version as tag and the registration's recorded manifest/config digests.
- [ ] Keep `load_registration` unchanged and strict against current fixtures; add a focused assertion that it rejects the legacy registration with the current registry.
- [ ] Run `uv run pytest -q tests/test_suite.py` and expect all tests to pass.
- [ ] Commit with `fix(audit): load frozen suite registrations`.

### Task 2: Audit historical evidence against its own registry

**Files:**
- Modify: `tests/test_release_audit.py`
- Modify: `src/coding_agent_eval/release_audit.py`

**Interfaces:**
- Consumes: `load_registration_snapshot(...)` from Task 1.
- Produces: publication-suite validation driven by `runs/reference/task-registry.json`, while repository task validation remains driven by `tasks/v0.1.json`.

- [ ] Add a regression test that copies the reference registration and archived registry, changes the current fixture contract, and proves publication registration loading still succeeds; add a sibling test that mutates the archived registry and expects `suite.registration` to block.
- [ ] Run both tests and observe the valid historical case fail because `_publication_registration` still uses the current registry and fixtures.
- [ ] Make `_task_registry` accept an explicit path. Make `_publication_registration` require the adjacent `task-registry.json`, load it with `load_registration_snapshot`, and return its path. Pass that path into `_audit_publication_suite` so coverage and trace contracts resolve the frozen task records.
- [ ] Preserve fail-closed behavior for missing, malformed, hash-drifted, or wrong-order sidecars and keep `audit_repository` strict on the current registry.
- [ ] Run `uv run pytest -q tests/test_release_audit.py tests/test_suite.py` and expect all tests to pass except a stale release-manifest assertion until regeneration.
- [ ] Commit with `fix(audit): bind reference evidence to frozen tasks`.

### Task 3: Refresh current synthetic baselines and release evidence

**Files:**
- Modify: `runs/baseline-fx-taskq-py-clean/results.json`
- Modify: `runs/baseline-fx-taskq-py-mutated/results.json`
- Modify: `runs/README.md`
- Modify: `docs/DATA_CARD.md`
- Modify: current release/smoke documentation already in the worktree
- Modify: `release-manifest.json`

**Interfaces:**
- Produces: current TaskQ 1.0.5 deterministic baseline artifacts and a complete release manifest.

- [ ] Run `uv run python scripts/regenerate_runs.py`; verify only TaskQ version/LOC-derived baseline fields change, all four results remain synthetic and `publishable: false`, and a second regeneration has an empty diff.
- [ ] Update `runs/README.md` to the current TaskQ 1,456 LOC denominator and document that the reference sidecar freezes the older registry; update the Data Card to distinguish current registry from the reference registry snapshot.
- [ ] Run `uv run python scripts/build_release_manifest.py` twice and confirm the second run changes no bytes.
- [ ] Run the full offline publication audit and expect zero findings.
- [ ] Commit with `docs: close historical evidence boundaries`.

### Task 4: Complete release gates

**Files:**
- No planned source changes; failures return to the relevant TDD task.

**Interfaces:**
- Produces: a clean, pushed `main` and green GitHub CI before a new paid-smoke approval request.

- [ ] Run Ruff, Ruff format check, strict mypy, full pytest, build, fixture rebuild/validation/verification, leak scan, offline and online publication audits, and all Docker-marked fixture/sandbox/baseline gates.
- [ ] Verify one `main` worktree, owner-only author/committer history, clean tracked/untracked state, and correct remote.
- [ ] Push `main`, wait for all GitHub CI jobs, and verify local HEAD equals `origin/main`.
- [ ] Stop and request explicit approval for one new TaskQ 1.0.5 clean paid smoke; do not execute the provider in this plan.
