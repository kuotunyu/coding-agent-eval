# Clean terminal semantics design

Date: 2026-08-11  
Decision: approved through the repository owner's delegated engineering judgment

## Problem

Paid TaskQ 1.0.6 clean smoke attempt 5 retained a valid
`budget_exhausted_tokens` outcome with zero findings. While validating the next-step
method, the harness exposed a separate contract contradiction: `run_agent` rewrites
every `COMPLETED` stop with no findings to `NO_OUTPUT`. A provider can therefore follow
the prompt exactly, return a final assistant response without `write_findings`, and
still be classified as not completing. The documented clean gate requires a normal
terminal response with zero findings, so raising the token budget before correcting
this ambiguity would pay to test a gate that cannot pass under its stated semantics.

Attempt 5 remains unchanged evidence for adapter 0.3.0 and prompt 0.2.0. It did not
reach the contradictory branch, and this correction must not relabel its terminal
outcome.

## Considered approaches

1. **Classify terminal output at each adapter, then preserve the adapter's explicit
   reason in the loop (selected).** A valid provider assistant/message with no tool call
   is `COMPLETED`; a usable provider response with no assistant/message is `NO_OUTPUT`.
   Scripted baselines state `NO_OUTPUT` explicitly. This puts shape knowledge at the API
   boundary and keeps the loop provider-neutral.
2. Add a second terminal-output flag to `Step` and let the loop derive the reason. This
   is precise but duplicates information already expressible by `TerminationReason` and
   expands every adapter/test double for no additional evidence.
3. Treat `NO_OUTPUT` as a passing clean outcome. This is rejected because it makes an
   empty/malformed provider response indistinguishable from an explicit no-defect
   conclusion.

## Terminal contract

### Responses API

- A completed response with exactly one function call continues as today.
- A completed response with no function call and at least one output item whose type is
  `message` returns `Step(stop=COMPLETED)`.
- A completed response with no function call and no `message` item returns
  `Step(stop=NO_OUTPUT)`. Reasoning-only or empty output is not a final answer.
- Non-completed status, multiple calls, or missing call IDs retain their current error
  classification.

### Chat Completions

- A choice containing one tool call continues as today.
- A choice containing an assistant message with no tool calls returns
  `Step(stop=COMPLETED)`.
- Missing choices or a non-object/missing assistant message returns
  `Step(stop=NO_OUTPUT)`.
- Existing malformed/multiple-call handling remains unchanged.

### Provider-neutral loop and baselines

`run_agent` preserves an explicit `COMPLETED` stop even when `context.findings` is empty.
The findings list describes what was reported; it is not proof that a provider emitted
or omitted a final response. `NO_OUTPUT` remains reachable by adapters and baselines that
explicitly report it. The scripted `no_output` baseline will use that reason directly.

## Identity and historical evidence

Both live adapter versions advance from 0.3.0 to 0.4.0 because the same provider output
can now receive a different terminal classification. System prompt 0.2.0 does not change.
Future schema-1.1 registrations and traces bind the new adapter version; retained
attempts 1--5 and the frozen reference suite keep their original adapter, prompt,
fixture, OCI, usage, and terminal identities.

No paid request, suite registration, tag, GitHub Release, or Zenodo action is authorized
by this design.

## Regression-first verification

Tests must fail against 0.3.0 for the intended reasons before implementation, then prove:

1. a provider adapter that explicitly returns `COMPLETED` can complete with zero findings;
2. the scripted no-output baseline still produces `NO_OUTPUT`;
3. Responses message output completes while empty/reasoning-only output does not;
4. Chat assistant-message output completes while missing choices/message does not;
5. finding-producing multi-round completion remains unchanged for both adapters;
6. adapter version 0.4.0 flows into run and suite identity;
7. conversation replay, tool errors, budgets, privacy projection, and deterministic
   replay remain green.

After offline and CI gates pass, any further paid clean/mutated smoke needs a new,
explicit authorization and a new output identity. No historical outcome may be retried
or selected away.
