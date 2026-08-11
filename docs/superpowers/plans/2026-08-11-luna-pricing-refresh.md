# GPT-5.6 Luna Pricing Refresh and Smoke Attempt 4 Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refresh the current GPT-5.6 Luna price table from official evidence, prove the estimate against attempt 3 usage, and execute the already approved TaskQ 1.0.5 clean smoke exactly once after every offline and CI gate passes.

**Architecture:** Version the mutable current price observation while treating every recorded historical run as immutable evidence. Gate the single paid call behind deterministic tests, publication checks, a clean pushed `main`, and green CI; preserve the terminal outcome without retries or relabeling.

**Tech Stack:** Python 3.12, pytest, Ruff, mypy, OpenAI Responses API, GitHub Actions, Docker/OCI.

## Global Constraints

- Work only in the existing `main` worktree and preserve owner-only Git provenance.
- Use GPT-5.6 Luna through the Responses API with manual history and `store: false`.
- Do not change historical trace/result pricing or cost bytes.
- Attempt 4 may make one clean-task provider execution with maximum new provider-side exposure USD 0.05 and no retry.
- Do not execute the mutated task without a separate paid approval.
- Do not create a Git tag, GitHub Release, Zenodo draft, or Zenodo record.

---

### Task 1: Prove and update the current Luna pricing table

**Files:**
- Modify: `tests/test_live_run.py`
- Modify: `src/coding_agent_eval/agent/provider.py`

**Interfaces:**
- Produces: current pricing version `openai-gpt-5.6-luna@2026-08-11-r2` with input USD 0.20/M, cached input USD 0.02/M, and output USD 1.20/M.

- [ ] Add a regression test using attempt 3's literal usage: 88,934 input, 70,786 cached input, and 1,052 output tokens. Independently expect USD 0.00630772 and the `-r2` price version.
- [ ] Run the focused test and observe failure against the stale current table.
- [ ] Update only the current default pricing constant, retaining the exact official source URL and 2026-08-11 observation date.
- [ ] Run the focused test and all provider/live budget tests; expect green.
- [ ] Commit with `fix(provider): refresh Luna pricing evidence`.

### Task 2: Close documentation and release verification

**Files:**
- Modify: `docs/MANUAL_RUN.md`
- Modify: `docs/PAID_SMOKE_PLAN.md`
- Modify: `release-manifest.json`

**Interfaces:**
- Produces: current pricing guidance and an attempt 4 plan that accurately distinguishes an estimated harness stop from a provider billing cap.

- [ ] Update only current/future pricing guidance; leave attempts 1-3 and all historical artifacts unchanged.
- [ ] Record the attempt 3 comparison estimate and explain why USD 0.05 remains sufficient without calling it a hard provider-side cutoff.
- [ ] Regenerate `release-manifest.json` twice and confirm determinism.
- [ ] Run Ruff, format check, strict mypy, full pytest, build, fixture validation/verification, leak scan, offline and online publication audits, and Docker gates.
- [ ] Verify one `main` worktree, owner-only author/committer history, no co-author trailers, and a clean tree.
- [ ] Push `main`, wait for all GitHub CI jobs, and confirm local HEAD equals `origin/main`.

### Task 3: Execute and validate paid smoke attempt 4

**Files:**
- Create on execution: `runs/smoke/smoke-2026-08-11-attempt-4/clean/**`
- Modify after evidence review: relevant benchmark/release documentation and `release-manifest.json`

**Interfaces:**
- Consumes: the ignored `.env` API key only after Task 2 is fully green.
- Produces: one preserved terminal clean-task outcome under TaskQ 1.0.5.

- [ ] Confirm the worktree is clean/synced and CI is green, then verify key presence without printing or copying the secret.
- [ ] Run the exact clean-task command in dry-run mode and validate model, API, reasoning, adapter/prompt versions, OCI digest, task, token/tool/time limits, USD 0.035 harness stop, and output path.
- [ ] Execute exactly once. Do not retry any provider or harness failure.
- [ ] Validate completion/finding semantics, assistant function-call continuity, `call_id`, `function_call_output`, replay, sanitization, raw/private trace boundaries, budget outcome, and absence of secret leakage.
- [ ] Preserve and document the terminal outcome, regenerate the release manifest, rerun applicable gates, commit, push, and wait for CI.
- [ ] If clean passes with the expected zero findings, stop for separate mutated-task paid approval. If it fails or is abnormal, stop with the retained evidence and do not run the mutated task.
