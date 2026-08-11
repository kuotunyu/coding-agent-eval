# Paid smoke gate

This is the approval contract for the first live request made by the corrected
conversation adapters. It is deliberately smaller than the 10-task reference suite.
Passing it permits registration of a **new** suite ID; it never upgrades or overwrites
the historical suite.

## Proposed attempt 6 -- not authorized

The next valid paid action is one clean task against corrected TaskQ 1.0.6 with adapter
0.4.0. No API key may be read and no provider request may be made until the owner explicitly
approves this new attempt after the source, OCI, local gates, pushed `main`, and GitHub CI
are green.

| Dimension | Planned value |
|---|---|
| Provider / model | OpenAI / `gpt-5.6-luna` |
| API / adapter / prompt | Responses API / `openai-responses@0.4.0` / `0.2.0` |
| Conversation state | Manual history; replay complete `response.output` and linked `function_call_output` |
| Provider storage | `store: false`; no `previous_response_id` |
| Reasoning | `low` |
| Task | `fx-taskq-py/clean` on fixture 1.0.6 |
| Isolation | `ghcr.io/kuotunyu/coding-agent-eval-fx-taskq-py@sha256:fc4e636299244b23a04a57f02cba1ed84b2cd4919cdc248eb7cb9a495bc75fc3` |
| Output | `runs/smoke/smoke-2026-08-11-attempt-6/clean` plus owner-only raw store |
| Retry policy | One terminal outcome; no automatic or selective retry |

The proposed limits are 1,024 provider output tokens per request, 120,000 observed
aggregate tokens, 12 tool calls, 300 seconds, and a USD 0.035 harness estimated-cost stop.
Using `openai-gpt-5.6-luna@2026-08-11-r2`, expected cost is approximately USD
0.006--0.015 and the requested maximum new provider-side exposure is USD 0.05 for clean.
A clean finding or abnormal termination stops the gate without a retry. Only a passing
clean task would make one B-001 mutated task eligible, under a separate additional maximum
provider-side exposure of USD 0.05 and a distinct output identity. Neither task is
authorized by this proposal.

## Approved attempt 4

Attempt 4 is one corrected **clean task only**. It gets a new output identity and does not
reuse or replace attempts 1–3.

| Dimension | Planned value |
|---|---|
| Provider / model | OpenAI / `gpt-5.6-luna` |
| API / adapter / prompt | Responses API / `openai-responses@0.3.0` / `0.2.0` |
| Conversation state | Manual history; replay complete `response.output` and linked `function_call_output` |
| Provider storage | `store: false`; no `previous_response_id` |
| Reasoning | `low` |
| Task | `fx-taskq-py/clean` on fixture 1.0.5 |
| Isolation | `ghcr.io/kuotunyu/coding-agent-eval-fx-taskq-py@sha256:db6a0afabe3acfd9c704e020b27a5b55ccef430b4864d8e565711b0b9cbc8966` |
| Output | `runs/smoke/smoke-2026-08-11-attempt-4/clean` plus owner-only raw store |
| Retry policy | One terminal outcome; no automatic or selective retry |

Limits for this one task are 1,024 provider output tokens per request, 80,000 observed
aggregate tokens, 12 tool calls, 300 seconds, and a USD 0.035 harness estimated-cost stop.
The official table observed immediately before execution is
`openai-gpt-5.6-luna@2026-08-11-r2`: USD 0.20/M input, USD 0.02/M cached input, and USD
1.20/M output. Attempt 3's literal usage would estimate to USD 0.00630772 under this
current table, so attempt 4 is expected to cost approximately USD 0.004–0.015. Because
aggregate limits are checked after a response arrives, the approved maximum new
provider-side exposure is USD 0.05; this is a risk authorization boundary, not a spending
target or guaranteed hard billing cap.

The owner approved this exact clean attempt with maximum new provider-side exposure USD
0.05. The API key may be read only after the refreshed pricing tests, offline release gates,
pushed `main`, and GitHub CI are all green. A passing clean outcome would only unlock
preparation of one B-001 mutated smoke, which needs its own paid approval. A finding on the
clean task or any abnormal termination stops the gate without a rerun.

## Historical attempts 1–2 configuration

| Dimension | Planned value |
|---|---|
| Provider / model | OpenAI / `gpt-5.6-luna` |
| API / adapter | Responses API / `openai-responses@0.2.0` |
| Conversation state | Manual history; replay complete `response.output` and linked `function_call_output` |
| Provider storage | `store: false`; no `previous_response_id` |
| Reasoning | `low` |
| Tasks | `fx-taskq-py/clean`, then `fx-taskq-py/B-001` (`--bug-index 0`) |
| Isolation | `ghcr.io/kuotunyu/coding-agent-eval-fx-taskq-py@sha256:392d4fbb33427c4fee63ee6b00fa055665ae37ec099acbc140594ed2010c19ad` |
| Retry policy | No automatic or selective retry; retain both terminal outcomes |

Per task limits:

- provider-side output: 2,048 tokens per request;
- observed aggregate usage: 20,000 tokens;
- tool calls: 12;
- wall clock: 300 seconds;
- harness estimated-cost stop: USD 0.05.

Across both tasks, the harness cost budget is USD 0.10. At the pricing table checked
on 2026-08-11 (USD 1.00/M input, USD 0.10/M cached input, USD 6.00/M output), the
expected total is approximately USD 0.02–0.06. Aggregate token and cost checks happen
after a response arrives, so they can overshoot by one in-flight request. OpenAI
project budgets may be soft thresholds unless the platform explicitly marks a spend
limit as enforced; they must not be described as an independent hard cap.

## Retained outcome — attempt 1

Approval was received for a maximum USD 0.15 exposure. The clean task ran once on
2026-08-11 and stopped at `budget_exhausted_tokens`; B-001 was not started.

| Field | Observed value |
|---|---:|
| Terminal outcome | `budget_exhausted_tokens` |
| Findings | 0 |
| LLM calls / tool calls | 7 / 7 |
| Input / cached input / output | 20,739 / 10,289 / 260 tokens |
| Reasoning tokens | 102 |
| Estimated cost | USD 0.013039 |

The model made seven successful read-only inspection calls. Each retained
`function_call` was followed by a `function_call_output` with the exact same
`call_id`; public evidence contains only sanitized metadata. The gate failed because
manual-history input accumulated beyond the 20,000 observed-token budget, not because
of a provider, tool, isolation, or linkage error. The terminal outcome remains under
`runs/smoke/smoke-2026-08-11/clean`; it will not be overwritten or selected away.

## Retained outcome — attempt 2

The revised clean task raised the observed-token budget to 80,000, kept a 12-call tool
budget, reduced the request output ceiling to 1,024, and used a USD 0.04 task cost
stop. A local append-only raw-store collision was found before any request, fixed with
path-derived hashed manual run IDs, and covered by regression tests. The approved
provider run then executed once and again stopped at `budget_exhausted_tokens`; B-001
was not started.

| Field | Observed value |
|---|---:|
| Terminal outcome | `budget_exhausted_tokens` |
| Findings | 0 |
| LLM calls / tool calls | 12 / 12 |
| Input / cached input / output | 80,522 / 61,312 / 441 tokens |
| Estimated cost | USD 0.027987 |

Attempt 2 made twelve successful read-only inspection calls with no tool errors. Exact
call-ID linkage and the private/public boundary remained valid. Cumulative observed
cost across both attempts is USD 0.041026. Raising budgets alone did not close the
gate: prompt 0.1.0 did not tell a clean-review agent how to stop or disclose its finite
tool budget.

Adapter 0.3.0 now renders prompt 0.2.0 from the registered tool-call budget and binds
the rendered prompt version/hash into schema-1.1 suite identity. This corrected method
has offline mocked evidence only. Neither attempt above can be relabeled as adapter
0.3.0 evidence.

## Gate sequence

1. Keep the real key outside Git and outside the public evidence directory. Validate
   the redacted configuration with `cae run --dry-run`.
2. Run the clean snapshot once. It must terminate normally, leave zero candidate
   findings, preserve raw events only in `.run-store/`, and emit a valid sanitized
   public trace.
3. Run B-001 once. It must terminate normally and use `write_findings` to create at
   least one candidate finding. A candidate is not a verified detection.
4. Validate the artifacts, replay/sanitization contract, fixture identity, and leak
   scan. Confirm exact `call_id` linkage in the owner-only raw request sequence without
   copying raw payloads into Git.
5. If either task fails the gate, stop. Keep that outcome and fix the method; do not
   start the 10-task suite and do not rerun merely to select a better result.
6. If candidate pairs exist, export a blinded worksheet and private keymap. A primary
   and an independent human reviewer—not an AI, script, fixture author, or run
   operator—must rule them before any `verified_*` metric can be published.

## Revised attempt requires new approval

The owner approved attempt 3 with cumulative provider-side exposure capped at USD
1.00. The harness kept the narrower USD 0.035 per-task stop; increasing the approval
ceiling did not require spending up to it.

## Retained outcome -- attempt 3

The clean task ran once with adapter 0.3.0/prompt 0.2.0, the 80,000-token observed
budget, 12 tool calls, and 1,024 output tokens per request. It terminated normally and
submitted one candidate clean-control finding. B-001 was not started because a clean
control must produce no findings for this smoke gate to pass.

| Field | Observed value |
|---|---:|
| Terminal outcome | `completed` |
| Findings | 1 (unverified clean-control report) |
| LLM calls / tool calls | 12 / 11 |
| Input / cached input / output | 88,934 / 70,786 / 1,052 tokens |
| Reasoning tokens | 446 |
| Estimated cost | USD 0.031539 (retained prior table) |

All 11 function calls have an exact linked `function_call_output`. Public artifacts
contain no API key, raw request/response body, or encrypted reasoning. A deterministic
local reproduction showed that after an expired task is re-leased under the same task
ID, acknowledging with the old worker's task ID transitions the current lease to
`done`. This is machine evidence only, not a human validity ruling.

The smoke gate therefore remains **failed**. The behavior was reproduced deterministically
and conservatively accepted as an in-scope engineering defect without representing that
assessment as an independent human ruling. TaskQ 1.0.5 fixes it with a monotonic lease
generation and a new immutable OCI identity; attempt 3 remains 1.0.4 evidence and cannot
validate the correction. Cumulative observed cost across all three attempts is USD
0.072565, below the approved USD 1.00 maximum exposure. No mutated task, 10-task suite,
verified metric, or selective rerun was produced.

## Retained outcome -- attempt 4

The separately approved TaskQ 1.0.5 clean task ran exactly once. It terminated normally but
submitted two candidate clean-control findings, so the gate failed and B-001 was not run.

| Field | Observed value |
|---|---:|
| Terminal outcome | `completed` |
| Findings | 2 (unverified clean-control reports) |
| LLM calls / tool calls | 12 / 11 |
| Input / cached input / output | 89,553 / 70,234 / 1,161 tokens |
| Reasoning tokens | 414 |
| Estimated cost | USD 0.006662 (`openai-gpt-5.6-luna@2026-08-11-r2`) |

All 11 assistant function calls were replayed and linked to a
`function_call_output` with the same `call_id`; every request used `store: false` and no
`previous_response_id`. Re-sanitizing the owner-only raw events reproduced the committed
public trace byte for byte. Public evidence contains no API key, raw request/response body,
or encrypted reasoning.

Deterministic offline reproducers confirmed both reported conditions: concurrent enqueues
with one idempotency key can create two tasks, and a simulated interruption after adding
`lease_generation` but before recording schema version causes the next startup to fail on
the duplicate column. This is machine evidence and an AI-assisted engineering assessment,
not an independent human ruling or a `verified_*` detection.

Attempt 4's paid authorization is consumed. It did not authorize a mutated task, rerun,
full suite, new reference registration, tag, GitHub Release, or Zenodo action. TaskQ 1.0.6
corrects both defects under a new fixture/OCI identity, but this retained outcome remains
1.0.5 evidence and does not authorize or validate attempt 5.

## Retained outcome -- attempt 5

The owner approved one TaskQ 1.0.6 clean task and a conditional B-001 task only if clean
passed. Clean ran exactly once with adapter 0.3.0/prompt 0.2.0. It exhausted the observed
token budget after its twelfth tool result, before a final provider turn, so the condition
for B-001 was false and the mutated task was not run.

| Field | Observed value |
|---|---:|
| Terminal outcome | `budget_exhausted_tokens` |
| Findings | 0 |
| LLM calls / tool calls | 12 / 12 |
| Input / cached input / output | 84,035 / 66,663 / 515 tokens |
| Reasoning tokens | 238 |
| Estimated cost | USD 0.005426 (`openai-gpt-5.6-luna@2026-08-11-r2`) |

The first eleven assistant function calls were replayed in subsequent requests with exact
linked `function_call_output` records. The twelfth tool result has no subsequent provider
request because the token budget stopped the run; this is expected terminal shape, not a
missing-link defect. Every request used `store: false` and no `previous_response_id`.
Re-sanitizing the owner-only raw events reproduced the committed public trace byte for byte.
Public evidence contains no API key, raw request/response body, or private reasoning.

Post-run review found a separate harness semantic defect: the loop reclassified every
explicit clean completion with zero findings as `no_output`, so the documented clean gate
could not represent a valid zero-finding completion. Adapter 0.4.0 now distinguishes an
actual final assistant message from absent or malformed output, and the loop preserves
that explicit reason. Attempt 5 remains adapter 0.3.0 evidence and is not relabeled.

Attempt 5's paid authorization is consumed. The clean gate failed abnormally, the
conditional mutated task was not eligible, and no new provider request is authorized.
Recorded estimates across all five retained paid attempts total USD 0.084653 across their
respective versioned price tables.
