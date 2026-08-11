# Portfolio Release Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the audit-report-shaped landing page with a concise recruiting surface and remove superseded live diagnostics without weakening release validation.

**Architecture:** Public evidence remains append-only where it has methodological value, while the eight pre-contract provider diagnostics leave the current release tree and remain recoverable from Git history. README becomes the shallow navigation layer; detailed scientific contracts remain in the existing cards and readiness documents.

**Tech Stack:** Markdown, Mermaid, pytest, Python release-audit CLI, Git release manifest.

## Global Constraints

- Operate only on the existing `main` worktree.
- Preserve all reference-suite and paid-smoke terminal outcomes and their claim boundaries.
- Do not weaken audit assertions, create verified metrics, or adjudicate the attempt-3 clean finding.
- Do not make a paid request, create a tag/Release/Zenodo object, rewrite history, or add a co-author.
- All commits use `kuotunyu <61350295+kuotunyu@users.noreply.github.com>`.

---

### Task 1: Remove superseded live diagnostics

**Files:**
- Modify: `tests/test_release_audit.py`
- Delete: `runs/live-01/` through `runs/live-08/`
- Modify: `docs/RELEASE_READINESS.md`

**Interfaces:**
- Consumes: `audit_repository(root: Path) -> list[AuditFinding]`
- Produces: a current evidence tree with zero `trace.legacy` findings.

- [ ] **Step 1: Change the release-contract test first**

Replace the legacy warning count assertion with:

```python
assert [finding.render() for finding in findings] == []
```

- [ ] **Step 2: Verify the test fails while legacy runs remain**

Run: `uv run pytest tests/test_release_audit.py::test_all_implementable_repository_release_contracts_are_clean -q`

Expected: FAIL because eight `trace.legacy` findings remain.

- [ ] **Step 3: Delete only the 24 tracked files under `runs/live-01` through `runs/live-08`**

Use `apply_patch`; do not delete any baseline, reference, or smoke artifact.

- [ ] **Step 4: Update release readiness**

Change R5 to state that current public traces use schema 0.2.0 and that superseded schema-0.1 provider diagnostics were removed from the release tree while remaining in Git history.

- [ ] **Step 5: Verify the focused contract**

Run: `uv run pytest tests/test_release_audit.py::test_all_implementable_repository_release_contracts_are_clean -q`

Expected: PASS with no audit findings.

- [ ] **Step 6: Commit**

```text
git add tests/test_release_audit.py docs/RELEASE_READINESS.md runs/live-01 runs/live-02 runs/live-03 runs/live-04 runs/live-05 runs/live-06 runs/live-07 runs/live-08
git commit -m "chore: remove superseded live diagnostics"
```

### Task 2: Rewrite the recruiting landing page

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: committed evidence in `runs/reference/`, `runs/smoke/`, and the detailed documentation cards.
- Produces: an English-first one-minute overview with a short Traditional Chinese navigation section.

- [ ] **Step 1: Replace README with the approved two-level structure**

Include exact badges, a compact Mermaid flow, the offline commands `uv sync --locked`, `uv run cae validate fixtures`, and `uv run cae release audit --publication`, plus sample output copied from those deterministic commands. Add `What I built`, engineering challenges, evidence boundaries, limitations, and links to the four detailed cards.

- [ ] **Step 2: Check claims and readability mechanically**

Run:

```text
rg -n "leaderboard|state-of-the-art|production-grade|certified sandbox|verified_finding_precision:|verified_bug_recall:" README.md
```

Expected: no unsupported promotional claim or invented verified metric. Then inspect the rendered Markdown structure and confirm the README remains below 800 English-equivalent words excluding code and tables.

- [ ] **Step 3: Validate every local README link**

Run a small read-only Python check that extracts relative Markdown links and asserts each target exists. Confirm the CI badge targets `actions/workflows/ci.yml`, the Python badge says 3.12+, the license badge says MIT, and release status says candidate rather than published.

- [ ] **Step 4: Commit**

```text
git add README.md
git commit -m "docs: focus README on evaluation engineering"
```

### Task 3: Regenerate evidence inventory and verify release contract

**Files:**
- Modify: `release-manifest.json`

**Interfaces:**
- Consumes: final tracked release artifacts.
- Produces: deterministic bytes/SHA-256 inventory with no `runs/live-*` entries.

- [ ] **Step 1: Regenerate the manifest**

Run: `uv run python scripts/build_release_manifest.py`

- [ ] **Step 2: Check inventory exclusions**

Run: `rg -n 'runs/live-0' release-manifest.json`

Expected: no matches.

- [ ] **Step 3: Run the full local CI contract**

Run Ruff check/format, configured `uv run mypy`, full non-Docker pytest, fixture validation, tracked leak scan, offline and online publication audits, wheel/sdist build, fixture Docker tests, sandbox Docker tests, and deterministic baseline Docker tests. Every command must exit zero and the publication audit must have zero warnings.

- [ ] **Step 4: Verify provenance and repository identity**

Confirm one worktree, branch `main`, owner-only author/committer history, no `Co-authored-by`, and a clean worktree.

- [ ] **Step 5: Commit and push main**

```text
git add release-manifest.json
git commit -m "chore: refresh release artifact inventory"
git push origin main
```

- [ ] **Step 6: Confirm GitHub CI**

Wait for Linux quality, Windows quality, and Docker gates to reach successful terminal states. Stop before any tag, GitHub Release, or Zenodo operation.
