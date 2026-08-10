# Running against a real provider

> **Eight live attempts were recorded on 2026-08-06: two zero-usage provider
> errors and six billable runs whose estimated costs total $0.399115.**
>
> `gpt-5.6-luna` against `fx-taskq-py`. Three via `OpenAICompatibleAdapter` with
> reasoning disabled (`runs/live-03` through `-05`) — the model refuses function
> tools on `/v1/chat/completions` at any other effort, so those measure a
> different subject from the model at its default. Three more via
> `OpenAIResponsesAdapter` with reasoning left on (`runs/live-06` through `-08`),
> which is live-observed, not just mock-tested — `reasoning_tokens` was 86.95%,
> 85.58%, and 91.55% of output tokens.
>
> What came out: `localization_recall` 1.0 in every mutated-snapshot run,
> computed by the deterministic matcher, with the same file reported zero times
> on the matching clean control. Two finding/bug pairs now have human rulings,
> but there is no completely adjudicated or independently verified result.
>
> What also came out, twice: `runs/live-05`'s clean control found a **real
> defect in the fixture** two author audit passes had missed
> (`fixtures/fx-taskq-py/defects.md`). `runs/live-07`, the first `/v1/responses`
> run to complete naturally, found a **real defect in the harness itself** — no
> memory of its own past turns, it resubmitted one finding seven times under
> seven ids. Fixed in `agent/tools.py`; `runs/live-08` re-ran the identical
> configuration afterward and capped every repeat at two (see
> `docs/BENCHMARK_CARD.md` limitation 5 for the full account).

> **Replay boundary:** all eight historical live traces predate the Gate A
> `run_header` and aggregate `cost` event contract. They remain useful historical
> observations, but the release audit warns that they cannot be replayed under
> the current strict contract. New runs write private raw events first and only
> publish after fail-closed sanitization.

## Two adapters, both now live-verified

`CAE_PROVIDER_API` picks between them; `chat_completions` is the default.

| | `chat_completions` | `responses` |
|---|---|---|
| Endpoint | `/v1/chat/completions` | `/v1/responses` |
| Adapter | `OpenAICompatibleAdapter` | `OpenAIResponsesAdapter` |
| Live-verified | **Yes** — three runs, 2026-08-06 | **Yes** — three runs, 2026-08-06 |
| Reasoning at this run's setting | `none` (forced) | `high` (`reasoning_tokens` 85.58–91.55% of output in completed runs; 86.95% in the token-capped run) |
| Reasoning + function tools together | Refused by `/v1/chat/completions` at any effort but `none` (why `reasoning_effort` exists as a knob) | Not restricted |

The second adapter exists for one reason: it is the only path that can measure
`gpt-5.6-luna` with reasoning left on. It was built from the documented request
and response shape — `input` items instead of `messages`, a flat tool schema, a
typed `output` array instead of `choices`, usage nested under
`input_tokens_details`/`output_tokens_details` — mock-tested exhaustively in
`tests/agent/test_responses_provider.py`, and then confirmed against real data:
`live-06`'s `estimated_cost_usd` ($0.092418) reproduces by hand from its usage
payload to six decimal places; `live-07` records $0.136248 and `live-08`
$0.127630. None of the three Responses runs terminated with `provider_error`.

One choice in that adapter has no equivalent in the other and is still
unverified in the specific sense that matters: a response can be HTTP 200 and
still carry a `status` other than `"completed"`, and every such status is
treated as a `provider_error` rather than acted on, because which non-completed
statuses actually occur has not been observed — none of the three live calls
hit one. Read the module's own docstring before assuming that branch is safe
just because the rest of the adapter is.

The three `responses` calls also did something the `chat_completions` calls
never got the chance to: run long enough (49–51 steps) to expose a harness-level
memory defect, described in `docs/BENCHMARK_CARD.md` limitation 5. Its fix was
itself validated the same way — mock-tested, then confirmed against a live
re-run under identical settings.

Both adapters land in the same run header field either way —
`agent_adapter`/`agent_adapter_version` name whichever one actually built the
request, never a hardcoded label — so a run is never left to be misread as the
other adapter's result.

## Why no gate needs a key

A gate that requires a secret does not run for anyone who lacks one, which is
the same as not having the gate. So none of them do. The offline gates drive the
scripted baselines in `coding_agent_eval.agent.baseline`, which produce every
termination reason and both extremes of every metric without a network.

That is also why the adapter takes its `httpx.Client` by injection: every path
through it is exercised with a mock transport, and that has to be a first-class
path rather than a testing hack.

## What is not verified

Everything below the mock. Specifically:

- **Real usage payloads have now been parsed, from one provider.** Six billable
  runs came back `completeness: complete` with no unknown fields, and the
  cached-input branch handled high cache rates — recomputing `live-06` cost by
  hand from its recorded usage reproduces the figure to six decimal places. That is one
  provider across two API shapes. Another provider may report a field this code files
  under "unknown".
- **Pricing exists for one model only.** `gpt-5.6-luna` has a real table with a
  source URL and an effective date. Every other model falls back to
  `PLACEHOLDER_PRICING`, which is all zeros and says so in its
  `estimator_limitations`: a zero there means *not priced*, not *free*. A dollar
  budget on an unpriced model is refused rather than accepted, because a cap that
  can never be reached is not a cap.
- **No rate limiting, retry, or backoff exists.** A 429 is treated like any
  other HTTP error: the run ends with `provider_error` and is excluded from
  aggregates. That is correct as an outcome and useless as a strategy.
- **Tool output is sent as `user` messages**, not `tool` messages, because this
  adapter does not replay assistant turns and so has no call ids to reference.
  A provider that scores differently on that shape would score differently here.

## Doing a run

1. Copy `.env.example` to `.env` and fill it in. `.env` is git-ignored and the
   tracked-file leak gate scans for key-shaped strings, so a key committed by
   accident fails the build rather than reaching a remote.

2. Build the prepared image for the fixture you are measuring. The manifest
   pins the digest the fingerprint was computed from, and a run against a
   different image is not comparable to one against this one:

   ```bash
   docker build --build-arg BASE_DIGEST=<from fixture.yaml> \
     -f fixtures/<fixture>/env/Dockerfile \
     -t <prepared_image_tag> fixtures/<fixture>
   ```

3. Confirm the fixture still validates and its witnesses still hold:

   ```bash
   uv run cae validate fixtures/<fixture>
   uv run cae fixture verify fixtures/<fixture>
   ```

4. Check the configuration without spending anything. `--dry-run` applies every
   refusal rule and prints the configuration with the key reduced to its
   presence, then exits without opening a connection:

   ```bash
   uv run cae run fixtures/<fixture> --out runs/<name> --dry-run
   ```

5. Run it. This is the command that makes billed requests:

   ```bash
   uv run cae run fixtures/<fixture> --snapshot mutated --out runs/<name>
   ```

   Add `--isolate <image-digest>` to put the agent's tools inside the measure
   container. Without it they run in the harness process, and the run header
   records `host_process` so the difference is never left to be inferred.

### What `cae run` refuses, and why

- **A dollar budget on a model with no pricing table.** The estimate would
  always be `0.00`, so the cap could never be reached and the operator would
  believe they were protected. Add rates with a source and date, or bound the
  run by tokens.
- **No budget at all**, since nothing would stop the run.
- **A malformed budget.** Silently dropping an unparseable limit leaves the run
  unbounded while its operator believes otherwise.
- **A missing key or model**, before anything is opened.

It also warns about any `CAE_`-prefixed variable nothing reads, because a
misspelled `CAE_MAX_TOKEN` is a run with no token budget.

### Which budget actually binds

`CAE_MAX_TOKENS` is measured. `CAE_MAX_ESTIMATED_COST_USD` is estimated from a
table that may be stale. At `gpt-5.6-luna` rates — $0.20 in, $1.20 out per
million — a 200,000-token run costs at most $0.24, so a $7 cap sits about two
orders of magnitude away from binding.

**Set the token budget as the real control**, and set a spending limit in the
provider's own account as the only backstop that does not depend on this code
being correct.

### What it does not do

`cae run` **does not score**. It writes a run header, the public trace
projection, and the findings. Turning findings into `verified_*` numbers needs a
person; a command that ran an agent and printed a recall figure in one breath
would make that step look optional.

## After a run

A run is not a result. Before any number from it is published:

- The public trace has to pass the sanitizer, which is fail-closed and writes
  nothing if it rejects.
- Findings have to be adjudicated by a person. No `verified_*` metric may be
  published from an unadjudicated run, and per spec §8.3.2 a model comparison
  needs a second independent adjudicator and a disagreement protocol.
- **No AI may author an adjudication.** Synthetic rulings carry a `SYNTHETIC-`
  prefix and force `publishable: false`, so a result scored against them cannot
  be mistaken for one scored against a human's.

## Reporting cost

Always `estimated_cost_usd`, never `cost_usd` (spec §11.3). It is an estimate
from a pricing table, and it carries the table's version, effective date, and
source so the figure can be recomputed later.

Costs across providers are **not** apples-to-apples: cache pricing, reasoning
token pricing, and tiered pricing all differ. Any comparison that reports
dollars must also report the four budget dimensions, and must state
`completeness` — an estimate missing a priced component is `partial`, and
comparing a partial figure with a complete one is not a comparison.
