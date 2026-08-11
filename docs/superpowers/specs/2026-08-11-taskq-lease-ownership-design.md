# TaskQ lease ownership correction design

## Status and evidence boundary

Paid smoke attempt 3 reported that an expired worker can complete a later lease of the
same task. A deterministic local reproduction confirmed the observable behavior. This
document records an AI-assisted engineering assessment, not an independent human ruling,
and creates no `verified_*` metric. The retained 1.0.4 smoke outcome remains failed and is
never relabeled as evidence from the corrected fixture.

## Root cause

`TaskQueue.acknowledge` and `TaskQueue.fail` identify work only by `task_id`. Task IDs are
stable across retries, so after worker A's lease expires and worker B leases the row, both
workers hold the same completion identifier. The completion paths accept whichever lease
is currently in `LEASED` state and do not check expiry. Read-then-write also occurs outside
the storage write transaction, leaving a time-of-check/time-of-use boundary.

## Considered designs

Using `attempts` as a lease generation is too weak because dead-letter requeue resets the
attempt counter. Using `leased_until` as a version is brittle across float serialization
and couples identity to timing. A random lease token would work but introduces a secret-like
capability that must be hidden from ordinary task status responses.

The selected design adds a non-secret, monotonically increasing `lease_generation` to the
task row. It never resets. Every successful lease increments it; completion must present
the generation returned by that lease.

## Runtime contract

- `Task` carries `lease_generation: int`, initially zero.
- `lease()` increments both `attempts` and `lease_generation` in the existing immediate
  write transaction and returns both values.
- `acknowledge(task_id, lease_generation)` and
  `fail(task_id, lease_generation, error)` require a positive integer generation.
- Each completion operation opens an immediate write transaction and validates, inside
  that transaction, that the row exists, is currently leased, has the supplied generation,
  and has `leased_until > clock()`.
- A mismatch or expired lease raises the existing `Conflict` error and performs no state
  mutation.
- The worker passes the generation from the exact `Task` it leased.
- HTTP ack and fail bodies require `lease_generation`; malformed or missing values return
  the existing 400 invalid-request response. The Python client requires the same argument.
- Task status may expose the generation. It is concurrency metadata, not an authorization
  secret; task routes are already explicitly unauthenticated and task IDs remain unguessable.

## Persistence and migration

SQLite schema version advances from 1 to 2. Migration adds
`lease_generation INTEGER NOT NULL DEFAULT 0`, preserving existing task rows. New databases
run the same migration path so there is one schema construction route. The schema version
is updated only after the column exists.

State validation and mutation remain within the single-connection write lock. This makes
the lease ownership check atomic with acknowledgement or failure.

## Fixture and benchmark lifecycle

The fixture advances from 1.0.4 to 1.0.5. All four bug manifests, clean witness metadata,
task registry references, LOC/test counts, checksums, environment fingerprint, and OCI
identity must be updated through existing lifecycle tools. Existing seeded patches retain
their semantics and must pass apply/witness/revert gates against 1.0.5; any patch or witness
affected by the required generation argument is regenerated rather than weakened.

The clean audit records this as an AI-discovered pre-registration fixture correction and
states explicitly that no independent human adjudication occurred. The old 1.0.4 image and
all smoke artifacts remain immutable.

## Test strategy

TDD begins with real queue tests proving that:

1. an expired generation cannot acknowledge or fail before re-lease;
2. generation 1 cannot mutate generation 2 after re-lease;
3. dead-letter requeue does not reuse an old generation;
4. the correct current generation can acknowledge and fail;
5. the HTTP API rejects missing, malformed, stale, and expired generations;
6. the worker and client forward the exact leased generation;
7. schema-1 databases migrate without losing task data.

After focused tests, run the fixture's full suite, patch/witness/revert verification,
repository tests, strict mypy, Ruff, build, leak scan, publication audits, Docker gates,
and GitHub CI.

## Authorization boundary

Local implementation and verification are authorized. No paid provider request, Git tag,
GitHub Release, Zenodo operation, or fabricated human ruling is authorized. A new paid
clean smoke requires explicit owner approval after the corrected 1.0.5 OCI identity is
published and all offline gates pass.
