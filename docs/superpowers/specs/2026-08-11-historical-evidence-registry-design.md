# Historical Evidence Registry Design

## Goal

Keep the 2026-08-10 reference suite reproducible after the current TaskQ clean
fixture advances from 1.0.4 to 1.0.5, without changing any historical outcome,
trace, finding, review record, or registration.

## Evidence boundaries

- `tasks/v0.1.json` remains the current 10-task registry and resolves against the
  current fixture tree (TaskQ 1.0.5 and Ledger 1.0.3).
- `runs/reference/task-registry.json` stores the exact registry bytes whose
  SHA-256 is already bound by `runs/reference/registration.json`. It describes
  TaskQ 1.0.4 and Ledger 1.0.3 and is historical evidence, not the current task
  registry.
- The reference registration and all retained terminal outcomes remain byte-for-
  byte unchanged. The old suite must never be made executable against the current
  fixture tree.
- Deterministic scripted baselines are regenerated against the current fixture.
  They remain synthetic, unpublishable evaluator checks and are not model evidence.

## Validation flow

Current suite registration and execution continue using strict fixture resolution:
the registry must match the current source tree, witnesses, OCI identities, and
environment fingerprints.

Publication audit uses a separate historical-load path. It validates the archived
registry schema, exact registration hash, task count/order, canonical suite ID,
budget arithmetic, and registration identity coverage without consulting current
fixture manifests. It derives each historical OCI tag from the archived registry's
single fixture version, while manifest/config digests remain those recorded in the
registration. Existing trace-contract checks then compare every retained trace to
that frozen task and OCI identity.

The offline audit fails closed when the sidecar registry is missing, malformed,
hash-drifted, has inconsistent versions for one fixture, or differs from registered
order/coverage. The current registry retains its existing strict validation.

## Tests

Regression tests first prove that:

1. a valid legacy registration can be audited using its exact sidecar registry
   after current fixture identities advance;
2. a changed sidecar is rejected by the registration hash;
3. current `load_registration` remains strict and rejects current-fixture drift;
4. regenerated scripted baseline results bind TaskQ 1.0.5 and remain synthetic and
   unpublishable;
5. the full offline publication audit returns no warnings or blockers.

No paid provider request, tag, GitHub Release, or Zenodo operation is part of this
change.
