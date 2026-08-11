# Committed runs

> **These numbers describe no model.** Every result here comes from a scripted
> baseline — a fixed list of steps with no model, no provider, and no network —
> scored against a **synthetic** ledger. Each file carries
> `"decision_source": "synthetic"`, `"publication_reason": "synthetic_adjudication"`,
> and `"publishable": false`, which is the machine-readable
> form of that sentence.

They exist so gate G9 has committed evidence rather than a claim: the pipeline runs end to
end and produces the values it was predicted to produce.

## What is here

| Run | Snapshot | What it shows |
|---|---|---|
| `baseline-fx-taskq-py-mutated` | one seeded bug applied | a perfect agent scores recall and precision 1 |
| `baseline-fx-taskq-py-clean` | clean control | five unsupported findings over 1,467 in-scope lines |
| `baseline-fx-ledger-ts-mutated` | one seeded bug applied | the same, on the TypeScript fixture |
| `baseline-fx-ledger-ts-clean` | clean control | five unsupported findings over 1,183 in-scope lines |

The mutated runs use the `perfect` baseline, which submits a finding derived from the bug's
own localisation. That is not a claim about what an agent would write — it is the input that
makes recall exactly 1, so that a wrong denominator, a dropped finding, or a matcher that
stopped matching shows up as a different number rather than as an error.

The clean runs use the `high_noise` baseline, which submits five well-formed findings that
match nothing. On a clean control every finding is unsupported by definition, so the
per-KLOC figure is `5 / (in_scope_loc / 1000)` and can be checked by hand.

The 2026-08-10 model-backed reference suite is a separate historical evidence set.
`reference/task-registry.json` preserves the exact TaskQ 1.0.4 registry bytes bound by its
registration; `tasks/v0.1.json` describes the current TaskQ 1.0.6 corpus. Neither set is
silently interpreted as the other.

## What these files are not

- **Not a model result.** No model produced them.
- **Not publishable.** The `publishable: false` field is not advisory; the evaluator sets it
  because the ledger is synthetic, and no `verified_*` number from a synthetic ledger may be
  reported as a benchmark result.
- **Not a ranking of anything.** One scripted baseline, run against itself.
- **Not evidence of sandbox isolation.** Each file says `"tool_backend": "host_process"`,
  meaning the agent's tools ran in the harness process rather than in the measure container.
  That is deliberate — see below — and the field exists so it cannot be assumed either way.

## Isolation, and why these were not produced under it

The agent's tools can run inside the measure container — `--network none`, `--read-only`, no
host mount — and doing so produces **identical scores**. `tests/test_e2e_baseline.py`
asserts metric-for-metric equality between the two under Docker, and
`tests/sandbox/test_tool_container.py` asserts the two backends return byte-identical tool
output over both real fixture trees.

These committed files still use the host backend, because requiring a running daemon and a
locally-built image to regenerate them would cost the property that makes them useful:
anyone can rebuild them and compare. A result that *was* produced under isolation says so in
the same field, naming the image digest it ran against.

## Reproducing

```bash
uv run python scripts/regenerate_runs.py
```

Then `git diff runs/`. The runs are deterministic, so an empty diff means the pipeline still
produces exactly what is committed here, and a non-empty one is a real change rather than
noise.

```bash
uv run pytest -q tests/test_e2e_baseline.py
```

The same test asserts each value in this directory.
