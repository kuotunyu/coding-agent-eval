# Capacity-Synchronized Tool Interface for the Formal TaskQ Smoke

**Date:** 2026-08-29

**Status:** Approved architecture; design checkpoint

**Scope:** One bounded remediation followed, only after offline verification and a separate paid-run authorization, by the preregistered `fx-taskq-py/clean` then `fx-taskq-py/B-001` sequence.

## 1. Decision

Adopt a harness-owned, capacity-synchronized tool interface with three phases:

1. `review`: more than one executable tool call remains; expose all four registered tools.
2. `report_only`: exactly one executable tool call remains and no `write_findings` call has yet been attempted; expose only `write_findings`.
3. `finalization`: no executable tool call remains, or `write_findings` has been attempted; expose no tools and request an ordinary final response.

The executable tool-call limit remains 12. The remediation does not increase tool, token, cost, wall-clock, or provider-step budgets. It changes which already-registered tools are available at a given capacity and makes that state visible in evidence.

## 2. Evidence and root cause

The six retained paid smoke attempts are historical evidence and remain immutable. They show a sequence of distinct failures rather than six interchangeable retries:

- attempts 1-2 ended on the earlier completion/budget contract;
- attempts 3-4 completed and correctly exposed genuine defects in older clean fixtures;
- attempt 5 used corrected TaskQ fixture 1.0.6 but ended on token exhaustion under the older adapter contract;
- attempt 6 used adapter 0.4.0 and TaskQ 1.0.6, executed all 12 registered tool calls, then made a thirteenth provider call for completion.

The structural raw evidence for attempt 6 shows that all four tools were still advertised on that thirteenth call, despite executable capacity being zero. The provider requested `read_file`; the harness refused it as `step_exhausted`. No tool result errors preceded that event.

The defect is therefore an interface/capacity inconsistency:

```text
harness capacity: 0 executable calls
model interface:  4 executable tools advertised
```

The system prompt was static and could only advise the model to stop early. Textual advice cannot make an advertised tool unavailable and cannot reserve the final registered tool call for reporting. Raising a budget or adding another reminder would leave the inconsistency intact.

## 3. Goals

The remediation must ensure that:

- the next provider request advertises no executable tools when remaining executable capacity is zero;
- the model always has an explicit non-tool final-response path;
- the harness never executes a tool absent from the interface advertised for that call;
- the last registered executable call is structurally reserved for `write_findings`;
- no executable call beyond the registered maximum can occur;
- raw and public trace evidence show the pre-call capacity and advertised interface;
- historical traces remain byte-immutable and readable;
- fixture content, provider bodies, credentials, and other private material stay out of public evidence;
- the formal paid sequence cannot expand, rerun, or switch mutations after seeing an output.

## 4. Non-goals

This change will not:

- add tools or fixture capabilities;
- add context compression;
- increase any formal smoke budget;
- change the scoring or human adjudication contract;
- turn candidate findings into verified detections;
- create a general suite-selection framework for a two-run release smoke;
- rewrite, backfill, or delete any of the six historical paid attempts;
- run the other seven TaskQ mutations;
- permit an LLM to act as primary reviewer, independent reviewer, resolver, or adjudicator.

## 5. Alternatives considered

### 5.1 Zero-capacity finalizer only

Expose all tools while capacity is positive and omit tools only at zero. This is the smallest patch and would address the final call from attempt 6, but it does not reserve a `write_findings` call. A mutated run could spend its twelfth call reading and reach finalization without ever having an executable reporting path. Rejected.

### 5.2 Dynamic prompt reminder

Add the remaining count to the prompt while keeping all tools available until zero. This improves observability but still delegates budget reservation to probabilistic instruction following. Attempt 6 already demonstrates that a static completion request is not an interface guarantee. Rejected.

### 5.3 Capacity-synchronized phased interface

Make the callable interface itself reflect the harness state and reserve the final executable slot for reporting. This is the selected design because it satisfies both the clean finalization case and the mutated reporting case without increasing budgets.

## 6. State model

Before every call to `AgentAdapter.next_step`, the loop computes:

```text
limit     = budget.max_tool_calls
remaining = max(limit - executed_tool_calls, 0)  # when limit is bounded
```

For an unbounded local/baseline budget, `remaining` is `null` and the phase is `review`. Formal paid runs are always bounded.

The phase and model-facing tools are selected as follows:

| Condition | Phase | Tools offered |
| --- | --- | --- |
| `write_findings_attempted` | `finalization` | none |
| bounded and `remaining == 0` | `finalization` | none |
| bounded and `remaining == 1` | `report_only` | `write_findings` |
| otherwise | `review` | `read_file`, `list_directory`, `search_code`, `write_findings` |

`write_findings_attempted` becomes true when the loop accepts a provider step whose invocation name is `write_findings`, before its handler result is known. This makes the one-batch contract fail closed: malformed findings cannot unlock a second submission attempt. A failed `write_findings` result therefore proceeds to tool-free finalization and fails the mutated automated gate because it was not a successful submission.

An ordinary final message is valid in every phase. A clean agent does not need to call `write_findings`; it may conclude directly from `review` or `report_only`.

The provider finalization call is a provider decision step, not an executable tool call. Existing token, estimated-cost, wall-clock, and provider-loop controls still apply to it. No budget is enlarged to accommodate it.

## 7. Enforcement

The loop passes only the phase-selected schema list to the adapter and retains the corresponding set of offered names for that provider step.

If a returned invocation is not in that set:

1. no tool handler is called;
2. `tool_calls` is not incremented;
3. the run terminates as the existing `step_exhausted` fail-closed outcome;
4. raw/public termination evidence is retained under the existing sanitizer boundary.

This includes a function call returned after a zero-tool request. The outcome remains a scored capacity failure rather than an invalid provider-infrastructure run: choosing an unavailable capability is behavior of the model under test, while the harness has correctly withheld and not executed that capability.

The existing `step_exhausted` guard remains as a fail-closed invariant. Under the new state machine it should be unreachable for a conforming adapter, but it continues to protect against internal regressions.

## 8. Provider request shape

Both maintained live adapters use the same phase-selected tool list and receive new adapter identity `0.5.0`.

For a non-empty list, the current tool configuration remains:

- one action per provider step;
- automatic tool choice;
- parallel tool calls disabled.

For an empty list, the request omits all tool-specific request members:

- `tools`;
- `tool_choice`;
- `parallel_tool_calls`.

Omission, rather than an empty executable-tool declaration, is the explicit provider-independent final-response path.

The shared system prompt becomes version `0.3.0`. It describes the phased interface:

- when inspection tools are available, inspect selectively;
- when only `write_findings` is available, submit all supported findings in that one call or return a clean final response;
- when no tools are available, return the final response and do not request a tool.

The state guarantee remains structural; the prompt is explanatory, not the enforcement mechanism.

## 9. Trace and public-evidence contract

Every recorded `llm_call` receives four harness-owned fields describing state immediately before that request:

```json
{
  "executable_tool_call_limit": 12,
  "executable_tool_calls_remaining": 1,
  "interface_mode": "report_only",
  "tools_offered": ["write_findings"]
}
```

For an unbounded run, the first two fields may be `null`. `tools_offered` follows the deterministic registered schema order.

These fields are safe for public evidence because they contain only first-party tool identifiers, enum-like state, and numeric capacity. Request and response bodies remain private. The sanitizer allowlist is updated explicitly and fail-closed tests prove that adjacent private fields are still removed.

Because the public-field classification changes, the redaction manifest advances from `0.1.0` to `0.2.0`. This is an additive change to the already-open `llm_call` payload of trace schema `0.2.0`; the event type and existing required fields do not change. The trace schema version remains `0.2.0` so historical evidence remains readable and is not reclassified. New run headers distinguish the method through adapter `0.5.0`, prompt `0.3.0`, redaction manifest `0.2.0`, and the existing prompt hash.

Historical evidence retains redaction manifest `0.1.0` and is not regenerated to add the new optional fields.

## 10. Offline TDD acceptance

Implementation starts with failing contract tests for:

1. `max_tool_calls=0` sends a provider step with no tools.
2. `remaining=1` offers exactly `write_findings`.
3. `remaining>1` offers all registered tools.
4. a successful or failed `write_findings` attempt makes the next provider step tool-free.
5. a clean final response can complete without `write_findings`.
6. finalization does not increment executable tool usage.
7. an unoffered tool invocation never calls its handler and ends as a structured provider error.
8. the registered tool-call maximum cannot be exceeded.
9. Responses and chat-completions requests omit all tool-specific members when the list is empty.
10. each `llm_call` records limit, remaining capacity, phase, and offered tool names.
11. public traces preserve those four fields while removing request body, response body, fixture content, and other private values.
12. historical public evidence and all existing schema/evaluator/review tests remain valid.

After focused tests, the full default offline suite, lint, type checking, release audit, and any repository-prescribed distribution checks must pass before any credential configuration is consulted.

## 11. Formal paid gate

Paid execution requires a separate explicit authorization after implementation and offline verification. Existing credentials may then be used only through the repository's existing safe configuration; they are never read, printed, copied, or committed by the remediation workflow.

The frozen order is:

1. `fx-taskq-py/clean`, TaskQ fixture 1.0.6, exactly once.
2. Only if clean terminates `completed` with zero candidate findings, run `fx-taskq-py/B-001`, exactly once.
3. B-001 must terminate `completed` and contain at least one successful `write_findings` tool result.

The fixed live identity and limits remain:

- provider API: OpenAI Responses;
- model: `gpt-5.6-luna`;
- reasoning effort: `low`;
- adapter: `0.5.0` after this behavioral change;
- prompt: `0.3.0` after this behavioral change;
- TaskQ: 1.0.6 with the already recorded OCI identity;
- maximum output tokens per request: 1,024;
- aggregate token stop: 120,000;
- executable tool-call limit: 12;
- wall-clock stop: 300 seconds;
- estimated-cost stop: USD 0.035 per run;
- provider-side maximum exposure: USD 0.05 per run.

No other mutation, fallback mutation, changed task, or rerun is allowed. Any failed gate retains its complete raw/private and sanitized/public evidence and stops the sequence.

## 12. Blinding and human boundary

After a successful B-001 automated gate, the existing review-set and worksheet tooling may produce blinded worksheets and private key maps. The key maps remain ignored and private.

Automated success means only that a candidate was submitted through the required interface and the run completed. It is not a verified detection.

Formal decisions must be completed by the existing human contract:

- a human primary reviewer;
- a distinct independent human reviewer who is not the fixture author or run operator;
- a third distinct human resolver if the two decisions disagree.

Codex, another LLM, or a script may initialize, validate, export, or import worksheet structure, but may not populate or impersonate any formal `DECISION` or `RATIONALE`. If eligible humans are unavailable, work stops with all automatable evidence prepared at this boundary.

## 13. Commit and publication boundaries

Local commits on the isolated `formal-smoke-remediation` branch are allowed after verification. This workflow will not push, tag, release, create a remote, or modify another repository. A passing local gate is evidence for a later human-controlled release decision, not authorization to publish.
