# TaskQ Clean-Control Corrections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct the two TaskQ 1.0.5 clean-control defects from paid smoke attempt 4, advance the fixture/OCI contract to 1.0.6, and close every offline release gate without altering the retained outcome.

**Architecture:** Serialize keyed enqueue through the existing immediate SQLite transaction, and make schema-2 migration both transactional and physically idempotent by checking the actual column before `ALTER`. Version every corrected fixture/environment identity while preserving historical evidence bytes.

**Tech Stack:** Python 3.12, SQLite/WAL, pytest, Docker/OCI, GHCR, YAML/JSON fixture contracts.

## Global Constraints

- Work only in the existing `main` worktree with owner-only Git provenance.
- Preserve all attempt-4 public and private evidence; do not recompute its cost or relabel candidates.
- Use TDD: production behavior changes only after focused failing tests.
- Publish only a new versioned TaskQ 1.0.6 OCI tag; never repoint 1.0.5.
- Do not make another paid provider request, run a mutated smoke, create a tag, GitHub Release, or Zenodo object.

---

### Task 1: Add failing clean-control regressions

**Files:**
- Modify: `fixtures/fx-taskq-py/tree/tests/test_idempotency.py`
- Modify: `fixtures/fx-taskq-py/tree/tests/test_storage.py`

**Interfaces:**
- Specifies: one task per live `(queue, idempotency_key)` under concurrent enqueue and recovery of an interrupted schema-2 migration.

- [ ] Add a two-thread real-SQLite test with controlled legacy lookup interleaving; assert both calls return one ID and only one pending task exists.
- [ ] Add an interrupted-migration test with `lease_generation` already present and version absent/old; assert `Storage` opens, reports version 2, and retains exactly one column.
- [ ] Run only these tests and observe deterministic failure for duplicate tasks and duplicate-column migration.

### Task 2: Make keyed enqueue and migration atomic

**Files:**
- Modify: `fixtures/fx-taskq-py/tree/src/taskq/queue.py`
- Modify: `fixtures/fx-taskq-py/tree/src/taskq/storage.py`

**Interfaces:**
- Produces: transactional keyed enqueue using `Storage.write_transaction()` and a transactionally resumable schema-2 migration.

- [ ] Move keyed lookup, prior-task lookup, insert, and key remember into one immediate transaction; retain the unkeyed fast path and existing validation/expiry behavior.
- [ ] Run the concurrency regression and observe green; run all idempotency/queue/API/server tests.
- [ ] In `_migrate`, inspect `PRAGMA table_info(tasks)` and perform column addition plus version write in one immediate transaction without suppressing unrelated SQLite errors.
- [ ] Run the migration regression and observe green; run all storage tests.
- [ ] Run the complete TaskQ test suite and commit with `fix(fixture): make enqueue and migration atomic`.

### Task 3: Advance the current fixture contract to 1.0.6

**Files:**
- Modify: `fixtures/fx-taskq-py/defects.md`
- Modify: `fixtures/fx-taskq-py/fixture.yaml`
- Modify: `fixtures/fx-taskq-py/known_residual_defects.yaml`
- Modify as required: `fixtures/fx-taskq-py/bugs/*.yaml`, `patches/*.patch`, and `witness/**`
- Modify: `tasks/v0.1.json`
- Modify: current-version references in README/docs/tests; preserve all historical run/registration bytes.

**Interfaces:**
- Produces: TaskQ 1.0.6 with exact committed checksum, LOC, 222 tests, and four valid seeded mutations.

- [ ] Document both machine-confirmed defects and the no-human-ruling boundary; leave the corrected 1.0.6 residual-defect list empty only after tests pass.
- [ ] Update current version/test-count references, then run fixture rebuild against the committed tree to derive exact checksum and LOC.
- [ ] Update the manifest and task registry with derived values; regenerate deterministic scripted baselines.
- [ ] Run fixture validation plus all clean/mutated/reverted witness cycles; adjust only mechanical patch context if the source edits changed hunk locations.
- [ ] Assert attempt 4 remains TaskQ 1.0.5 with unchanged hashes/usage/cost/findings.
- [ ] Commit with `chore(fixture): advance TaskQ fixture to 1.0.6`.

### Task 4: Build and pin the immutable 1.0.6 environment

**Files:**
- Modify: `fixtures/fx-taskq-py/fixture.yaml`
- Modify: current OCI references in README/docs/tests and `.github/workflows/ci.yml`

**Interfaces:**
- Produces: public `ghcr.io/kuotunyu/coding-agent-eval-fx-taskq-py:1.0.6` with pinned manifest/config digests and environment fingerprint.

- [ ] Build Linux/amd64 from the pinned base digest and run the TaskQ clean suite inside the local image.
- [ ] Push only tag `1.0.6`; inspect public manifest and config digests without changing 1.0.5.
- [ ] Record immutable digests, recompute/verify environment fingerprint, and update every current identity assertion while preserving historical references.
- [ ] Run offline and online publication audits and Docker fixture/sandbox/baseline gates.
- [ ] Commit with `chore(fixture): pin TaskQ 1.0.6 OCI identity`.

### Task 5: Close release verification and stop before paid execution

**Files:**
- Modify: `docs/PAID_SMOKE_PLAN.md`
- Modify: `docs/BENCHMARK_CARD.md`
- Modify: `docs/DATA_CARD.md`
- Modify: `docs/RELEASE_READINESS.md`
- Modify: `release-manifest.json`

**Interfaces:**
- Produces: a clean, pushed, CI-green 1.0.6 release candidate and a proposed next clean-smoke contract requiring separate approval.

- [ ] Regenerate the release manifest twice and prove determinism.
- [ ] Run Ruff, format, strict mypy, full pytest, build, fixture validation/verification, tracked leak scan, offline/online publication audits, and all Docker gates.
- [ ] Verify one main worktree, owner-only author/committer history, no co-author trailers, clean tree, and HEAD/origin alignment.
- [ ] Push `main`, wait for Ubuntu/Windows/Docker GitHub CI, and verify GitHub Contributors still lists only `kuotunyu`.
- [ ] Document a low-cost 1.0.6 clean smoke proposal, then stop for explicit paid approval. Do not read `.env` or execute the provider in this plan.
