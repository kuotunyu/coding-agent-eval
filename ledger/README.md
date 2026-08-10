# Adjudication ledger

`adjudications.jsonl` holds **human** rulings, one JSON object per line,
append-only. It is the evidence behind every `verified_*` metric.

## Current state: two human rulings, no publishable result

The formal ledger currently contains two rulings by `kuotunyu`, both for distinct
findings matched to `fx-taskq-py/B-001`. They are preserved append-only. Two
rulings are not complete coverage of a run, and the adjudicator is also the
fixture author, so they support neither a publishable `verified_*` metric nor a
model comparison. The evaluator refuses to emit a headline while any candidate
pair is unruled.

## What may not go in here

No agent, model, or script may write an entry. A ruling records that a person
looked at a blinded pair and judged it, and a fabricated one makes every number
derived from it meaningless.

Synthetic decisions used to test the evaluator live in
`tests/evaluator/fixtures/synthetic_adjudications.jsonl`, carry a `SYNTHETIC-`
adjudicator id, and force `publishable: false` on anything scored with them. The
loader here rejects such entries outright.

## Why entries cannot be edited

Each entry carries a hash over its own contents, and a key appears at most once.
Editing a ruling after the fact would make the ledger unfalsifiable, so instead
the file is public, append-only, and every entry states a rationale that a third
party can challenge.

## Independence

For v0.1 the fixture author and the adjudicator are the same person, which is
disclosed in the data card. A second independent adjudicator and a
disagreement-resolution protocol are preconditions for publishing any model
comparison (design spec §8.3.2).

## How to actually adjudicate a run

Two commands, run in order. Neither one decides anything — a person reads a
document and rules on it in between.

```bash
uv run cae evaluate export runs/<run> --fixture-dir fixtures/<fixture> \
  --ledger ledger/adjudications.jsonl --out <worksheet>.txt
```

Writes `<worksheet>.txt` — a plain-text document, one item per unruled
candidate pair, in shuffled order — and `<worksheet>.keymap.json` beside it.
**The key map is private.** It is what turns a filled-in worksheet back into a
ledger entry, and it is the one file that must never reach whoever is
adjudicating, because it names which bug each item is really about.

Every item shows the code the run actually measured, the bug's own claim, and
the finding's claim/root_cause/evidence — nothing about the run, the model, the
cost, or which bug an item corresponds to. Fill in the two lines marked `>>>`
per item — `DECISION` is one of `same_root_cause`, `different_root_cause`,
`insufficient`; `RATIONALE` is required and has to fit on one line — then:

```bash
uv run cae evaluate import --worksheet <worksheet>.txt --keymap <worksheet>.keymap.json \
  --ledger ledger/adjudications.jsonl --adjudicator-id <your-id>
```

This appends the new rulings — existing entries are read first and kept, so
running `export` again afterward shows only what is still unruled.
`--adjudicator-id` must not start with `SYNTHETIC-`; that prefix exists to mark
rulings that were not made by a person, and this path writes to the formal
ledger only.

If a run has nothing to adjudicate — a clean-control snapshot, or a mutated
run whose findings never localised any bug — `export` says so and writes
nothing.
