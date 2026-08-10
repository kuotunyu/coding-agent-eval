# coding-agent-eval — v0.1 Implementation Plan

- **Date**: 2026-08-05
- **Revision**: 2 — hardened evaluation protocol (9 approved corrections applied)
- **Scope**: strictly v0.1 as defined in
  [`docs/superpowers/specs/2026-08-05-coding-agent-eval-design.md`](../specs/2026-08-05-coding-agent-eval-design.md) §2.1

---

## 0. Plan-wide rules

1. **Tests first.** Write a failing test, confirm it fails *for the expected reason*, write
   the minimum implementation, run task tests, run the full non-Docker regression, commit.
2. **No GPU** at any point.
3. **No paid API.** Every verification command runs offline. The provider adapter (F3) is
   verified with an injected mock transport; no live call is made anywhere in this plan.
4. **No remote, push, tag, or Release.** Commits are local to `implementation/v0.1`.
5. **Author/committer** is exactly `kuotunyu <61350295+kuotunyu@users.noreply.github.com>`,
   with no `Co-authored-by` and no AI tool attribution.
6. **Nothing is copied from the private archive** — no code, trace, report, or Git metadata.
7. **Docker-dependent tasks are marked** `[docker]`.
8. **One task = one logical commit** unless the task says otherwise.
9. **No AI may fill the formal adjudication ledger** (spec §8.3.1). Synthetic decisions live
   only in `tests/evaluator/fixtures/synthetic_adjudications.jsonl` and carry a
   `SYNTHETIC-` adjudicator prefix.

### Scope guard

Anything in spec §16 is out of scope. If a task appears to need an upstream fixture,
historical cohort, poisoned fixture, LLM-as-judge, MCP server, dashboard, sweep, or
Anthropic adapter — stop and re-read this line.

> **Naming note**: task IDs use prefixes `A/B/C/D/E/F/EV/H/I/X`. Spec §14 gate IDs are
> `G1…G11`. The namespaces are disjoint, so "G2" always means the witness gate.

---

## 1. Ordering

The witness gate must be **live before the third bug exists**. Revision 1 violated this by
building four bugs before the runner. Corrected order:

```
A1 → A2 → B1 → B2 → C1 → C2 → H1 → D1 ∥ D3 → D2a(canary B-001) → C3 (gate G2 live)
    → D2b(3 bugs) ∥ D4(4 bugs), each bug validated by G1/G2/G3/G4 as it lands
```

`A2` (leak scanner) precedes `B1` and every fixture or doc task — the hygiene gate exists
before there is anything to leak.

### Full dependency graph

```
A1 ── A2 ──┬── B1 ── B2 ──┬── C1 ── C2 ──┬── H1 ──┬── D1 ──┬── D2a ── C3 ──┬── D2b ──┐
           │              │              │        └── D3 ──┘               ├── D4 ───┤
           │              │              └──────────────────────────────── H2[docker]│
           │              ├── E1 ── E2 ── E3 ────────────────────────────────────────┤
           │              ├── EV1 ── EV2 ── EV3 ── EV4 ── EV5 ──────────────────────┤
           │              └── F1 ──┬── F2 ──────────────────────────────────────────┴── X1[docker]
           │                       └── F3
           └── I1, I2, I3, I4
```

| Group | Tasks | Note |
|---|---|---|
| P-1 | `I1`–`I4` | after `A2`; docs need no code |
| P-2 | `C1→C2`, `E1→E2→E3`, `EV1→EV2`, `F1→F2` | after `B2`; independent chains |
| P-3 | `D1` ∥ `D3` | the two clean fixtures are independent |
| P-4 | `D2b` ∥ `D4` | only after `C3` proves the witness gate on the canary |

Strictly sequential: `A1→A2→B1→B2`; `EV3→EV4→EV5`; `C3` needs `C2`+`H1`+`D1`+`D2a`;
`X1` is last.

---

## Track A — Scaffold and hygiene

### A1. Project skeleton, tooling config, CI shell

**Expected files**
```
pyproject.toml
src/coding_agent_eval/__init__.py
src/coding_agent_eval/cli.py
tests/conftest.py
tests/test_cli_smoke.py
.gitattributes
.github/workflows/ci.yml
```

**Tests first** — `cae --help` exits 0; `cae --version` prints the benchmark version.

**Implementation**
- `pyproject.toml`: package `coding-agent-eval`, Python `>=3.12`, console script `cae`,
  hatchling. Runtime deps: `jsonschema`, `pyyaml`, `httpx`. Dev: `pytest`, `ruff`, `mypy`.
  `mypy` strict on `src/coding_agent_eval`.
- `cli.py`: argparse skeleton; subcommands `validate`, `fixture`, `run`, `evaluate`,
  `sanitize`, `store`, `hygiene` stubbed to exit 2. One console script only.
- **`.gitattributes` — path-specific, per spec §6.7** (this is correction 6):
  ```gitattributes
  * text=auto eol=lf
  fixtures/** -text
  *.patch     -text
  ```
  Not `* -text`. Cross-platform stability comes from checkout policy; the checksum itself
  never normalises.
- CI workflow per spec §14.1: **`ubuntu-latest` and `windows-latest`** both run locked
  install, `ruff check`, `ruff format --check`, `mypy`, non-Docker `pytest`. Docker jobs are
  Linux-only and added by C3/H2/X1. Actions SHA-pinned, minimal `permissions`, checkout with
  `persist-credentials: false`.

**Verification**: `uv sync && uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q`

**Commit**: `chore: project skeleton, tooling config, dual-platform CI shell`

---

### A2. Hygiene policy and tracked-file leak scanner (gate G11)

**Depends on**: A1 · **Must precede**: B1 and every fixture/doc task

**Expected files**
```
src/coding_agent_eval/hygiene/__init__.py
src/coding_agent_eval/hygiene/patterns.py     # single source of truth
src/coding_agent_eval/hygiene/policy.py       # versioned, context-specific policy
src/coding_agent_eval/hygiene/leak_scan.py
tests/hygiene/test_patterns.py
tests/hygiene/test_leak_scan.py
tests/hygiene/test_email_policy.py
```

**Tests first**
- Positive corpus flagged: Windows absolute path, `\\?\`, UNC, `/home/…`, `/Users/…`, <!-- leak-scan-allow: absolute_path (rule definition quoted in the spec it defines) -->
  `/root/…`, `AIza…`, `sk-…`, `gh?_…`, PEM header, JWT-shaped, `.env`-shaped block.
- Negative corpus passes: relative paths, `<PROJECT_ROOT>` placeholders, SPDX ids, prose.
- **Correction 7 — two policies, one pattern set** (spec §10.8):
  - `61350295+kuotunyu@users.noreply.github.com` **passes** the tracked-file scanner.
  - Near-misses **fail** the tracked-file scanner: `kuotunyu@users.noreply.github.com`, <!-- leak-scan-allow: email (rule definition quoted in the spec it defines) -->
    `61350295+kuotunyu@users.noreply.github.com.evil.com`, <!-- leak-scan-allow: email (rule definition quoted in the spec it defines) -->
    `61350295+kuotunyu@users.noreply.github.co`, an old Gmail-shaped address, any other <!-- leak-scan-allow: email (rule definition quoted in the spec it defines) -->
    address.
  - The **same** official address **fails** the public-trace sanitizer policy.
  - The allowlist is exact-literal — a test asserts it is not applied as a regex or by
    domain match.
- `hygiene_policy_version` is exposed and asserted present.

**Implementation**
- `patterns.py` holds the regex family. `policy.py` composes them into two named policies:
  `TRACKED_FILE_POLICY` (email allowlist of exactly one literal) and
  `PUBLIC_ARTIFACT_POLICY` (no email allowlist).
- `cae hygiene leak-scan --tracked` scans `git ls-files` content.

**Verification**: `uv run pytest -q tests/hygiene && uv run cae hygiene leak-scan --tracked`

**Commit**: `feat(hygiene): versioned pattern policy and tracked-file leak scanner (G11)`

---

## Track B — Schemas

### B1. JSON Schemas

**Depends on**: A2

**Expected files**: `schemas/{fixture,bug,finding,trace-record,ledger-entry,results,known-residual-defects}.schema.json`, `schemas/README.md`

**Tests first**
- Each schema meta-validates as Draft 2020-12.
- `bug.schema.json` rejects a witness whose `expected_clean` deep-equals `expected_mutated`.
- `fixture.schema.json` rejects `provenance != first_party` at `schema_version 0.1`.
- `finding.schema.json` has `additionalProperties: false` and requires all nine fields.
- **Correction 3**: `known-residual-defects.schema.json` sets `"defects": {"maxItems": 0}`;
  a manifest with one residual entry is **rejected**.
- `ledger-entry.schema.json` requires `adjudicator_id`; a test records that
  `SYNTHETIC-` prefixed ids are schema-valid but rejected by the formal-ledger loader (EV3).

**Verification**: `uv run pytest -q tests/schemas`

**Commit**: `feat(schemas): manifest and artifact contracts`

---

### B2. Validator and `cae validate`

**Depends on**: B1

**Expected files**: `src/coding_agent_eval/schemas/{loader,validate}.py`, `tests/schemas/test_validate.py`, `tests/schemas/fixtures_invalid/`

**Tests first**
- Valid minimal fixture + bug pass.
- Each invalid sample fails with an error naming the offending **JSON pointer**.
- Cross-file consistency: `bug.fixture_id`/`fixture_version` mismatch, a listed bug with no
  manifest, a manifest not listed in `bugs[]`.
- **Correction 3**: a fixture whose `known_residual_defects.yaml` has a non-empty `defects`
  array fails validation with a message stating the fixture is not release-eligible.

**Verification**: `uv run pytest -q tests/schemas`

**Commit**: `feat(schemas): manifest validator with cross-file consistency checks`

---

## Track C — Fixture tooling

### C1. Loader, tree checksum, answer-leak audit

**Depends on**: B2

**Expected files**: `src/coding_agent_eval/fixtures/{model,checksum,leak_audit,loc}.py`, `tests/fixtures/test_{checksum,leak_audit,loc}.py`, `tests/fixtures/synthetic/`

**Tests first — correction 6 replaces the contradictory line-ending assertions**
- Checksum is stable across repeated runs and across file iteration order.
- Checksum is stable for the same committed fixture under the controlled checkout policy
  (`fixtures/** -text`), asserted by checking out the fixture twice via `git archive`.
- **Changing LF to CRLF in a file changes the checksum** — the checksum measures committed
  bytes and performs no normalisation.
- Checksum changes on: a byte edit, adding a file, deleting a file, toggling the executable
  bit.
- `leak_audit` flags a tree containing a `bug_id`, a `canonical_claim` 5-gram, a `*.patch`,
  a `witness/` dir, `defects.md`, or `known_residual_defects.yaml`; passes on a clean tree.
- `cae-loc`: blank lines excluded, whole-line comments excluded per language, block comments
  excluded, binaries excluded, `out_of_scope_paths` excluded.

**Verification**: `uv run pytest -q tests/fixtures`

**Commit**: `feat(fixtures): loader, byte-exact tree checksum, answer-leak audit, LOC counter`

---

### C2. Patch runner

**Depends on**: C1

**Tests first**
- `check` succeeds on a good patch, fails on drifted context.
- `apply` then `revert` restores the exact original `tree_checksum`.
- Applying twice fails rather than double-applying.
- A patch touching a path outside `tree/` is rejected before git is invoked.
- Every patch applies to the **same clean base** (asserted by checksum before apply).

**Verification**: `uv run pytest -q tests/fixtures/test_patcher.py`

**Commit**: `feat(fixtures): patch apply/check/revert runner`

---

### C3. Witness contract runner (gate G2) `[docker]`

**Depends on**: C2, H1, D1, **D2a canary bug**

**Tests first** — the full G2 cycle on the canary:
1. clean contract passes
2. `git apply --check` succeeds
3. mutated contract produces the declared `expected_mutated`
4. `git apply -R` restores
5. clean contract passes again
- A broken witness (same expected result on both trees) is rejected — by schema at B1 and,
  defensively, by the runner.
- A timing-out witness is reported as a contract failure, not a crash.
- Witness artifact `sha256` mismatch fails the contract.
- **Overlay must not use a host bind mount.** A test asserts the generated argv contains no
  `-v`, and that the overlay is delivered by `docker cp` into the container's writable
  workspace. After the run, a test asserts the measured tree never contained the overlay.

**Verification**: `uv run pytest -q -m docker tests/fixtures/test_witness_contract.py && uv run cae fixture verify fixtures/fx-taskq-py`

**Commit**: `feat(fixtures): witness contract runner with no-bind-mount overlay (G2)`

---

## Track D — Fixtures

Both fixtures are **first-party, MIT, authored here**. Realistic but bounded: a real service
shape with minimal dependencies, 1,500–3,000 in-scope LOC, deterministic clean contract.

Per **correction 3**, each fixture ships `known_residual_defects.yaml` with an **empty**
`defects` array. A non-empty array makes the fixture release-ineligible.

### D1. `fx-taskq-py` clean tree · D3. `fx-ledger-ts` clean tree

**Depends on**: H1 · **D1 ∥ D3**

Each provides: `fixture.yaml`, `tree/`, `env/{Dockerfile,env.lock.json}`, `defects.md`,
`known_residual_defects.yaml` (empty), `witness/clean_suite.yaml`.

**Tests first**: the fixture's own suite passes on the clean tree; `cae validate` passes;
`leak_audit` passes; `cae-loc` agrees with `scope.in_scope_loc`.

**Commits**: `feat(fixtures): fx-taskq-py clean tree and defect audit` /
`feat(fixtures): fx-ledger-ts clean tree and defect audit`

---

### D2a. Canary bug `fx-taskq-py/B-001`

**Depends on**: D1 · **Blocks**: C3

Exactly **one** bug, authored so the witness runner has something real to prove itself
against before any further bug exists. Category `security`.

**Verification**: `uv run cae validate fixtures/fx-taskq-py` (G2 runs in C3)

**Commit**: `feat(fixtures): fx-taskq-py canary bug B-001`

---

### D2b. `fx-taskq-py` bugs B-002…B-004 · D4. `fx-ledger-ts` bugs B-001…B-004

**Depends on**: C3 passing on the canary · **D2b ∥ D4**

Categories — D2b: `correctness`, `data_boundary`, `release_claim`.
D4: `concurrency` (mandatory), `correctness`, `data_boundary`, `security`.
Union across both fixtures: **5 of 5 categories**.

**Per-bug procedure — run immediately as each bug lands, never batched:**

| Step | Gate |
|---|---|
| `cae validate` | G1 |
| clean pass → apply → mutated as declared → revert → clean pass | G2 |
| tree checksum + LOC agree with manifest | G3 |
| answer-leak scan on the **mutated** tree | G4 |

Also verified per bug: localization line numbers checked against the **actual mutated
tree**; `authored_at` is the real authoring date (no fabricated provenance); the patch adds
no comment, identifier, or test name that reveals the defect.

D4's witness contracts exercise the **non-pytest** path: `expected_mutated` is a specific
non-zero exit code plus a stdout marker from a deterministic concurrency harness.

**Commits**: one per bug — `feat(fixtures): <fixture> <bug_id> (<category>)`

---

## Track E — Trace and sanitizer

### E1. Private raw evidence store

**Depends on**: B2

**Tests first**: append-only (rewriting a sequence number raises); content-addressed blobs
written once; `prune` respects the retention window; a test reads `.gitignore` and asserts
`.run-store/` is ignored.

**Commit**: `feat(trace): append-only content-addressed private evidence store`

---

### E2. Public trace writer — three-way field classification

**Depends on**: E1

**Correction 8** — the raw event schema classifies every field as one of:
`public_allowlisted` / `known_private_only` / unknown.

**Tests first**
- A `public_allowlisted` field appears in the public projection.
- A **`known_private_only`** field is **silently dropped** and does **not** raise.
- An **unknown** field (in neither list) causes the projection to **raise**, so E3 can turn
  that into a fail-closed rejection.
- `findings_submitted` records are retained in full.
- `tool_result` carries `content_sha256`, `content_bytes`, `excerpt_policy`; third-party
  content yields `excerpt: "<redacted>"`.
- `context_compression` carries `pre_view_hash`, `post_view_hash`, `raw_content_sha256[]`
  and never raw content.

**Commit**: `feat(trace): public projection with three-way raw field classification`

---

### E3. Fail-closed sanitizer (gate G5)

**Depends on**: E2

**Tests first**
- ≥2 poisoned samples per spec §10.5 rule; each causes non-zero exit **and**:
  - the public output path **does not exist**
  - **no partial file** remains (asserted by listing the output directory)
- An **unknown raw field** rejects the whole artifact (correction 8).
- A `known_private_only` field does **not** reject — it is dropped.
- The official noreply email **is rejected** here even though the tracked-file scanner
  allows it (correction 7).
- A clean artifact passes and is byte-identical to the golden output.

**Commit**: `feat(trace): fail-closed sanitizer with atomic output (G5)`

---

## Track F — Agent runtime

All written fresh against the spec. No file is copied from the private archive.

### F1. Adapter protocol and tool surface

**Depends on**: B2

**Tests first**: generated tool schemas are strict (`additionalProperties: false`, complete
`required`); `write_findings` rejects a payload violating `finding.schema.json` naming the
JSON pointer; path escape rejected in `read_file`/`list_directory`/`search_code`; injected
runtime resources absent from the model-facing schema; expected tool failures feed back
without terminating, and **three consecutive unexpected exceptions terminate with
`harness_error`** (spec §13.3).

**Commit**: `feat(agent): adapter protocol, strict tool registry, sandbox tool surface`

---

### F2. Deterministic fake baseline

**Depends on**: F1

Scripted behaviours: a perfect run, a zero-recall run, a high-noise run, a `no_output` run,
and one per termination reason. Two runs of one script produce identical public traces
apart from `run_id` and timestamps. No network, no API key.

**Commit**: `feat(agent): deterministic scripted baseline for offline gates`

---

### F3. OpenAI-compatible provider adapter (mock-verified only)

**Depends on**: F1

**Tests first**: message conversion round-trips through `httpx.MockTransport`; usage mapping
emits `null` (never `0`) for fields the response omits and lists them in `unknown_fields`;
all four budget dimensions terminate correctly; a missing API key raises **without**
attempting a request.

Adds `.env.example` and `docs/MANUAL_RUN.md`, which states plainly that no live run has been
performed. **No CI step may require an API key.**

**Commit**: `feat(agent): OpenAI-compatible adapter verified against mock transport`

---

## Track EV — Evaluator

### EV1. Exact-hash deduplication

**Depends on**: B2

**Correction 2** — v0.1 primary scoring uses **exact** collapse only.

**Tests first**
- Two findings with identical `finding_hash` collapse to one; the smallest `id` survives.
- Two findings that differ **only** in `evidence` **both survive** (their hashes differ —
  this is correction 1 observed from the dedup side).
- Two findings in the same region with different `root_cause` **both survive** — the
  Jaccard-style merge that would have hidden one is gone.
- `exact_duplicates_removed` is reported.
- A test asserts no Jaccard/token-similarity code path can remove a finding: the module
  exposes no such function, and precision denominators are unaffected.

**Commit**: `feat(evaluator): exact-hash deduplication for primary scoring`

---

### EV2. Candidate matcher and `localization_recall` (gate G7)

**Depends on**: EV1

**Tests first**: hand-authored synthetic finding set with pre-computed expected
`localization_recall`, asserted numerically; boundary cases at exactly
`line_start - tol` and `line_end + tol`, and one line beyond each; `acceptable_alternates`
counts; category mismatch blocks a candidate despite perfect overlap; the module emits the
key `localization_recall` and **no** key containing `bug_recall` or `correctness`.

**Commit**: `feat(evaluator): deterministic candidate matcher and localization_recall`

---

### EV3. Ledger, blinded export, formal/synthetic separation

**Depends on**: EV2

**Corrections 1 and 4.**

**Tests first**
- `finding_hash` matches spec §6.5 **including `evidence`**. Explicit assertions:
  - changing `evidence` **changes** the hash (replaces revision 1's inverted assertion)
  - changing `id`, `severity`, or `suggested_verification` does **not** change it
- Blinded export omits every one of: provider, model, adapter name/version, budget, cost,
  tokens, latency, `run_id`, `bug_id`, `finding_hash`, other findings' decisions — each
  asserted individually as absent.
- Import maps opaque batch sequence numbers back to `(bug_id, finding_hash)`.
- Append-only: rewriting an existing key raises. `entry_hash` detects tampering.
- `insufficient` counts as **not verified**.
- **Formal-ledger loader rejects any entry whose `adjudicator_id` starts with
  `SYNTHETIC-`** (fail-closed).
- The synthetic ledger loader accepts them but stamps `ledger_kind: "synthetic"` and
  `publishable: false`.
- `ledger/adjudications.jsonl` exists and is **empty**; a test asserts it has zero entries,
  so no AI-authored decision can slip in unnoticed.

**Commit**: `feat(evaluator): adjudication ledger with formal/synthetic separation`

---

### EV4. Metrics and `results.json` (gate G8)

**Depends on**: EV3

**Correction 3** — no `R` set, no residual exclusion, `F_scored = F`.

**Tests first**
- Every spec §8.5 metric against a hand-built scenario with values worked out in the test
  docstring.
- `unsupported_findings == |F \ M|`; there is no residual-exclusion code path and no
  `residual_defect_findings` key.
- `|V| = 0` → `cost_per_verified_bug`/`tokens_per_verified_bug` are `null` with
  `reason: "no_verified_bugs"`; never `0` or `Infinity`.
- `|F| = 0` → `verified_finding_precision` is `null`, `reason: "no_findings"`.
- `|B| = 0` (clean control) → recall metrics `null`, `reason: "no_bugs_in_snapshot"`, and
  every finding counts as unsupported.
- Out-of-scope findings are in the precision denominator and reported separately.
- One-to-one assignment across `compound_group`s is deterministic.
- Fail-closed (G8), each non-zero exit: unadjudicated pair, `fixture_version` mismatch,
  `tree_checksum` mismatch, `entry_hash` failure, unsupported `trace_schema_version`.
- Results computed from the synthetic ledger carry `publishable: false`.
- Aggregation reports `n_valid`/`n_invalid` and excludes invalid runs.

**Commit**: `feat(evaluator): headline metrics with fail-closed evaluation (G8)`

---

### EV5. Replay determinism (gate G6)

**Depends on**: EV4, E2

**Tests first**: `cae evaluate replay` on the golden trace is byte-identical to the committed
`results.json`; replay never reads `.run-store/` (monkeypatched to raise); mutating one byte
of the golden trace changes the output or fails closed.

**Commit**: `feat(evaluator): replay with byte-identical determinism (G6)`

---

## Track H — Sandbox

### H1. Profiles and fingerprint

**Depends on**: C1

**Tests first**: the measure argv contains every spec §9.2 flag — `--network none`,
`--cap-drop ALL`, `--security-opt no-new-privileges`, `--read-only`, `--pids-limit`,
`--memory`, `--cpus`, non-root user, tmpfs — and **no `-v` bind mount**; the prepare profile
does not set `--network none`; a tag-only image reference raises; the fingerprint is stable
and changes with each component.

**Commit**: `feat(sandbox): prepare/measure profiles and environment fingerprint`

---

### H2. Observed isolation behaviour `[docker]`

**Depends on**: H1

**Tests assert observed behaviour, not flags**: outbound network fails; root filesystem
write fails while workspace and `/tmp` succeed; `id -u` non-zero; a capability-requiring
operation fails; a fork bomb is contained by the PID limit; an over-timeout command is
killed and reported as a timeout; no host path is visible; the image is referenced by
digest; the container is removed afterwards.

`docs/SANDBOX_VERIFICATION.md` records Docker version, platform, image digest, and each
observed result. Until this task passes, every document describes §9.2 as design
requirements only.

**Commit**: `test(sandbox): observed isolation properties under Docker`

---

## Track I — Documentation

**Depends on**: A2 (so the leak scanner exists before docs are written) · I1–I4 parallel

- **I1 `README.md`** — spec §1.1 one-liner verbatim; what is and is not measured; the
  disclosure that `verified_*` includes frozen human adjudication; that v0.1 is a
  methodology slice (2 fixtures, 8 bugs) **not sufficient to rank models**; that there is
  **no live provider run and no paid API**; that committed E2E numbers are **synthetic
  evaluator validation, not model capability**; contamination wording restricted to spec
  §12.1 with no "zero risk" phrasing; may say the runtime evolved from an internal
  prototype but must not offer or link the private archive; no MCP content.
- **I2 `docs/THREAT_MODEL.md`** — trust boundaries, untrusted-input assumption, the three
  sandbox phases, residual risks including that isolation is unverified until H2.
- **I3 `docs/DATA_CARD.md` + `docs/BENCHMARK_CARD.md`** — provenance, authoring dates, LOC
  and counting tool, category distribution, licence, contamination statement with version
  and cutoff, retention policy; **explicit disclosure that the fixture author and the
  adjudicator are the same person** (spec §8.3.2) and that a second independent adjudicator
  is a precondition for publishing any model comparison; benchmark card carries all metric
  definitions with denominators and the full limitations list.
- **I4 `docs/METRICS.md`** — formulas, denominators, zero-denominator behaviour, the
  `localization_recall` naming rationale, the clean-control naming rule, and why
  `estimated_cost_usd` is not apples-to-apples.

**Commits**: one per document.

---

## Track X — End-to-end

### X1. Deterministic baseline E2E (gate G9) `[docker]`

**Depends on**: D2b, D4, C3, E3, F2, EV5, H1

**Tests first**
- The baseline runs 2 fixtures × 2 snapshots and produces the exact expected value for each
  headline metric, asserted by name: `verified_bug_recall`,
  `verified_finding_precision`, `benchmark_unsupported_findings_per_kloc`,
  `localization_recall`, `cost_per_verified_bug`, `tokens_per_verified_bug`.
- **All six come from the synthetic test ledger**, and every committed `results.json`
  carries `ledger_kind: "synthetic"` and `publishable: false`.
- The zero-recall script yields `null` cost/token-per-bug with
  `reason: "no_verified_bugs"`.
- Clean-control runs yield the expected
  `benchmark_unsupported_findings_per_kloc` with recall metrics `null`.
- The sanitizer accepts every artifact; the leak scanner passes on committed runs.
- Replay of each committed run is byte-identical.

**Commit**: `test(e2e): deterministic baseline across both fixtures (G9)`

---

## 2. Release checklist

- [ ] Spec §14 gates G1–G11 green
- [ ] 2 fixtures, 8 bugs, 5/5 categories, 2 languages
- [ ] Every bug passes G1/G2/G3/G4, verified as it landed
- [ ] `ledger/adjudications.jsonl` is **empty** — no AI-authored human decisions
- [ ] Every committed `results.json` is `publishable: false`, `ledger_kind: "synthetic"`
- [ ] `cae hygiene leak-scan --tracked` clean
- [ ] Dual-platform non-Docker CI workflow present; Docker jobs Linux-only
- [ ] Release verification (spec §14.2): wheel + sdist build, isolated wheel install smoke,
      sdist audit, clean `git archive` export install + test, privacy and file-size scan,
      doc link audit, Git identity audit
- [ ] Every commit authored/committed by `kuotunyu <61350295+kuotunyu@users.noreply.github.com>`,
      no `Co-authored-by`
- [ ] No remote, push, tag, or Release; no paid API; no GPU
- [ ] `docs/SANDBOX_VERIFICATION.md` exists, or every doc labels isolation as design-only
- [ ] Claims discipline: no assertion that hosted GitHub CI is green

## 3. Deferred to v0.2+

35-bug dataset, historical cohort, upstream fixtures, poisoned fixtures, partial/timeout
fixtures, Anthropic adapter, MCP server, LLM-as-judge sensitivity analysis, Jaccard
near-duplicate diagnostics, non-empty residual defects with semantic adjudication, second
independent adjudicator, dashboard, leaderboard, nightly sweep, Hugging Face deployment,
public remote, any paid API run.
