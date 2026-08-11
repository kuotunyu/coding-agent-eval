# v0.1 Reference Suite 操作契約與結果

Reference Suite 是固定的 10-task execution contract：兩個 clean controls，加上八個 single-bug
mutated tasks。用途是產生可重播、可逐項追溯的 reference evidence，不是 leaderboard、model ranking
或一般 coding capability estimate。

## Frozen registration

| Field | Value |
|---|---|
| Suite ID | `suite-ca6834e720ce87309847af909c342789286f7cffb943b03e9e140c73e040d80b` |
| Created date | `2026-08-10` |
| Provider | `openai` |
| Model | `gpt-5.6-luna` |
| API | `responses` |
| Reasoning effort | `high` |
| Agent adapter | `openai-responses@0.1.0` |
| Tasks | 10（2 clean controls＋8 mutations） |
| Retry policy | `no_automatic_retry` |
| Per-task budget | 200,000 tokens／60 tool calls／900 s／USD 0.25 estimated |
| Suite maximum | 2,000,000 tokens／600 tool calls／9,000 s／USD 2.50 estimated |

Canonical registration：[`../runs/reference/registration.json`](../runs/reference/registration.json)；
其 SHA-256 綁定的 exact historical registry 位於
[`../runs/reference/task-registry.json`](../runs/reference/task-registry.json)。這份 snapshot 描述
TaskQ 1.0.4，不會被 current `tasks/v0.1.json` 的 TaskQ 1.0.6 identity 取代。
Task order、task registry SHA-256、model/config、budgets、OCI identities 與 environment fingerprints
都在第一個 paid call 前凍結。

## Ordered tasks

1. `fx-taskq-py/clean`
2. `fx-taskq-py/B-001`
3. `fx-taskq-py/B-002`
4. `fx-taskq-py/B-003`
5. `fx-taskq-py/B-004`
6. `fx-ledger-ts/clean`
7. `fx-ledger-ts/B-001`
8. `fx-ledger-ts/B-002`
9. `fx-ledger-ts/B-003`
10. `fx-ledger-ts/B-004`

Clean control 固定先於同 fixture 的 mutated tasks。Runner 不允許靜默覆寫既有 outcome directory，
因此 resume 也不能以較好結果取代先前 outcome。

## Immutable environments

| Fixture | Immutable ref | Config digest | Fingerprint |
|---|---|---|---|
| `fx-taskq-py` | `ghcr.io/kuotunyu/coding-agent-eval-fx-taskq-py@sha256:392d4fbb33427c4fee63ee6b00fa055665ae37ec099acbc140594ed2010c19ad` | `sha256:8796584be151aa59e641a7c4d70202f7d147ef6130241478a96f67459157e6d1` | `sha256:c58f1695a826a91308e1d839476b5d240c1e14e73f700a772d1b9bfd3594c3c9` |
| `fx-ledger-ts` | `ghcr.io/kuotunyu/coding-agent-eval-fx-ledger-ts@sha256:38450742408270a0e48ae053499dd626f61a4cf09139d40ae494838def4b0312` | `sha256:c7d310f6a41a47132484bddc47969547c9e34cb7628456415696c59af223d583` | `sha256:b2b8074ec374e30a5bd0634fd7d0524160a0a0d210ddbdc0c6654a3111b6e380` |

Registration 不接受 mutable tag 作為 execution identity。Current trace 的 `tool_backend` 必須為
`measure_container:<manifest_digest>`。

## Observed result

2026-08-10 依上述 registration 執行全部 10 tasks，一次一個、無自動重試：

- 10/10 寫出 `status.json`、`run.json`、`findings.json` 與 sanitized `trace.jsonl`；
- 10/10 status 為 `budget_exhausted`；
- 10/10 termination reason 為 `budget_exhausted_tokens`；
- 0 `completed`、0 provider errors、0 harness errors；
- 0 findings、0 candidate pairs；
- estimated cost 合計 USD 0.097166；
- wall-clock 合計 500.316 s，平均 50.032 s；
- 10/10 usage payload `complete`，trace schema 0.2.0。

Source of truth：[`../runs/reference/summary.json`](../runs/reference/summary.json) 與
[`../runs/reference/tasks/`](../runs/reference/tasks/)。

`10/10 retained` 只表示 outcome coverage。所有 tasks 都在 token budget 後停止，不能宣稱 10/10
成功；mutated tasks 沒有 candidate finding，不能宣稱 human-verified recall。Clean controls 的 0
findings 也因未完成而不能當作成功完成 code review 的證據。

此外，這個 frozen suite 使用的 adapter 0.1 沒有保留 assistant `response.output`／function-call
`call_id`，也沒有產生 `function_call_output`。因此上述 outcomes 只能作為舊協定的 retained failure
evidence，不能驗證目前 adapter 0.3／prompt 0.2 的 Responses conversation validity，也不能被重標成
新協定結果。
Registration schema 1.0 沒有綁定 adapter identity；runner 現在只允許它繼續被讀取與稽核，禁止再用它
執行。任何 current adapter suite 必須使用 schema 1.1 建立新 suite ID，並綁定 rendered prompt
version/hash。

## Human-review boundary

Dual blinded review 是每個 candidate pair 的必要 publication gate，不是每個 status file 的形式要求。
本次 candidate set 為空，因此沒有 review set／ruling／resolver；這既不缺件，也不代表 reviewer
agreement。AI 或 script 不得為了補齊數量製造 formal adjudication。

若未來 registration 產生 candidates：

1. Bind exact run、fixture、findings、trace 與 candidate-set hashes；
2. Primary 與 independent human 分別完成 blinded worksheet；
3. 全部 pair 都要有 decision 與 rationale；
4. Disagreement 由第三位 independent resolver 處理；
5. Private keymaps 不得進 Git；公開 review evidence 由 `kuotunyu` 驗證與提交。

## Dry-run／register／run contract

建立新的 configuration 時，先產生無 secret plan：

```powershell
uv run cae suite dry-run --env-file .env --tasks tasks --fixtures fixtures `
  --out PLAN.json
```

`dry-run` 驗證 provider、model、API、reasoning、四種 budgets、完整 task registry、fixture version、
fingerprints、OCI digests、adapter name/version、rendered prompt version/hash、`manual_history` 與
Responses `store: false`，不開
provider connection。Operator 必須檢查 exact plan 並取得 aggregate cost 明確批准。

核准後，先 register 再 run：

```powershell
uv run cae suite register --plan PLAN.json --tasks tasks --fixtures fixtures `
  --out runs/reference/NEW_SUITE_ID/registration.json

uv run cae suite run `
  --registration runs/reference/NEW_SUITE_ID/registration.json `
  --tasks tasks --fixtures fixtures --env-file .env `
  --out runs/reference/NEW_SUITE_ID
```

只有 `suite run` 會呼叫 paid provider。Valid status taxonomy 是 `completed`、`provider_error`、
`timeout`、`budget_exhausted`、`harness_error`、`fixture_defect`；任何 failure 都必須保留。

若 clean control 證實 fixture／harness defect，該 registration 立即失去 publication eligibility；
先修復、bump fixture version、重建／re-pin OCI，再建立新的 suite ID。不得用 adjudication 掩蓋。

## Replay 與 publication audit

有 completed outcomes 與必要 human evidence 時：

```powershell
uv run cae suite replay `
  --registration runs/reference/NEW_SUITE_ID/registration.json `
  --review-sets ledger/review-sets --out runs/reference/NEW_SUITE_ID
```

不論是否有 completed outcomes，release 都必須通過：

```powershell
uv run cae release audit --publication
uv run cae release audit --publication --online
```

Offline audit 驗證 registration、10-task coverage、status、trace contract、replay／review requirements、
claims、owner-only history、private-data exclusion 與 release manifest。Online audit 才額外執行 anonymous
GHCR manifest／config verification；它不使用 owner credentials，也不改寫 artifacts。

## Private／public boundary

可公開：registration、summary、status、sanitized trace、findings、public run summary，以及實際有
candidates 時的 dual-review manifests、formal ledgers 與 replayed results。

只能留在本機：API key、`.env`、`.run-store/` private raw events、Docker credentials、worksheet
keymaps、未經 sanitizer 分類的 provider／tool payload。GitHub Release 與 Zenodo 只能使用
`release-manifest.json` 明列的公開 artifacts。
