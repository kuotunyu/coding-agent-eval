# GPT-5.6 Luna Pricing Refresh Design

## Goal

Ensure paid smoke attempt 4 records cost estimates from the official GPT-5.6 Luna
prices observed immediately before execution, without changing any historical trace
or result.

## Evidence and version boundary

The official GPT-5.6 Luna model page fetched on 2026-08-11 lists USD 0.20 per
million input tokens, USD 0.02 per million cached input tokens, and USD 1.20 per
million output tokens. The Responses API remains supported.

The current default pricing table advances from
`openai-gpt-5.6-luna@2026-08-11` to
`openai-gpt-5.6-luna@2026-08-11-r2`. The revision suffix distinguishes two price
observations made on the same calendar date. Its source remains the exact official
model URL and its effective/observed date remains 2026-08-11.

Historical reference and smoke artifacts retain their recorded pricing table,
token usage, and estimated cost bytes. They are not recomputed or relabeled.

## Cost and exposure behavior

The estimator continues to bill uncached input as `input_tokens -
cached_input_tokens`, cached input at the cached rate, and all output tokens once at
the output rate. Attempt 3's literal usage therefore costs USD 0.00630772 under the
new table. The attempt 4 harness cost stop remains USD 0.035 and the approved maximum
new provider-side exposure remains USD 0.05.

Aggregate limits are checked after a response returns. The exposure limit is a risk
authorization boundary, not a claim that the harness or provider independently
enforces a hard billing cutoff.

## Verification

A regression test uses attempt 3's literal token counts and independently derived
USD 0.00630772 expectation. Existing cache-accounting, reasoning-token, usage
aggregation, trace, and budget tests remain unchanged. Documentation distinguishes
the new table from historical cost evidence, the release manifest is regenerated,
and all offline/CI gates must pass before the paid request reads `.env`.
