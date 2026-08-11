# Manual Run — 付費 provider execution 與 evidence flow

本文件說明如何執行 current-contract reference suite。驗證 repository／release 不需要 API key；只有
`cae suite run` 會呼叫 paid provider。任何 paid run 都應先產生 dry-run plan，確認 model、budgets、
OCI identity 與 aggregate maximum，再取得明確費用核准。

目前 committed reference suite 已於 2026-08-10 執行：

- Suite ID：`suite-ca6834e720ce87309847af909c342789286f7cffb943b03e9e140c73e040d80b`
- Provider／model：OpenAI `gpt-5.6-luna`
- API／reasoning：Responses API／`high`
- Tasks：2 clean controls＋8 mutations，固定順序，共 10 tasks
- Retry：`no_automatic_retry`
- Outcome：10 `budget_exhausted`；0 findings；0 provider／harness errors
- Estimated cost：USD 0.097166
- Wall-clock：500.316 s

不要直接重跑這個 registration：既有 outcome directories 不允許覆蓋，且重跑會造成額外費用，也會
破壞「每 task 一次 attempt」的解讀。新的研究問題或 configuration 必須建立新的 plan 與 suite ID。

## 1. 先完成不付費的 repository gates

```powershell
uv sync --locked
uv run cae validate fixtures
uv run cae fixture verify fixtures/fx-taskq-py
uv run cae fixture verify fixtures/fx-ledger-ts
uv run cae release audit --publication
uv run cae release audit --publication --online
```

最後一個 command 會匿名讀 GHCR，但不使用 Docker credentials；其餘 publication audit 是 offline。
若 clean control、witness、checksum 或 OCI identity 不一致，先停止，不得開始 provider calls。

## 2. Local secret setup

將 provider key 放在 repository 之外或 ignored `.env`；不要貼到 issue、terminal transcript、README、
trace、worksheet 或 shell history。Repository 使用 `CAE_PROVIDER_API_KEY` process variable，public
plan 只記錄 key 是否存在，不記錄 value。

`.env`、`.run-store/` 與 Docker credential files 必須維持 untracked。可先確認：

```powershell
git check-ignore .env .run-store
git status --short
```

## 3. Dry-run：產生不含 secret 的 exact plan

```powershell
uv run cae suite dry-run --env-file .env --tasks tasks --fixtures fixtures `
  --out PLAN.json
```

在核准前逐項核對：

- provider、model、API、reasoning effort；
- 10 個 ordered task IDs；
- per-task 與 suite-total token／tool-call／wall-clock／estimated-cost budgets；
- `no_automatic_retry`；
- 兩個 immutable `repository@sha256:...` refs、config digests 與 environment fingerprints；
- plan 中沒有 key、base URL secret、local path、mutable image tag 或 raw payload。

`dry-run` 不開 network connection，不產生 API 費用。

## 4. Register：第一個 API call 前凍結 identity

```powershell
uv run cae suite register --plan PLAN.json --tasks tasks --fixtures fixtures `
  --out runs/reference/NEW_SUITE_ID/registration.json
```

Registration 的 `suite_id` 由 canonical content 計算；`created_date` 不參與 identity。Register 會重新
驗證 task registry hash 與 fixture identity，拒絕覆蓋既有 registration。Model、order、budgets 或 OCI
identity 有任何 drift，都必須產生新 registration。Schema 1.1 也把 adapter name/version、rendered
system-prompt version/hash、manual conversation state 與 Responses `store: false` 納入 suite identity；
schema 1.0 registration 只能讀取
歷史 evidence，不能再執行。

## 5. Run：唯一會付費的步驟

```powershell
uv run cae suite run `
  --registration runs/reference/NEW_SUITE_ID/registration.json `
  --tasks tasks --fixtures fixtures --env-file .env `
  --out runs/reference/NEW_SUITE_ID
```

Runner 依 registration 順序執行，一個 task 最多一次 attempt，不做 retry／backoff。每個 task 都必須
留下 `status.json`，status 只可能是：

- `completed`
- `provider_error`
- `timeout`
- `budget_exhausted`
- `harness_error`
- `fixture_defect`

Failure 也是 evidence，不得刪除、重新命名或以後續較好 outcome 取代。Clean control 若揭露 fixture／
harness defect，整個 registration 不得成為 publication evidence；修復、bump fixture、重建／re-pin OCI
後再建立新 registration。

## 6. Cost 與 budget 解讀

`CAE_MAX_TOKENS` 是 observed usage boundary；`CAE_MAX_ESTIMATED_COST_USD` 依版本化 pricing table
估算。兩者都只能在 provider 回覆後檢查，因此可能超出一個 in-flight request。設定
`CAE_MAX_OUTPUT_TOKENS_PER_REQUEST` 會分別送出 Responses API 的 `max_output_tokens` 或 Chat
Completions 的 `max_completion_tokens`，限制單次 output overshoot；provider account spending limit
仍是不依賴本程式的最後 backstop。新 suite registration 會綁定這個 request limit，避免執行時漂移。

Current `gpt-5.6-luna` table（`openai-gpt-5.6-luna@2026-08-11-r2`）是每 1M tokens：

| Component | Rate |
|---|---:|
| Input | USD 0.20 |
| Cached input | USD 0.02 |
| Output（含 reasoning tokens，不能重複計價） | USD 1.20 |

所有報表使用 `estimated_cost_usd`，不使用 `cost_usd`。Pricing source、effective date、table version、
usage completeness 與 unknown fields 都隨 trace 保存。跨 providers 的 cache、reasoning 與 tier rules
不同，dollar values 不是天然可比。

歷史 reference suite 的 registered budgets 為每 task 200,000 tokens／60 tool calls／900 s／
USD 0.25，suite 上限 2,000,000 tokens／600 tool calls／9,000 s／USD 2.50。該 suite 的
`estimated_cost_usd = 0.097166` 是用 artifact 內封存的
`openai-gpt-5.6-luna@2026-08-06` pricing table 計算；不得以目前費率回算或覆寫。10 個 tasks
都是 token budget 先觸發。新 run 才使用上表的 2026-08-11 `r2` 費率；同日 revision
用 suffix 區分兩次觀測，不能拿新表回算或覆寫舊 artifact。

## 7. Private raw events 與 public trace

每個 run 先把完整 provider／tool events 寫入本機 `.run-store/`。Public trace 只能經 fail-closed、
atomic sanitizer 產生；unknown field 使整個 export 失敗。禁止手動複製 raw payload 到 `runs/`。

可公開的 current evidence：

- registration、summary、status；
- sanitized trace schema 0.2.0；
- public run summary 與 findings；
- 有 candidates 時的 public review-set manifests／formal ledgers／replayed results。

必須保持 private：API key、`.env`、`.run-store/`、raw provider／tool payload、Docker credentials、
worksheet keymaps 與 reviewer private identity。

Responses adapter 不使用 `previous_response_id`。每次 request 固定 `store: false`，由 client 完整重送
先前 `response.output`（包含 reasoning／function-call items），再以原始 `call_id` 附上
`function_call_output`。Chat Completions 同樣重送 assistant `tool_calls` message 與相連的 tool message。
完整 request／response 與 provider free-text error 只留在 `.run-store/`；public run directory 只保留
sanitized trace、request hash、結構化 failure classification、usage 與 findings。詳見
[OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling) 與
[conversation state](https://developers.openai.com/api/docs/guides/conversation-state)。

## 8. Human review 與 replay

`cae run`／`cae suite run` 不產生 `verified_*` scores。只有 completed task 實際產生 candidate pairs 時，
才建立 review set，依序完成 blinded primary、independent 與必要的 resolver workflow。AI 不得填寫
formal decisions 或 rationales。

本次 suite 的 findings 全空，因此 review-set 數量為 0 是正確 outcome；不得為滿足表面 coverage
而製造空白／synthetic rulings。

完成必要 human review 後，使用 committed public evidence replay：

```powershell
uv run cae suite replay `
  --registration runs/reference/NEW_SUITE_ID/registration.json `
  --review-sets ledger/review-sets --out runs/reference/NEW_SUITE_ID
uv run cae release audit --publication
```

Replay 拒絕 trace、fixture、candidate set、review set 或 content hash drift。Legacy single-review 與
synthetic ledger 永遠不能產生 `publishable: true`。

## Historical live attempts（非 reference result）

2026-08-06 的八次 `runs/live-*` harness-development diagnostics 已從 current release tree 移除並
保留於 Git history：其中包含兩次 zero-usage provider errors；
六次 billable runs 的 historical estimated cost 合計 USD 0.399115。三次使用
`chat_completions`／reasoning none，三次使用 Responses／reasoning high。

這些 traces 早於 current `run_header`＋aggregate `cost`＋trace 0.2 contract，也曾用來發現 fixture 與
harness defects。它們是診斷 provenance，不可與 2026-08-10 registered suite 合併計算、不可補寫成
current evidence，也不支持 model success-rate claim。
