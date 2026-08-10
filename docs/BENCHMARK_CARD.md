# Benchmark card — coding-agent-eval

> A ground-truth benchmark for measuring whether coding agents can discover known defects,
> how many unsupported findings they produce, and what resources they consume under
> reproducible sandbox conditions.

- **Benchmark version**: `0.1.0`
- **Dataset**: BugSeed — see [DATA_CARD.md](DATA_CARD.md)
- **Status**: methodology vertical slice. **No independently verified, publishable model
  result exists.**

This card carries every metric definition, its denominator, and the limitations that qualify
it. The derivations, the matcher and assignment rules, and the reasoning behind the naming
decisions live in [METRICS.md](METRICS.md).

---

## What is measured

Whether an agent **finds** a defect it was not told about, in a tree whose defects are known
because they were seeded deliberately. Not whether it can repair one — that is a different
benchmark.

Scoring runs in two stages, and only the first is automatic.

**Stage A — deterministic matcher.** No human, no model. A finding is a *candidate* for a
bug when it names the same file, its line range overlaps the bug's localisation window
widened by that bug's tolerance, and the categories match.

**Stage B — blinded human adjudication.** Only candidate pairs reach it. The adjudicator
sees the fixture language, the source around the localisation window, the bug's canonical
claim and root cause, and the finding's claim, root cause and evidence. They do **not** see
the provider, model, adapter, budget, cost, tokens, latency, run id, trace, other rulings
from the same run, or the bug and finding identifiers.

A ruling of `same_root_cause` requires **three** yeses:

1. Does the claim describe the same defect?
2. Is the root cause the same mechanism?
3. **Is the evidence supported by the code shown?**

The third is a hard condition. Fabricated or irrelevant evidence forbids `same_root_cause`
even when claim and root cause are both correct: an agent that guesses the conclusion and
invents the evidence is worthless in a real review, and cannot be trusted. Evidence is part
of `finding_hash`, so changing it produces a new adjudication key rather than inheriting the
old ruling.

`insufficient` counts conservatively as **not verified**.

---

## Metric definitions

For one run over one fixture and one snapshot:

| Symbol | Meaning |
|---|---|
| `B` | the bug set for that `fixture_version` — **the recall denominator** |
| `F` | findings after exact-duplicate collapse, **including** out-of-scope ones — **the precision denominator** |
| `V` | bugs with at least one `same_root_cause` finding, after one-to-one assignment |
| `M` | findings assigned to have verified some bug |

| Metric | Definition | Denominator | Snapshot |
|---|---|---|---|
| `localization_recall` | bugs with ≥1 candidate finding, over all bugs | `\|B\|` | mutated |
| `verified_bug_recall` | `\|V\| / \|B\|` | `\|B\|` | mutated |
| `verified_finding_precision` | `\|M\| / \|F\|` | `\|F\|` | mutated |
| `unsupported_findings` | `\|F \ M\|` | count, not a ratio | mutated and clean |
| `benchmark_unsupported_findings_per_kloc` | `unsupported_findings / (in_scope_loc / 1000)` | in-scope KLOC | **clean control is the headline source** |
| `cost_per_verified_bug` | `estimated_cost_usd / \|V\|` | `\|V\|` | mutated |
| `tokens_per_verified_bug` | `(input_tokens + output_tokens) / \|V\|` | `\|V\|` | mutated |
| `out_of_scope_findings` | findings landing in `out_of_scope_paths` | count | both |
| `exact_duplicates_removed` | findings collapsed before matching | count | both |

### Zero-denominator behaviour

Never `0`, never `Infinity`, never an omitted field. Each case emits `null` with a reason:

| Condition | Result |
|---|---|
| `\|V\| = 0` | `cost_per_verified_bug` and `tokens_per_verified_bug` are `null`, `"reason": "no_verified_bugs"` |
| `\|F\| = 0` | `verified_finding_precision` is `null`, `"reason": "no_findings"` |
| `\|B\| = 0` (clean control) | `verified_bug_recall` and `localization_recall` are `null`, `"reason": "no_bugs_in_snapshot"` |

On a clean control `B` is empty, so `M` is empty and every finding is unsupported. That is
the metric's meaning, not a degenerate case.

### Two naming rules that carry meaning

**`localization_recall` is never called bug recall.** Stage A proves only that a finding
pointed at the right place with the right category. Calling it correctness recall would
claim semantic agreement that no deterministic matcher can establish.

**The clean-control metric is `benchmark_unsupported_findings_per_kloc`**, never a
"real-world false-positive rate". The name is honest about its scope: it counts findings
unsupported *by this benchmark's ground truth*, on a tree whose defects are known. The same
number on a third-party repository, whose true defect set nobody has enumerated, would not
mean that.

### Counting decisions worth knowing

- **Out-of-scope findings count toward the precision denominator.** Off-topic noise is still
  a cost to a reviewer. They are also reported separately so the decision stays visible and
  arguable.
- **Only exact duplicates are collapsed** — identical `finding_hash`, evidence included.
  Fuzzy near-duplicate clustering is deliberately excluded from primary scoring: merging two
  *different* defects in the same region would remove a wrong finding from the precision
  denominator and silently raise precision. A heuristic that can only improve the headline
  has no place on the scoring path.
- **One finding verifies at most one bug.** Ties are broken deterministically by largest
  localisation overlap, then lowest `bug_id`. A bug verified by several findings counts once
  in the recall numerator.

---

## Reproducibility

Results are only comparable when six version fields agree; `results.json` carries all of
them, and any difference must be flagged in a report.

Environment comparability is a `sha256` fingerprint over the base image digest, prepared
image digest, OS id and version, runtime version, package manager version, lock manifest
hash, and architecture. Two runs whose fingerprints differ **are not comparable**, however
close their numbers look.

Reproducibility is stated as three separate things rather than one promise: the **prepared
image digest** (the only bit-level anchor), the **lock manifest** (exact language-level
versions), and the **rebuild recipe** (best effort, *not* byte-identical). If a prepared
image is lost, a rebuild may differ and its results must carry a different fingerprint.

For traces produced by the current Gate A pipeline, replay validates a contiguous sequence,
exactly one header/cost/termination event, summed per-call usage, and aggregate cost before
it scores. The eight historical `runs/live-*` traces predate that event contract and are
retained as explicitly warned legacy evidence, not silently treated as replayable.

---

## Fail-closed behaviour

The evaluator refuses rather than approximating. Any of these produces a non-zero exit and
no `verified_*` output:

1. any candidate pair with no ledger entry — reported as `unadjudicated_pairs = N`
2. `fixture_version` disagreeing with the trace
3. `tree_checksum` disagreeing with the manifest
4. a ledger entry whose hash does not verify
5. an unsupported `trace_schema_version`

Partial adjudication never produces a headline number.

The two ledgers are separate by construction. The formal ledger holds only real human
rulings and currently contains two append-only rulings for two findings against
`fx-taskq-py/B-001`. They do not cover a complete run and were made by the fixture author,
so they support no publishable model metric. The synthetic ledger exists to validate the
evaluator's arithmetic; every result computed from it carries `ledger_kind: synthetic` and
`publishable: false`, and the evaluator rejects any `SYNTHETIC-` prefixed entry appearing in
the formal ledger.

---

## Limitations

The full list. Each qualifies every number this benchmark can currently produce.

1. **`verified_*` includes frozen human work.** It is not fully automatic. Once frozen it
   replays deterministically, but producing it the first time requires a person.
2. **Adjudicator independence is limited.** The fixture author and the adjudicator are the
   same person. Blinding removes brand bias, not knowledge of the intended answer.
   Mitigated by disclosure, a recorded rationale per ruling, and an append-only public
   ledger. A second independent adjudicator and a disagreement-resolution protocol are a
   **precondition** for publishing any model comparison.
3. **No independently verified, publishable model result exists in v0.1.** Eight live
   attempts were recorded (six billable), but their candidate pairs do not have complete,
   independent adjudication and their traces predate the current strict-replay contract.
   Committed `results.json` files come only from a deterministic scripted baseline against a
   synthetic ledger. They validate evaluator arithmetic and data flow; they are **not**
   model capability measurements.
4. **Non-empty residual defect lists are not supported.** A clean fixture must actually be
   clean; a suspected defect goes through the fix procedure, not an exemption register.
5. **No fuzzy deduplication, and the harness used to give the model no memory of its own
   past submissions.** Two distinct findings at the same location each count in the
   precision denominator — that is a deliberate choice, not an oversight, since a tool
   cannot reliably tell a resubmission from a second, genuinely different defect on the
   same lines. But a 51-step live run against a reasoning model (`runs/live-07`)
   resubmitted one seeded bug **seven times** under seven different ids, tagged three
   different categories along the way, because `write_findings` returned only a bare
   count and this harness never replays the model's own past turns — nothing ever told
   it "you already said this." Fixed in `agent/tools.py`: `write_findings` now notes an
   overlapping location in its return message, still recording both findings rather than
   refusing either.

   The fix is live-verified, not just mock-tested. `runs/live-08` re-ran the identical
   configuration afterward: of ten locations submitted more than once, none went past
   **two** submissions — where the unfixed run had gone as high as seven, and the specific
   bug that had been resubmitted seven times was resubmitted exactly twice. The
   distinct-location share of all submissions rose from 46.6% (27 of 58) to 64.3% (18 of
   28). This is one run compared with one run, not a controlled trial — the two runs also
   differ in what the model happened to explore — but the ceiling effect (nothing escalated
   past a second attempt, on the flagship case or any of the other nine) is a specific,
   causally suggestive result, not merely a smaller total.
6. **Two fixtures and eight bugs is low statistical resolution — not sufficient to rank
   models.** Its purpose is to show the methodology works end to end.
7. **First-party fixture realism has a ceiling.** These are services written to be measured,
   not production systems.
8. **Contamination resistance decays** after publication.
9. **Dollar costs are not fully comparable across providers.** Cache pricing, reasoning-token
   pricing and tiered pricing all differ. Any comparison reporting dollars must also report
   all four budget dimensions and state `completeness` — an estimate missing a priced
   component is `partial`, and comparing a partial figure with a complete one is not a
   comparison.
10. **The clean-control metric depends on `defects.md` completeness**, which is credible only
    at this size (≤ 3,000 LOC per fixture) and not beyond it.
11. **Sandbox isolation is observed on one platform only.** Gate H2 records the measure
    profile behaving as specified under one Docker version, kernel and architecture — see
    [SANDBOX_VERIFICATION.md](SANDBOX_VERIFICATION.md). It is not a security audit, says
    nothing about other runtimes, and does not cover the deliberately weaker witness
    profile.

Two further gaps in the current build, beyond the specification's list:

12. **Isolation is per-run, and a result is only as isolated as it says it is.** The agent's
    tools can run inside the measure container (spec §9.1), reaching no host path at all,
    and doing so produces scores identical to the host backend — asserted metric-for-metric
    under Docker. But the host backend still exists and is still the default, so every
    result carries a `tool_backend` field naming which one produced it. `host_process` means
    the tools ran unsandboxed, isolated by their own path checks rather than by the kernel.
    A result reported without that field, or with that value, is not evidence of containment.
13. **One component of environment identity cannot be checked offline.** Fixture identity
    and environment identity are both re-derived now: G3 rebuilds each tree from its commit
    and asserts the checksum and the `in_scope_loc` denominator, and `cae fixture
    environment` runs the prepared image and recomputes the §9.4 fingerprint from what is
    actually inside it. `base_image_digest` is the exception — a registry manifest digest
    that does not resolve offline, so it is consumed as an input to the fingerprint rather
    than verified, and is reported as an unverified observation rather than a passing check.
    What is checked without a network is that the rebuild recipe consumes the pin instead of
    naming a floating tag.

---

## Secondary analyses that must not be mixed in

LLM-as-judge scoring is **not implemented** in v0.1, and its boundary is fixed here so it
cannot later be folded in by convenience. If it is added, it answers only "how far would the
conclusion move under a model adjudicator", writes to a separate file, never enters
`results.json`, and never appears in the same column as a headline metric.
