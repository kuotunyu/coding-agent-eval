# Budget-aware completion contract design

Date: 2026-08-11  
Decision: approved by repository owner through delegated engineering judgment

## Problem

Two retained GPT-5.6 Luna clean smoke attempts validated Responses API linkage,
privacy projection, OCI isolation, and cost accounting, but neither completed. The
first exhausted a 20,000-token budget after seven tool calls; the second exhausted an
80,000-token budget at the twelfth tool call. Both produced zero findings and no tool
errors. The static system prompt asks the model to inspect code and submit supported
findings, but does not define how a clean review ends or tell the model that the tool
budget is finite.

Increasing budgets again would hide the missing completion contract and spend more
without improving evaluation validity.

## Decision

Keep the existing provider-neutral completion signal: a provider response with no
tool call terminates the run. Do not add a terminal tool or allow an empty
`write_findings` call.

Render a versioned system prompt for each run from the registered tool-call budget.
The prompt will:

- describe the review as selective rather than exhaustive;
- disclose the maximum tool-call budget when one exists;
- require the model to stop before consuming the whole budget;
- direct it to submit all supported findings in one `write_findings` batch;
- direct it to return a final response without a tool call after submitting;
- direct it to return a final response without calling `write_findings` when no
  supported defect was found;
- forbid continued reading solely to prove the absence of defects.

The existing `write_findings` non-empty-array contract remains unchanged. This avoids
turning an empty tool call into an ambiguous finding event and keeps both adapters on
the same terminal semantics.

## Identity and evidence

The prompt contract changes the measured agent, so both live adapter versions and the
system-prompt version will advance. New schema-1.1 suite registrations will bind:

- adapter name and version;
- system-prompt version;
- SHA-256 of the fully rendered prompt;
- registered budgets, including the tool-call count used to render the prompt;
- conversation state, request output limit, and Responses `store: false`.

Runtime suite execution will recompute and compare all bindings before opening a
provider connection. Registration identity therefore changes when the prompt,
adapter, or budget changes.

Historical suite evidence and both failed smoke attempts retain their original
adapter/prompt versions and terminal outcomes. They cannot be relabeled as evidence
for the new contract.

## Failure handling

Budget exhaustion remains a terminal retained outcome. The harness will not convert
it to completion merely because the task is clean or findings are empty. Provider,
tool, linkage, privacy, and isolation failures retain their existing classifications.

Manual run IDs will remain path-derived and hashed so separate attempts with the same
leaf name cannot collide in the append-only private store or disclose host paths.

## Verification

Regression-first tests will prove:

1. the rendered prompt contains the finite tool budget and both finding/no-finding
   completion paths;
2. an unbounded tool budget is represented explicitly without inventing a number;
3. Chat Completions and Responses receive the same rendered prompt contract;
4. suite registration binds prompt version and rendered-prompt hash;
5. prompt or budget drift invalidates the canonical suite identity and runtime check;
6. legacy schema-1.0 registration remains readable but cannot execute under the new
   adapter contract;
7. manual run IDs differ across attempt parent directories and reveal no host path;
8. existing multi-round call-ID, tool-error, budget, replay, and sanitizer tests stay
   green.

All verification before another smoke attempt is offline and uses mocked transports.
No paid request is authorized by this design.
