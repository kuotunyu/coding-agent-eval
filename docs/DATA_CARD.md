# Data card — BugSeed

The dataset this benchmark measures against: what is in it, who made it, when, under what
licence, and what it cannot support.

- **Dataset name**: BugSeed
- **Benchmark version**: `0.1.0`
- **Authoring cutoff**: `2026-08-05` — every fixture and every bug carries this
  `authored_at`. Nothing in this dataset existed before that date.
- **Licence**: MIT, for the dataset and for both fixtures individually.

---

## Provenance

Both fixtures are **first-party**: written for this benchmark by its author, not adapted
from an existing project. No upstream code, no scraped repositories, no third-party
snapshots. Each fixture carries its own `LICENSE`.

Every bug is **injected**: authored as a patch against a clean tree. There is no historical
cohort in v0.1 — no bug here was taken from a real project's issue tracker or fix commit.

The reference agent runtime evolved from an internal prototype. The fixtures, schemas,
evaluator, and trace pipeline were written for this benchmark.

## Authorship and adjudication — read this before using any `verified_*` number

> **The fixture author and the adjudicator are the same person.**

This is the dataset's most significant limitation and it is stated here rather than in a
footnote. Blinding removes the provider, model, budget, cost, and run identifiers from what
an adjudicator sees, which eliminates brand bias. It cannot remove the fact that someone who
wrote a bug knows its intended answer, and therefore cannot be fully neutral about whether a
given finding really describes that root cause.

v0.1 accepts the limitation and requires three things in exchange:

1. The relationship between `adjudicator_id` and the fixture author is disclosed — this
   section is that disclosure.
2. Every ruling records a `rationale`, so a third party can review any specific decision.
3. The ledger is public and append-only, so any ruling can be disputed after the fact.

**A second, independent adjudicator and a documented disagreement-resolution protocol are a
precondition for publishing any model comparison.** Not an improvement, a precondition. No
comparative claim may be made from this dataset until that exists.

**No AI may author an adjudication.** Synthetic rulings carry a `SYNTHETIC-` prefix and force
`publishable: false`, and the evaluator rejects a formal ledger containing any such entry.
The formal ledger `ledger/adjudications.jsonl` currently contains two human rulings for two
findings against `fx-taskq-py/B-001`. They are incomplete and not independent, and therefore
support no publishable `verified_*` result.

---

## The fixtures

| | `fx-taskq-py` | `fx-ledger-ts` |
|---|---|---|
| Version | 1.0.3 | 1.0.2 |
| Language | Python 3.12 | TypeScript / Node 22 |
| Shape | Background task queue: HTTP API, worker loop, SQLite persistence | Double-entry ledger: HTTP API, per-account locks, append-only journal, batch settlement |
| In-scope LOC | 1,367 | 1,183 |
| In-scope paths | `src/**` | `src/**` |
| Out-of-scope paths | `tests/**` | `tests/**`, `node_modules/**`, `dist/**` |
| Own test suite | 205 tests | 174 tests |
| Third-party runtime dependencies | none | none |
| Licence | MIT | MIT |
| `authored_at` | 2026-08-05 | 2026-08-05 |

**Line counting.** `in_scope_loc` is produced by `cae-loc 0.1.0`, a counter that lives in
this repository rather than an external tool. The rule is deliberately plain: a line counts
when it has content that is not a whole-line comment. Precision beyond that would invite
arguments the metric does not need — what matters is that the number is reproducible and
that the same counter is available wherever the gate runs, since it is the denominator of
`benchmark_unsupported_findings_per_kloc`.

Counts are taken over the **committed** tree, exported from Git, not over a working
directory. A built `fx-ledger-ts` working copy carries `node_modules/` and `dist/`, neither
committed, and counting them would describe a tree no run will ever see.

### Clean controls

Each fixture ships a clean control: the same tree with no seeded defect. It is the source of
the headline noise metric, so its cleanliness is audited rather than assumed — see
`fixtures/<id>/defects.md`.

Those audits are not a formality. Across the two trees they found **four real defects**,
each fixed and the fixture version bumped before any bug was seeded:

- `fx-taskq-py` 1.0.1: the service returned 500 on every route that touched storage when run
  as its README documents, and a 204 announced a body it never sent.
- `fx-taskq-py` 1.0.2: a queue name ending in a newline was accepted, creating a queue no
  worker would ever read.
- `fx-ledger-ts` 1.0.1: the server exited the moment it began listening, so it never served
  anything, and the documented run command named a path the build does not produce.
- `fx-taskq-py` 1.0.3: `delay_seconds` was coerced with a bare `float()`, so malformed input
  escaped the API's error handling and a NaN reached a NOT NULL column. **Found by an agent
  on a clean-control run, not by the author** — the two audit passes over that file had both
  missed it.

Both trees' `known_residual_defects.yaml` is empty, which v0.1 requires. A non-empty list
makes a fixture release-ineligible: excluding a finding because it sits near a
known-but-unfixed defect would hand a free pass to any wrong finding that landed nearby.

---

## The bugs

Eight, four per fixture, all `provenance: injected`, all `authored_at: 2026-08-05`.

| Bug | Category | Severity | Subcategory |
|---|---|---|---|
| `fx-taskq-py/B-001` | security | critical | authentication_bypass |
| `fx-taskq-py/B-002` | data_boundary | high | cross_tenant_identifier_exposure |
| `fx-taskq-py/B-003` | correctness | medium | pagination |
| `fx-taskq-py/B-004` | release_claim | low | documented_boundary_off_by_one |
| `fx-ledger-ts/B-001` | concurrency | high | lost_atomicity |
| `fx-ledger-ts/B-002` | correctness | medium | boundary_off_by_one |
| `fx-ledger-ts/B-003` | data_boundary | high | internal_detail_exposure |
| `fx-ledger-ts/B-004` | security | medium | timing_side_channel |

**Category distribution**: correctness 2, security 2, data_boundary 2, concurrency 1,
release_claim 1 — **5 of 5 categories** covered, though thinly.

**Severity distribution**: critical 1, high 3, medium 3, low 1.

Eight bugs across five categories means **at most two samples per category**. No per-category
conclusion can be drawn from this dataset, and neither can an overall ranking.

### How bugs were selected

By mutation screening, not by judgement about what looked fragile. Candidate defects were
written, applied one at a time, and each fixture's own suite re-run against every one. Only
survivors were used, and of those the selection favoured spread across modules and
categories.

- `fx-taskq-py`: 23 candidates written, 13 caught by the suite, 10 survived.
- `fx-ledger-ts`: 18 candidates written, 12 caught by the suite, 6 survived.

**Every seeded bug survives its fixture's own test suite.** This is a requirement. A defect
the fixture's tests catch would measure whether an agent runs the test suite rather than
whether it can read code, and would inflate recall for a behaviour the benchmark is not
trying to measure.

Each bug carries a machine-executable **witness contract**: a command whose result differs
between the clean and mutated trees. Gate G2 runs five steps per bug — clean passes, patch
applies, mutated behaves as declared, patch reverts, clean passes again. The last step is
the one usually omitted, and without it a patch that also changed something else would still
look correct.

---

## Contamination

The bugs are **novel, privately authored mutations at benchmark creation time**. That makes
the dataset **contamination-resistant** and gives it **lower contamination risk than public
historical fixes**, which may already sit in training data alongside their fix commits,
issue threads and changelogs.

- **Benchmark version**: `0.1.0`
- **Cutoff date**: `2026-08-05`
- **Contamination resistance decays.** Once published, these bugs may enter future training
  data. The property weakens with time and with exposure, and a result quoted long after
  this date should be read accordingly.

---

## What is kept, and for how long

| Artefact | Where | Retention |
|---|---|---|
| Fixture trees, bugs, patches, witnesses | Version control | Permanent; part of the dataset |
| Adjudication ledger | Version control, append-only | Permanent; rulings are never edited or deleted |
| Public trace | Published with a run | Permanent |
| Raw evidence store | `.run-store/`, local only | **30 days by default**, pruned by `cae store prune` |

For runs produced by the current runner, the raw store holds full tool output and full model
exchanges. It is never published: the public trace is a projection through a field allowlist,
where every field is classified public or known-private and an **unclassified field raises**
rather than defaulting either way. The sanitizer that produces public artifacts is
fail-closed and atomic. The eight committed historical live traces predate this raw-event
contract and are retained as legacy evidence with release-audit warnings.

---

## Intended use, and uses this dataset does not support

**Intended**: validating that the measurement methodology works end to end, and measuring
one agent's defect-discovery behaviour under stated conditions.

**Not supported:**

- **Ranking or comparing models.** Two fixtures and eight bugs cannot separate one agent
  from another. This holds for anyone who runs it, not just for us.
- **Per-category conclusions.** At most two bugs per category.
- **Claims about real-world false-positive rates.** The clean-control metric is named
  `benchmark_unsupported_findings_per_kloc` for that reason, and must not be described as a
  real-world false-positive rate on a repository whose ground truth is unknown.
- **Generalising to production systems.** These are services written to be measured — real
  in shape, bounded in size, and with no third-party runtime dependencies. Their realism has
  a ceiling.
- **Anything requiring a publishable `verified_*` result today.** The formal ledger has two
  rulings, but not complete run coverage or an independent second adjudicator.
