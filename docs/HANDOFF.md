# Handoff — v0.1 Gate A integrity work

- **Last updated**: 2026-08-10
- **Development source branch**: `implementation/v0.1`
- **Release target**: clean single-commit `main` lineage for `kuotunyu/coding-agent-eval`
- **Release state**: methodology preview; not a Release Candidate; Zenodo no-go
- **External authorization**: the owner created an empty public GitHub repository and
  authorized its first source push plus `zh-TW` About settings on 2026-08-10; no tag,
  GitHub Release, Zenodo upload, OCI upload, or paid provider call is authorized

This is the current operational handoff. Historical live observations remain in `runs/`,
and the original design/implementation records remain under `docs/superpowers/`; neither is
a substitute for the current contracts below.

## Preserve before doing anything

The checkout was already dirty when Gate A began. These user-owned changes must be kept
byte-for-byte and must never be reset, replaced, or attributed to an agent:

- `.gitignore`: the user added the adjudication key-map exclusion.
- `ledger/adjudications.jsonl`: the user added two formal human rulings.

Run `git status --short` before further work. The historical checkout stays dirty and is not
the repository that will be pushed. The approved release process copies its source snapshot
into a new Git repository, leaving the original index and history unchanged.

## What Gate A adds

- A versioned task schema and `tasks/v0.1.json`, covering two clean controls and eight
  mutated tasks with no unregistered task.
- Results-schema alignment for `schema_version` and `tool_backend`, plus regenerated
  synthetic baseline artifacts at the current fixture versions.
- A live evidence path that records private raw events first and only writes public output
  after fail-closed sanitization.
- Strict replay validation for event order, singleton header/cost/termination events,
  per-call usage aggregation, and aggregate cost.
- `cae fixture verify` execution of each clean suite followed by all four focused witnesses
  for each fixture; CI builds both pinned fixture images and runs the whole Docker matrix.
- A read-only `cae release audit` covering task/result/trace/ledger/link/metadata contracts
  and an opt-in immutable Git-history provenance check.
- Prepared `CITATION.cff`, `.zenodo.json`, and a deterministic checksum manifest. These are
  metadata preparation only; no DOI or published record exists.

Design and execution records:

- `docs/superpowers/specs/2026-08-10-gate-a-integrity-design.md`
- `docs/superpowers/plans/2026-08-10-gate-a-integrity.md`

## Evidence boundaries

There are eight retained live attempts from 2026-08-06: two zero-usage provider errors and
six billable runs with recorded estimated costs totaling $0.399115. They concern one model,
one fixture, and one seeded bug under two adapter configurations. They do not support a
capability or ranking claim.

All eight historical live traces predate the Gate A `run_header` and aggregate `cost` event
contract. The release audit therefore reports eight non-blocking `trace.legacy` warnings.
Do not rewrite those files to manufacture replay compatibility.

The formal ledger contains two human rulings for two findings matched to
`fx-taskq-py/B-001`. It does not provide complete run coverage. The adjudicator is also the
fixture author, so no independently verified, publishable model result exists. Committed
`results.json` files remain scripted-baseline validation with `ledger_kind: synthetic` and
`publishable: false`.

## Publication blockers

The contributor-lineage condition is handled by a clean release repository: it imports the
approved source snapshot as one commit authored and committed only by `kuotunyu`, while the
historical development repository remains intact and local. The remaining conditions require
owner/human action:

1. A second independent adjudicator and a documented disagreement protocol are required
   before any model comparison or publishable `verified_*` result.
2. The exact prepared images recorded in fixture manifests were local-only and are no longer
   present. Best-effort rebuilds match the recorded runtime components but correctly produce
   new image digests. A release must distribute pinned OCI artifacts or deliberately re-pin
   and rebuild the environment identity.
3. GitHub Release, OCI distribution, and Zenodo publication remain separate owner approval
   gates. The one-time source push and About authorization does not include them.

Until the publication criteria are resolved, do not create a GitHub Release or Zenodo record. The metadata files
are readiness inputs, not permission to publish.

## Verification commands

Fast, non-Docker checks:

```bash
bash scripts/check.sh
```

Repository contract and immutable history report:

```bash
uv run cae release audit
uv run cae release audit --check-git-history
```

Full Docker evidence:

```bash
uv run cae fixture verify fixtures/fx-taskq-py
uv run cae fixture verify fixtures/fx-ledger-ts
uv run pytest -q -m docker
```

`scripts/check.sh` deliberately does not claim to run Docker gates. No gate uses a provider
API or requires an API key, though building the pinned fixture images may fetch locked
dependencies. Run Git-dependent checks from a native checkout; WSL `/mnt/c` can synthesize
executable bits and legitimately change the tree checksum.

## Invariants

1. Preserve existing user modifications and append-only adjudication history.
2. Author and committer identity is `kuotunyu <61350295+kuotunyu@users.noreply.github.com>`;
   add no co-author or AI attribution trailer.
3. No AI authors a formal adjudication. Synthetic decisions remain test-only and force
   `publishable: false`.
4. The first source push and `zh-TW` About update were authorized on 2026-08-10. Do not tag,
   create a GitHub Release, upload OCI/Zenodo artifacts, expose credentials, or call a paid
   provider without separate explicit owner approval.
5. `finding_hash` includes evidence; primary scoring collapses exact hashes only.
6. `known_residual_defects.yaml` stays empty. A clean-tree defect stops scoring and requires
   a fixture fix and version bump.
7. Tree checksums identify committed bytes. Do not normalize or silently regenerate them.
8. Legacy evidence stays legacy; no backfill may imply data that was never recorded.
