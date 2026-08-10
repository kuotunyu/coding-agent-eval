# v0.1 Reference Suite 操作契約

Reference Suite 是固定的 10-task execution contract：兩個 clean controls，加上八個
single-bug mutated tasks。它的用途是產生一組可重播、可逐項追溯的 reference evidence，
不是 leaderboard，也不能代表模型在一般軟體開發工作的整體排名。

## 先決條件與付費 gate

執行前，兩個 fixture 都必須通過本機 schema、witness、rebuild 與 OCI identity 驗證；
registration 只接受 immutable `repository@sha256:...` identity，不接受 mutable tag。
`.env` 只留在本機且已由 `.gitignore` 排除。

`suite dry-run` 與 `suite register` 都不會呼叫 provider。只有 `suite run` 會產生真實、
可能付費的 API calls；operator 必須先檢查畫面顯示的 10-task aggregate maximum cost，並在
明確核准付費執行後才執行。Harness 的 retry policy 固定為
`no_automatic_retry`：每個 task 最多一次 provider attempt，不以重試美化成功率或延遲。

## 1. Dry-run：建立不含 secret 的 plan

```powershell
uv run cae suite dry-run --env-file .env --tasks tasks --fixtures fixtures `
  --out PLAN.json
```

這一步驗證 provider、model、API shape、reasoning effort、四種 per-task budgets、完整 10-task
registry、fixture version、environment fingerprint，以及 OCI manifest/config digests。輸出只
記錄公開設定，不包含 API key、base URL、environment-variable value、mutable image tag 或
local path。`suite_id` 由 canonical registration content 計算，排除 `created_date`。

## 2. Register：凍結 suite identity

```powershell
uv run cae suite register --plan PLAN.json --tasks tasks --fixtures fixtures `
  --out runs/reference/SUITE_ID/registration.json
```

Register 會重新比對 task registry 與 fixture identity，且拒絕覆蓋既有 registration。完成後，
task order、provider/model/config、budgets、image identities 與 environment fingerprints 都不得
再改；任何 drift 都要建立新的 registration 與 `suite_id`。

## 3. Run：保留全部 outcome

```powershell
uv run cae suite run `
  --registration runs/reference/SUITE_ID/registration.json `
  --tasks tasks --fixtures fixtures --env-file .env `
  --out runs/reference/SUITE_ID
```

Runner 依 registration 順序執行全部十個 task。每個 task 都會留下 `status.json`，status 只能是
`completed`、`provider_error`、`timeout`、`budget_exhausted`、`harness_error` 或
`fixture_defect`；失敗的 task 不得從 summary 消失。既有 task artifact directory 不會被靜默
覆蓋，因此 resume 也不能替換較差的 outcome。

Clean controls 必須先於同 fixture 的 mutated tasks。若 clean control 證實 fixture／harness
defect，該 registration 不得繼續成為 publication evidence；先修復 fixture、bump version、
重新驗證並建立新的 suite registration。不得靠修改 adjudication evidence 掩蓋 clean failure。

## 4. Replay：只使用公開 evidence

```powershell
uv run cae suite replay `
  --registration runs/reference/SUITE_ID/registration.json `
  --review-sets ledger/review-sets --out runs/reference/SUITE_ID
```

Replay 必須保留所有非 completed outcomes，並只對具有 current trace contract 與完整 dual
blinded human review 的 task 產生 publishable results。Legacy single-review 或 synthetic ledger
只能重播歷史／evaluator arithmetic，不能轉成 publication evidence。

## Private / public boundary

可公開：registration、sanitized trace、findings、task status、summary、dual-review manifests 與
formal human ledgers、replayed results。只能留在本機：API key、`.env`、`.run-store/` private raw
events、Docker credentials、worksheet keymaps，以及未經 sanitizer allowlist 的 provider/tool
payload。GitHub Release、OCI artifact 與 Zenodo deposit 只能取用 release manifest 明列的公開
檔案。

截至建立本契約時，reference suite 尚未執行，沒有新的 model success-rate、cost 或 latency
claim。任何數字都必須等固定 registration、十個 outcome、dual human adjudication 與 replay
audit 全部完成後，才可在 README 或 release metadata 中陳述。

