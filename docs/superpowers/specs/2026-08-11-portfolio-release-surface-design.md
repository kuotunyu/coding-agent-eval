# Portfolio release surface design

## Objective

Make the repository understandable to an international AI developer-tools or LLM
evaluation hiring reader in roughly one minute, without weakening its evidence rules or
turning failed experiments into performance claims.

## Public evidence policy

The current release tree will remove `runs/live-01` through `runs/live-08`. These are
early host-process diagnostics that terminated at the provider boundary with zero tool
calls and zero findings. They are superseded by deterministic adapter tests and retained
schema-0.2 smoke outcomes. Their Git history remains available; moving them to an archive
inside the release would retain eight audit warnings and add no material recruiting
evidence.

The audit will continue to reject malformed current evidence. No assertion is weakened
to make the legacy warnings disappear.

## README information architecture

The README will be English-first and compact enough for a one-minute first pass:

1. one-sentence problem/value statement;
2. CI, Python, license, and release-status badges;
3. a small Mermaid diagram showing fixture registration, isolated agent execution,
   sanitized traces, deterministic matching, and human adjudication;
4. a 30-second offline quickstart and reproducible sample CLI output;
5. `What I built` and the main engineering challenges;
6. a concise evidence table that separates scripted baselines, the legacy reference
   suite, the three corrected-adapter smoke attempts, and pending new evidence;
7. explicit limitations and links to the Benchmark Card, Data Card, Reference Suite,
   and Release Readiness documents;
8. a short Traditional Chinese navigation summary rather than a duplicate audit report.

The README will not use leaderboard, state-of-the-art, production-grade, certified
sandbox, or equivalent claims. A normal provider completion is not task success, a
candidate finding is not a verified detection, and the attempt-3 clean finding remains
pending independent human review.

## Evidence boundaries

- Scripted baseline artifacts demonstrate deterministic pipeline behavior only.
- The 2026-08-10 reference suite remains adapter-0.1 evidence: all ten terminal outcomes
  are retained, all exhausted their token budget, and none produced findings.
- Paid smoke attempts 1 and 2 remain adapter-0.2 failures and cannot be relabeled.
- Paid smoke attempt 3 is adapter-0.3/prompt-0.2 evidence. It completed but submitted one
  unverified finding on a clean control, so the smoke gate failed and the mutated task was
  not run.
- No `verified_*` headline metric exists without complete independent human review.

## Required updates

Remove the eight legacy run directories, update any documentation and tests that refer to
their warnings, regenerate `release-manifest.json`, and ensure the publication audit is
clean with no legacy-run exception. The private review worksheet remains ignored under
`.run-store/` and does not enter the release.

## Verification and release state

Run the repository's complete local CI contract: pytest, Ruff, format, configured strict
mypy, wheel/sdist build, fixture validation, tracked leak scan, offline and online
publication audits, and Docker gates. Push only after a clean local result and confirm all
GitHub CI jobs. Preserve owner-only authorship and a single main worktree.

This work does not authorize another paid request, a tag, a GitHub Release, or any Zenodo
operation. The repository remains a release candidate until the clean-control report is
independently resolved and the resulting methodology path is completed.
