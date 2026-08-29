# Threat model

What this benchmark defends, what it assumes is hostile, and what it does not protect
against. Written so that a reader can tell which properties are **verified** and which are
still **design requirements** — the difference matters more here than the list itself,
because a sandbox described as working when nobody has watched it work is worse than no
sandbox, in that people rely on it.

- **Benchmark version**: `0.1.0`
- **Verification status**: the isolation properties in §9.2 of the design specification are
  **observed under Docker on one platform** — see
  [SANDBOX_VERIFICATION.md](SANDBOX_VERIFICATION.md). What that record does and does not
  establish is set out in [Residual risks](#residual-risks).

---

## What is actually at risk

Three things, in descending order of how badly losing them would hurt:

1. **The integrity of the measurement.** If an agent can read the answers, or modify the
   tree it is being scored against, every number this repository produces is fiction. This
   is the primary asset, and most of the machinery exists to protect it.
2. **The operator's machine and credentials.** The benchmark runs code that was written to
   contain defects, and in a later version will run code written to be hostile.
3. **Data that should not be published.** Raw traces contain full tool output and full model
   exchanges. Publishing one unfiltered would leak file contents, paths, and potentially
   whatever the operator's environment happened to contain.

---

## Trust boundaries

| Component | Trusted? | Reasoning |
|---|---|---|
| Fixture tree under measurement | **No** | Assumed hostile by policy, not by evidence. Today it is first-party code with seeded defects; v0.2 adds deliberately poisoned fixtures, and the boundary has to be drawn now rather than retrofitted then. |
| Agent output (findings, tool arguments) | **No** | It is model output. Paths are validated, findings are schema-checked, and neither is used to construct a filesystem operation without checking. |
| Model provider responses | **No** | Parsed defensively: a malformed tool call becomes an empty argument dict and an ordinary tool error, and a non-JSON body ends the run as `provider_error` rather than raising. |
| The harness itself | Yes | It runs on the host, unsandboxed. This is the assumption everything else rests on; see [what is not defended](#what-this-does-not-defend-against). |
| External `stdio-jsonl` agent process | Operator-trusted | It is selected by the operator and runs on the host as `host_unsandboxed`; the `cae-agent-stdio` handshake is identity matching, not containment or attestation. |
| Fixture manifests, patches, witnesses | Yes | Repository content, reviewed in Git. They are the ground truth; if they are wrong the benchmark is wrong, and no runtime check can help. |
| The adjudication ledger | Yes, with verification | Append-only and entry-hashed. The evaluator verifies hashes and refuses to score a tampered ledger rather than scoring it anyway. |

### External process boundary

The empty child working directory and environment allowlist reduce accidental exposure of
repository paths and ambient credentials. They do not contain hostile code: the executable
is an operator-trusted host process, can use host resources available to it, and may transmit
tool results after the harness returns them. **--isolate does not sandbox the external process.**
It applies only to fixture tool execution.

The external child may self-report normalized usage, but that evidence is labelled
`agent_reported_unverified`. Protocol 1.0.0 has no harness-enforced token or cost cap because
the harness cannot independently observe model calls made inside the child. The operator
must enforce upstream spend controls. These limitations apply even when the child completes
the `cae-agent-stdio` handshake successfully.

Protocol 1.0.0 closes every host envelope and payload. Initialization declares the three true
host capabilities; subsequent `next_step` requests expose only the capacity-aware tool list and
`null` or one incremental observation. Failed attempted requests remain owner-auditable while
public evidence discloses only the request hash, complete/partial write state, and safe offered
interface metadata.

### The boundary that matters most

**The agent must never see the answers.** Three mechanisms, none of which trusts the others:

- The measured tree is materialised from `tree/` alone. Bug manifests, patches, witnesses,
  audits and environment recipes live outside it and are never copied in.
- Gate **G4** scans the measured tree for authoring artefacts, any bug identifier, and any
  five-word run lifted from a canonical claim. It runs against the *mutated* tree, which is
  the one an agent actually sees.
- The witness overlay is delivered to a container at run time and is never written into the
  tree. This was found the hard way: an early version of the witness runner copied
  `witness/` into the measured tree, and G4 caught it.

---

## The three phases

Network posture differs per phase, and the distinction is the point.

| Phase | Container network | Produces |
|---|---|---|
| **prepare** | **On.** Dependencies have to be installed. | An immutable prepared image, its digest, `env.lock.json`, and an environment fingerprint. |
| **measure** | **Off** (`--network none`) for the tool container. | Raw evidence and a public trace. |
| **evaluate** | No container at all. Local file reads. | `results.json`. |

**What `--network none` does and does not mean.** It applies to the *tool container* — the
one holding the code under analysis. The agent's model calls are made by the harness on the
host, and host networking is unaffected. So the code being examined cannot talk to anything,
while the agent can still reason. Conflating these would either break the benchmark or
leave the fixture able to phone home.

### Measure profile

Non-root, `--cap-drop ALL`, `--security-opt no-new-privileges`, read-only root filesystem,
writable only in `/tmp` and a scratch area, `--network none`, and limits on pids, memory and
CPU. The image is referenced **by digest, never by tag**, because a tag can be repointed and
a result taken against a moving base is not reproducible.

**No host mount, ever.** The fixture tree is baked into the prepared image, so the container
has no path into the host filesystem at all — a stronger position than mounting one
read-only. The argument builders that construct every container invocation are asserted to
emit no `-v`, `--volume`, or `--mount`.

**The agent's tools can run in this profile, and a result records whether they did.** The
four tools ask a backend for bytes; the container backend answers from a process inside a
measure container, which has no host path to reach even if a path check were wrong. The
mutated tree is delivered as a tar stream piped to `docker exec -i`, which writes to the one
writable tmpfs — `docker cp` is refused against a read-only rootfs, and a bind mount is the
thing being avoided, so this is the delivery that concedes nothing.

The two backends produce byte-identical tool output over both fixture trees and
metric-for-metric identical scores, so isolation is free to choose. It is nonetheless still
a choice: the host backend remains the default because the fast suite cannot start a
container per test, and **`results.json` therefore carries a `tool_backend` field**.
`host_process` means the tools ran unsandboxed. A result that does not say it was contained
was not.

### The witness profile is weaker, deliberately

Witness contracts run under a separate profile with a **writable root filesystem**. This is
forced rather than chosen: the overlay is delivered with `docker cp`, and `docker cp` into a
`--read-only` container is refused outright by the daemon. The only other way in is a bind
mount — precisely what the no-mount rule exists to prevent. Between a writable root and a
host path inside the container, the writable root concedes less.

The network stays off, because `deterministic: true` is a claim every contract makes.

This profile runs fixture-authored verification code from this repository, which is a
narrower trust boundary than the measure profile has to hold. It is **not** the profile
agent output runs under, and must not be reused as one.

---

## Tool surface

The agent gets four tools: read a file, list a directory, search the tree, submit findings.
All read-only except the last, which writes only to the run's own finding list — **a run
cannot modify the tree it is scored against.**

- **Paths are checked twice.** Lexically, which rejects `..`, absolute paths and backslashes
  before touching the filesystem; and after resolution, which is the only way to catch a
  symlink inside the tree that points out of it.
- **Runtime resources are not parameters.** The tree root and the byte caps live in a
  context the harness supplies and are absent from the schema the model sees. A tool whose
  root is an argument is a tool the model can point elsewhere.
- **Output is capped** per file, per directory listing, and per search, so one call cannot
  exhaust a context window or make a run's cost depend on the size of the tree.

### Failure handling

An expected failure — missing file, escaping path, bad arguments — is returned to the agent
as content and the run continues. An unexpected exception is also returned, but counted:
**three consecutive** ones end the run as `harness_error`. Consecutive rather than
cumulative, because an intervening ordinary failure is evidence the harness still works.
A harness that is throwing produces numbers nobody should trust, and a plausible number from
a broken harness is worse than none.

---

## Data that leaves the machine

Two stores, and the boundary between them is enforced rather than remembered.

**Private raw store**: full tool output, full model exchanges. Never published. Pruned on a
retention schedule.

**Public trace**: a projection through a field allowlist. Every field of every event is
classified public, known-private, or unclassified — and an **unclassified field raises**.
A new event type carrying nothing today would otherwise pass silently and start carrying
something tomorrow.

The sanitizer that produces publishable artifacts is **fail-closed**: if it rejects, it
writes nothing at all, rather than writing a partly-cleaned file that looks finished.

The tracked-file leak scan (**G11**) applies the same rule set to everything under version
control, so a secret cannot reach a remote by being committed rather than by being traced.

### Provider conversation state

Both OpenAI wire formats use explicit, client-managed history. Chat Completions replays the
assistant message containing `tool_calls` before the linked `role: tool` result. Responses
replays every item from the previous `response.output` array—including function calls and
encrypted reasoning items—before appending `function_call_output` with the same `call_id`.
Responses requests set `store: false`; the benchmark does not use `previous_response_id` or
the Conversations API as a hidden state dependency. This follows the official OpenAI
[function-calling](https://developers.openai.com/api/docs/guides/function-calling) and
[conversation-state](https://developers.openai.com/api/docs/guides/conversation-state)
guidance.

The exact request and response bodies are retained only in the owner-controlled
`.run-store/` as known-private `llm_call` fields. The publishable trace keeps their request
hash, latency, finish classification, and usage, but the fail-closed sanitizer drops both
bodies. Provider free-text errors follow the same boundary: structural status/type/code may
be public; message text remains private because a provider can quote source or prompts.
Authorization headers are never traced, so the API key is neither in the raw event payload
nor in its public projection. `store: false` disables saved Response objects; it is not a
claim of Zero Data Retention certification or of the provider's wider account policy.

---

## Residual risks

Stated as risks, not as caveats. Each one is a thing that could go wrong today.

### 1. Sandbox isolation is observed on one platform only

Gate **H2** now observes the measure profile's properties rather than assuming them:
outbound connections refused, DNS unresolvable, root filesystem and measured tree
read-only, `/tmp` and the scratch area writable, uid 1000, `CAP_CHOWN` gone, runaway process
creation stopped by the PID limit, and an over-timeout command killed and reported.

What that does **not** establish:

- **It is one platform.** One Docker version, one kernel, one architecture. A different
  runtime or a rootless configuration may grant different things.
- **It is not a security audit.** It shows the specific properties asked for were granted.
  It does not show the container cannot be escaped.
- **The witness profile is not covered** and is weaker on purpose; see above.

Writing the gate justified its existence immediately: `/workspace/scratch`, the one
directory a run is supposed to write to, **was not writable at all**. The tmpfs mounted over
the image's `chown` and came up root-owned while the container ran as uid 1000. The flag had
been right since H1 and the behaviour never had been.

The same happened when the tool container's isolation was first asserted. The network probe
used `cat < /dev/tcp/...`, which is a bash feature — and the shell in both prepared images is
dash, where it fails whether or not there is a network. The test passed for the wrong reason
until it was rewritten to read the interface list out of `/proc/net/dev`. A probe that cannot
succeed proves nothing by failing.

### 2. The prepared image digest is local

Images are built locally and never pushed, so `prepared_image_digest` is a local image ID.
Anyone reproducing this must rebuild from `env/Dockerfile` and will get their own digest.
That is correct behaviour — the fingerprint is *supposed* to say "not the same environment"
when it is not — but it means the committed digests are not portable identifiers, and a
third party cannot verify they ran what we ran.

### 3. Reproducibility is bounded

There is no promise that system packages rebuild identically. Reproducibility rests on three
separate things: the **prepared image digest** (the only bit-level anchor), the **lock
manifest** (exact language-level versions), and the **rebuild recipe** (best effort, not
byte-identical). If a prepared image is lost, a rebuild may differ, and results from it must
carry a different `environment.fingerprint` and must not be compared with the old ones.

### 4. No defence against poisoned fixtures yet

v0.1 fixtures are first-party and contain no prompt injection or tool-output poisoning. The
untrusted-input assumption above is policy, not tested behaviour. v0.2 adds poisoned
fixtures with deterministic canaries — deterministic because a model judging whether a model
was manipulated is not evidence.

### 5. The adjudicator is not independent

The fixture author and the adjudicator are currently the same person, so the adjudicator
knows the intended answer for every bug and cannot be fully neutral about whether a finding
really describes that root cause. Mitigations in force: the relationship is disclosed, every
ruling records a rationale, and the ledger is append-only so any specific ruling can be
disputed. A second independent adjudicator and a disagreement protocol are a **precondition**
for publishing any model comparison.

### 6. Clean-control completeness is a claim about size

`benchmark_unsupported_findings_per_kloc` counts every finding on the clean control as
unsupported, which is only honest if the tree really is clean. That rests on the audits in
`defects.md`, and the completeness claim holds at roughly 1,200 lines per fixture and no
further. The audits are not hypothetical: they found four real defects across the two trees,
including two services that did not run at all when started as documented.

### 7. The sanitizer knows only the rules it has

It rejects what its rule set describes. A secret in a shape nobody anticipated passes. The
rule set is versioned and its corpus contains positive and negative cases for every rule, so
a gap is visible when found — but "no finding" means "no rule matched", not "no secret".

### 8. One component of environment identity cannot be checked offline

Fixture identity and environment identity are both re-derived now, not trusted.

Gate G3 rebuilds each `tree/` from `HEAD` and asserts its checksum and in-scope line count
against the manifest, then asserts the working copy is those same bytes — so drift between a
manifest and its tree is caught by `cae fixture rebuild`, not by someone happening to look.
Writing that runner turned up a real defect in the claim it was meant to check: `git archive`
honours `core.autocrlf`, so the manifests' checksums reproduced only because this repository
carries `core.autocrlf=false` locally. Conversion is now pinned off per invocation, and the
rebuild is asserted under all three settings.

`cae fixture environment` does the same for §9.4: it runs the prepared image, reads the OS,
runtime, package manager and architecture out of it, hashes the lock manifest on disk, and
recomputes the fingerprint to compare against the manifest. A mistyped digest, a drifted
image, or an edited lock file each fail it, and each is asserted failing.

**What remains unverified is `base_image_digest`.** It is a *registry manifest* digest,
obtained with `docker buildx imagetools inspect`. It does not resolve offline, and neither
base image is present on a machine that has only ever built the prepared images — the local
`node:22-bookworm-slim` is now a different image entirely, the tag having moved, which is
exactly why the pin exists. So the fingerprint check consumes it as an input from the same
manifest it compares against, and it is reported as an unverified **observation** rather than
as a passing check, because a green row would read as confirmation.

What is checked without a network is that the rebuild recipe actually *consumes* the pin —
`ARG BASE_DIGEST` and `FROM <image>@${BASE_DIGEST}`, with no floating `FROM` anywhere. That
is what makes the recorded digest load-bearing at build time instead of decorative. Confirming
the digest itself is a manual, online step.

### 9. Contamination resistance decays

The seeded bugs are novel and privately authored as of `2026-08-05`. Once published they may
enter future training data, and the property weakens with time and exposure.

### 10. External process cleanup does not own descendants

The stdio adapter closes, escalates, and reaps only the directly launched child process. It
does not create or manage a process group, Windows Job Object, or detached descendants. An
operator-trusted external agent must not daemonize or leave helper processes behind; the
harness does not guarantee those descendants stop when `cae run` ends.

---

## What this does not defend against

Said plainly, so nobody assumes otherwise:

- **A hostile agent adapter.** Adapters run on the host, unsandboxed, with whatever
  credentials the operator gave them. Running an untrusted adapter is equivalent to running
  untrusted code on your machine, and nothing here changes that.
- **A hostile model provider.** Responses are parsed defensively, but a provider that
  returns plausible, wrong tool calls is indistinguishable from a bad model.
- **A compromised Docker daemon or kernel escape.** Container isolation is the boundary; if
  it fails, everything inside it is on the host.
- **An operator publishing the private store directly.** The sanitizer protects the path it
  is on. Copying `.run-store/` somewhere public bypasses it entirely.
- **Malicious repository content.** Fixtures, patches and witnesses are trusted because they
  are reviewed in Git. A malicious commit that passed review would be trusted by everything
  downstream.
