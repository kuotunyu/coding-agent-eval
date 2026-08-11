# TaskQ Clean-Control Corrections Design

## Goal

Correct the two TaskQ 1.0.5 defects exposed by paid clean smoke attempt 4, advance the
fixture to 1.0.6, and restore an offline-valid clean-control contract without changing or
relabeling the retained 1.0.5 outcome.

## Evidence boundary

Attempt 4 remains a failed TaskQ 1.0.5 clean-control outcome with two unverified candidate
findings. Deterministic local reproducers confirm both engineering conditions, but this is
machine evidence and an AI-assisted assessment, not independent human adjudication or a
`verified_*` benchmark detection.

No attempt-4 run, trace, finding, cost, fixture identity, or pricing byte is recomputed.
TaskQ 1.0.6 receives a new tree checksum, task-registry identity, OCI tag, manifest/config
digests, and environment fingerprint. No paid provider call is part of this correction.

## Atomic idempotent enqueue

For an enqueue with an idempotency key, validation remains outside the database
transaction. The live-key lookup, prior-task lookup, new task insertion, and key recording
then execute inside the existing `Storage.write_transaction()` boundary. Its process-local
lock serializes threads sharing a connection, while SQLite `BEGIN IMMEDIATE` serializes
writes across independent connections and processes.

The first committed transaction wins. A competing caller begins only after that commit,
observes the live key, and returns the original task. An expired key or a live key whose
task was purged may create and atomically bind a replacement, preserving existing behavior.
Unkeyed enqueue retains its current single-insert path; it does not pay for an unnecessary
explicit transaction.

Alternatives were rejected: a reservation-first protocol adds recovery states and
compensation logic, while a Python-only mutex does not protect multiple connections or
processes.

## Recoverable schema migration

Base schema creation remains idempotent. Version inspection, physical-column inspection,
the version-2 `ALTER TABLE`, and schema-version recording then run in one immediate write
transaction. Migration checks `PRAGMA table_info(tasks)` before adding
`lease_generation`.

This has two required behaviors:

- A normal schema-0/1 database without the column adds it and records version 2 atomically.
- A database left by the old interrupted migration with the column present but version
  0/1 skips the duplicate `ALTER` and records version 2.

The design does not catch and suppress arbitrary SQLite errors. Unexpected migration
failures still abort and roll back.

## Regression tests

The concurrency test uses real `TaskQueue`, `Storage`, SQLite, and two threads. A controlled
interleaving widens only the legacy non-transactional lookup window; the assertion is on
observable task IDs and row count. It must fail on 1.0.5 by producing two tasks and pass
after the transaction serializes the keyed operation.

The migration test constructs the exact interrupted physical state: the version-1 base
schema, an already-added `lease_generation` column, and no updated version row. Opening
`Storage` must succeed, retain exactly one generation column, and report schema version 2.

All existing storage, idempotency, queue, API, worker, witness, and fixture tests remain
green. The fixture's recorded test count increases from 220 to 222.

## Release closure

After RED/GREEN commits, document both corrected defects in `defects.md`, advance current
TaskQ metadata and task registry to 1.0.6, rebuild exact checksum/LOC values, and verify all
four seeded mutation cycles. Regenerate deterministic scripted baselines and release
documentation while preserving every historical reference/smoke identity.

Build and publish only the new versioned GHCR `1.0.6` image, then pin its immutable
manifest/config digests and recomputed environment fingerprint. Run the complete offline,
online OCI, Docker, leak, build, typing, formatting, test, provenance, and GitHub CI gates.
Stop before any new paid smoke, tag, GitHub Release, or Zenodo action.
