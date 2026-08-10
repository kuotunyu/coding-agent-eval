# Metrics

The formulas, their denominators, what happens when a denominator is zero, and the reasoning
behind three naming decisions that would otherwise look like pedantry.

This describes what the evaluator **does**, not what it aspires to. Where the implementation
carries a case the design specification does not mention, that case is documented here
rather than left to be discovered.

Summary definitions live in [BENCHMARK_CARD.md](BENCHMARK_CARD.md). This is the derivation.

---

## Notation

For one run, over one fixture, at one snapshot:

| Symbol | Meaning |
|---|---|
| `B` | the bug set declared for that `fixture_version` |
| `F` | findings remaining after exact-duplicate collapse, **including** out-of-scope ones |
| `C` | candidate pairs `(f, b)` produced by the deterministic matcher |
| `V` | bugs credited as verified, after one-to-one assignment |
| `M` | findings credited with verifying some bug |

`M ⊆ F` and `V ⊆ B`. There is no separate "residual exclusion" set in v0.1: a clean fixture
must actually be clean, so nothing is ever excluded from a denominator for sitting near a
known-but-unfixed defect.

---

## Stage A — the deterministic matcher

No human, no model. A finding is a **candidate** for a bug when all three hold:

```
candidate(f, b)  ⟺  f.category == b.category
                AND  ∃ loc ∈ ({b.localization.primary} ∪ b.localization.acceptable_alternates) :
                         f.file == loc.file
                     AND [f.line_start, f.line_end]
                         overlaps [loc.line_start − tol, loc.line_end + tol]

                where tol = b.localization.line_tolerance
```

Category is checked first and is exact. Tolerance widens the bug's window, never the
finding's, so a vague finding spanning half a file does not become a candidate for
everything inside it by accident — though a wide enough range still can, which is why
Stage B exists.

`acceptable_alternates` lets a bug name more than one correct place to point at. A defect
whose root cause is a function's contract can be correctly localised either at the offending
statement or at the function; both are recorded, and either matches.

## Deduplication, and why only exact

Before matching, findings with an identical `finding_hash` collapse to one — the lowest `id`
lexicographically, so the choice is deterministic. The count is reported as
`exact_duplicates_removed`.

`finding_hash` covers file, line range, category, and the normalised claim, root cause **and
evidence**. Two findings that agree on everything but their evidence are therefore *not*
duplicates, and neither collapses into the other.

**Fuzzy near-duplicate clustering is deliberately absent from primary scoring.** Merging
findings by similarity would sometimes merge two *different* defects reported in the same
region. If one of them was unsupported, that unsupported finding leaves the precision
denominator — so fuzzy deduplication can only ever raise the headline. A heuristic whose
error direction is always favourable does not belong on the scoring path. It may be computed
as a diagnostic, provided it deletes nothing and changes no metric.

## Stage B — assignment after adjudication

A ruling of `same_root_cause` makes a pair verified. One finding can be ruled that way for
several bugs, and the resolution is deterministic:

- If those bugs **share one non-null `compound_group`**, all of them are credited. The
  manifest said in advance that they are facets of one defect.
- Otherwise **exactly one** is credited: largest localisation overlap first, then lowest
  `bug_id`. Without this, one vague observation spanning an overlapping region would collect
  credit for several distinct defects.

The reverse is unconstrained: several findings may verify one bug, and it counts once in the
recall numerator.

---

## The formulas

```
localization_recall  =  |{ b ∈ B : ∃ f ∈ F, candidate(f, b) }|  /  |B|

verified_bug_recall  =  |V|  /  |B|

verified_finding_precision  =  |M|  /  |F|

unsupported_findings  =  |F| − |M|

benchmark_unsupported_findings_per_kloc
                     =  unsupported_findings  /  (scope.in_scope_loc / 1000)

cost_per_verified_bug    =  estimated_cost_usd  /  |V|

tokens_per_verified_bug  =  (input_tokens + output_tokens)  /  |V|
```

Plus two counts that are reported rather than derived: `out_of_scope_findings` and
`exact_duplicates_removed`.

### Denominators, and why each is what it is

| Metric | Denominator | Why |
|---|---|---|
| `localization_recall` | `\|B\|` | Every declared bug, whether or not the agent looked at that file. Recall against "bugs it happened to examine" would reward not looking. |
| `verified_bug_recall` | `\|B\|` | Same. |
| `verified_finding_precision` | `\|F\|` | **Every** finding, including out-of-scope ones. Off-topic noise is still a cost to whoever reads the report. |
| `benchmark_unsupported_findings_per_kloc` | in-scope KLOC | Noise scales with how much code there is to be wrong about, so the denominator has to as well. Counted by `cae-loc`, which lives in this repository so the same rule runs everywhere. |
| `cost_per_verified_bug` | `\|V\|` | Cost per *result*, not per attempt. |
| `tokens_per_verified_bug` | `\|V\|` | Same, in a unit that survives a pricing change. |

Note the asymmetry: recall counts against all bugs, precision counts against all findings.
Both denominators are the largest defensible one, so neither metric can be improved by
narrowing what it is measured against.

---

## When a denominator is zero

Never `0`. Never `Infinity`. Never an omitted field. The metric is `null` and a reason is
recorded beside it in `undefined_reasons`.

| Condition | Metrics set to `null` | Reason |
|---|---|---|
| `\|B\| = 0` — a clean control | `localization_recall`, `verified_bug_recall` | `no_bugs_in_snapshot` |
| `\|F\| = 0` — the agent submitted nothing | `verified_finding_precision` | `no_findings` |
| `\|V\| = 0` — nothing was verified | `cost_per_verified_bug`, `tokens_per_verified_bug` | `no_verified_bugs` |
| `\|V\| > 0` but no cost was reported | `cost_per_verified_bug` | `cost_not_reported` |

The fourth case is not in the design specification and exists because the situation is real:
a run can verify bugs while its provider reported no usable cost. Emitting `0` there would
claim the run was free. `tokens_per_verified_bug` is still produced, because tokens are
counted by the harness rather than reported by the provider.

`benchmark_unsupported_findings_per_kloc` has no zero case: `in_scope_loc` is validated as
at least 1 and the LOC counter refuses an empty in-scope path list, precisely so this
denominator cannot silently become zero.

### The clean control is not a degenerate case

On a clean control `B` is empty, so `V` and `M` are empty and **every** finding is
unsupported. That is the metric's meaning, not an edge case being tolerated: the clean
control exists to ask "how much does this agent report when there is nothing to report".

---

## Three naming decisions

### `localization_recall` is not "bug recall"

Stage A establishes one thing: a finding pointed at the right file, within tolerance of the
right lines, with the right category. It establishes **nothing** about whether the finding
describes the defect correctly. An agent that reported "something is wrong around line 50"
for every function in a file would score well on it.

Calling it `bug_recall`, or `correctness_recall`, would claim semantic agreement that no
deterministic matcher can produce. The name is the guard: any table that wants to claim an
agent *found* a bug has to use a `verified_*` number, and those require a person.

### The clean-control metric is not a "false-positive rate"

`benchmark_unsupported_findings_per_kloc` counts findings unsupported **by this benchmark's
ground truth**, on a tree whose defects have been enumerated and audited.

Applying the phrase "false-positive rate" to a third-party repository would assert that
everything the agent reported and nobody confirmed was wrong. Nobody knows that: the true
defect set of an arbitrary repository has not been enumerated, so an unconfirmed finding may
be a real defect nobody had noticed. The `benchmark_` prefix is there to keep the number
attached to the only ground truth that justifies it.

This is also why the metric's denominator is audited. It is only honest if the clean tree is
genuinely clean — and the audits behind that claim found four real defects across the two
fixtures, each fixed before any bug was seeded.

### `estimated_cost_usd`, never `cost_usd`

It is an **estimate**, derived from a pricing table with a version, an effective date and a
source URL. `cost_usd` would read as a measurement of what was actually billed.

---

## Why dollar figures are not apples-to-apples

Reporting only dollars would make the comparison look cleaner than it is. Providers differ
in ways that change the number without changing the work:

- **Cache pricing.** Cached input is billed at a different rate, or not at all, and only
  some providers report how much of the input was cached. Where they do not, the field is
  `null` and the estimate is `partial`.
- **Reasoning tokens.** Some bill them, some do not, some do not report them at all. A model
  that spends heavily on reasoning may look cheap simply because its reasoning is invisible.
- **Tiered and negotiated pricing.** The same model can cost different amounts to different
  callers, so a dollar figure is partly a fact about the account it was billed to.

Three consequences, all enforced:

1. **Unknown is never zero.** A field the provider did not report is `null` and is named in
   `unknown_fields`. Recording `0` would turn silence into a measurement.
2. **Any unknown component makes the estimate `partial`.** Comparing a `partial` figure with
   a `complete` one is not a comparison, and `completeness` is what makes that visible.
3. **All four budget dimensions are kept**: tokens, tool calls, wall clock, and estimated
   dollars. Any comparison reporting dollars must report the others too. Tokens are the
   most portable of the four, because they are counted by the harness rather than priced by
   the provider.

---

## Replay

Once the ledger is frozen, re-scoring the same public trace produces a **byte-identical**
`results.json` — gate G6. That is what makes a published number auditable: anyone with the
trace and the ledger can recompute it and get the same bytes, not merely the same value.

The evaluator refuses rather than approximating. Any of the following ends scoring with a
non-zero exit and no `verified_*` output: a candidate pair with no ledger entry, a
`fixture_version` disagreeing with the trace, a `tree_checksum` disagreeing with the
manifest, a ledger entry whose hash does not verify, or an unsupported
`trace_schema_version`. Partial adjudication never produces a headline number.
