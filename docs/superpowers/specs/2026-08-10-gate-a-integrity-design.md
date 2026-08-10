# Gate A Integrity Design

## Status and decision

This design implements the approved first gate of the flagship-promotion audit. Gate A makes
the existing v0.1 methodology slice internally consistent, machine-verifiable, and replayable.
It does not expand the corpus, publish a release, claim benchmark representativeness, or create
model-ranking results.

The two pre-existing working-tree changes are inputs, not cleanup targets:

- `.gitignore` keeps the user's added `*.keymap.json` policy.
- `ledger/adjudications.jsonl` keeps the user's two formal human rulings.

No step may reset, discard, rewrite, stage, commit, push, tag, publish, use a secret, or write to
an external service.

## Scope

Gate A has five deliverables:

1. Replace the obsolete "formal ledger must be empty" release assumption with a contract that
   permits valid human rulings and rejects synthetic or malformed entries.
2. Give the eight existing tasks a machine-readable registry and make committed results conform
   to the public result schema.
3. Make a live run use the private raw store and fail-closed sanitizer, emit complete replay
   metadata, and replay multi-call usage correctly.
4. Make local and CI gates execute the contracts they claim to execute, including every fixture
   witness and the clean-suite contract.
5. Reconcile public claims and prepare, but do not publish, citation/release metadata and a
   contributor-provenance audit.

## Non-goals

- No new fixture, bug, language, provider, dashboard, leaderboard, MCP server, hosted service,
  or paid model run.
- No claim that BugSeed is representative enough to rank developer agents.
- No conversion of the two formal rulings into verified model metrics without the required
  independent adjudication policy.
- No attempt to erase the twenty historical `Co-Authored-By` trailers. Existing Git history is
  immutable under the user's instruction; release remains blocked until the owner chooses a
  provenance strategy that can satisfy the GitHub Contributors requirement.

## Architecture

### Release contract

`cae validate` remains the fixture validator. A new release-audit module validates repository
artifacts that fixture validation cannot see: task registry, committed result documents, formal
ledger integrity, documentation evidence, release metadata, and Git identities/trailers. It
returns structured findings and a non-zero CLI status on any blocking issue.

The formal ledger contract changes from "zero entries" to "zero synthetic entries, every entry
schema-valid and hash-valid." The repository may contain human rulings without making any model
result publishable. Publishability remains a property of a fully replayed result, not merely the
ledger filename.

### Task registry

`schemas/task.schema.json` defines one immutable task as a fixture version, snapshot, optional
bug id, patch, witness, tree checksum, and split. `tasks/v0.1.json` enumerates the two clean
controls and eight mutated tasks. It references existing fixture artifacts instead of copying
canonical answers. Registry validation checks that fixture versions, bug ids, patches, witnesses,
and checksums resolve to the current fixture manifests.

### Evidence path

The live runner records complete private events into `RawStore`. Public evidence is written only
by `sanitize_run`, never by calling the projector directly. The event sequence is:

1. `run_header` with fixture/snapshot identity, tree and environment fingerprints, adapter and
   provider configuration hashes, budget, seed, and tool backend;
2. one `llm_call` per provider call, with request hash, latency, finish reason, and usage;
3. tool calls/results and findings;
4. one aggregate `cost` event;
5. one `termination` event.

Raw provider bodies and tool output remain private. Public events contain allowlisted metadata
only. A sanitizer rejection prevents all public evidence files from being committed to the run
directory.

Replay sums every `llm_call`, checks the aggregate cost event, rejects duplicate singleton events,
and validates trace provenance before scoring. Golden tests cover multiple calls so selecting the
first event cannot pass unnoticed.

### CI and release evidence

The Docker witness job invokes fixture verification for both fixture directories, exercising all
eight bug cycles and each declared clean suite. Non-Docker CI validates fixtures, tasks, the formal
ledger, committed results, public traces, tracked-file hygiene, and documentation links.

Release verification builds wheel/sdist, validates their contents, installs from a clean archive,
checks citation metadata and checksums, and audits author/committer/trailer identities. The
contributor audit is deliberately blocking on the current history; it reports the exact commits
and does not modify them.

## Error handling

- Unknown or missing event fields fail sanitization; no partial public trace is left behind.
- Missing, duplicate, or inconsistent replay singleton events are evaluation errors.
- Provider failures remain invalid attempts and retain sanitized failure classification.
- A task registry reference that does not resolve to current fixture bytes fails validation.
- A result not conforming to `results.schema.json` fails repository validation.
- Contributor identities or trailers outside the approved policy fail release audit without
  attempting remediation.

## Documentation and release metadata

README, data card, benchmark card, handoff, manual-run guide, run index, and fixture audit must use
one vocabulary:

- eight live attempts, six billable runs, four completed runs, total estimated cost `$0.399115`;
- formal ledger contains two human rulings but no independently adjudicated publishable result;
- v0.1 is a methodology preview, not a representative ranking benchmark;
- all committed baseline results are synthetic and unpublishable;
- live runs used `host_process` and therefore do not prove contained-agent execution.

`CITATION.cff` and `.zenodo.json` describe the same v0.1 preview, creator `kuotunyu`, MIT software
license, and no DOI. A release manifest records artifact hashes locally. Their presence means
"prepared for review," never "published on Zenodo."

## Testing strategy

Every behavioral change follows red-green-refactor:

- ledger test: a valid committed human ledger passes; a synthetic entry fails;
- task registry tests: all ten tasks resolve and a stale fixture version fails;
- result tests: every committed result validates, and unknown fields still fail;
- live evidence tests: raw events exist privately, public trace is sanitized, and rejection is
  atomic;
- replay tests: two LLM calls sum exactly; missing/duplicate aggregate events fail;
- CLI/CI tests: repository audit and all-fixture witness command expose correct exit codes;
- release tests: metadata is parseable and contributor audit reports current historical blockers.

Gate A is complete only when all implementable checks pass and the immutable-history contributor
blocker is reported explicitly. No release-readiness claim is allowed while that blocker remains.
