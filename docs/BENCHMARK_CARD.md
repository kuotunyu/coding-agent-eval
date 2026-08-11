# Benchmark Card — coding-agent-eval / BugSeed v0.1

> 以已知 ground truth 的 seeded defects，評估 AI coding agent 的 defect-discovery
> behavior、unsupported findings、cost、latency 與 failure modes。

- **Benchmark version**：`0.1.0`
- **Dataset**：BugSeed；見 [DATA_CARD.md](DATA_CARD.md)
- **Reference suite**：10 tasks（2 clean controls＋8 mutations）
- **Suite ID**：`suite-ca6834e720ce87309847af909c342789286f7cffb943b03e9e140c73e040d80b`
- **Configuration**：OpenAI `gpt-5.6-luna`、Responses API、`reasoning_effort=high`
- **Status**：可重現的單一 reference execution；不是 leaderboard 或 model comparison

Metric formulas、matcher 與 assignment rules 的規範版本在 [METRICS.md](METRICS.md)。

## Intended use

本 benchmark 測量 agent 是否能在沒有 defect location 或 canonical answer 的情況下，從 bounded
service repository 找出 seeded defect。它在 discovery 階段停止，不評估 patch generation／repair。

適合：

- 驗證 evaluation harness、sandbox、trace、replay 與 human-review workflow；
- 對一個明確的 agent configuration 描述 detection／noise／resource behavior；
- 保留 timeout、budget exhaustion 與 provider／harness error，分析失敗模式。

不適合：

- 排名或比較 models／agents；
- 推論 production repositories 的真實 false-positive rate；
- 由五個薄樣本 categories 推論 category capability；
- 把 sandbox tests 稱為 security certification；
- 把 deterministic baseline 或 legacy traces 當成 empirical model result。

## Evaluation pipeline

### Stage A — deterministic matcher

Finding 只有在 file 一致、line range 與 bug localization window（含 tolerance）重疊，且 category
一致時，才成為 candidate pair。這只證明定位相容，不證明 root cause 正確。

### Stage B — dual blinded human adjudication

只有 candidate pairs 需要 adjudication。Review worksheet 隱藏 provider、model、adapter、budget、cost、
tokens、latency、run id、trace、其他 reviewer decision 與公開 identifiers。`same_root_cause` 同時要求：

1. Claim 描述同一 defect；
2. Root cause 是同一 mechanism；
3. Evidence 受所示 code 支持。

Publication result 需要 primary 與 independent reviewer 對每個 pair 完整覆蓋；disagreement 交由與前兩位
不同的 independent resolver。AI 不得撰寫 formal adjudication。Synthetic rulings 永遠
`publishable: false`。

本次 reference suite 的 10 個 `findings.json` 都是空集合，因此 candidate pairs＝0；human-review
coverage 對此 execution 為空集合上的完備條件，不代表有人審閱了十個 tasks，也不支持
human-verified performance claim。

## Metrics

令 `B` 為該 snapshot 的 seeded bug set、`F` 為 exact-duplicate collapse 後的 findings、`V` 為經
human review 確認且完成 one-to-one assignment 的 bugs、`M` 為成功驗證 bug 的 findings。

| Metric | 定義 | Denominator／邊界 |
|---|---|---|
| `localization_recall` | 至少一個 Stage A candidate 的 bugs ÷ `|B|` | 只適用 mutated snapshot；不是 correctness recall。 |
| `verified_bug_recall` | `|V| / |B|` | 需要 publication-eligible human review。 |
| `verified_finding_precision` | `|M| / |F|` | Out-of-scope findings 仍進 denominator。 |
| `unsupported_findings` | `|F \ M|` | Count；clean 與 mutated 都可報。 |
| `benchmark_unsupported_findings_per_kloc` | unsupported findings ÷ in-scope KLOC | Headline noise source 是 clean control；不是 real-world FPR。 |
| `cost_per_verified_bug` | `estimated_cost_usd / |V|` | Cost 是 estimate；跨 provider 不可直接等同。 |
| `tokens_per_verified_bug` | `(input_tokens + output_tokens) / |V|` | Cached／reasoning tokens 另外報。 |
| `out_of_scope_findings` | 落在 out-of-scope paths 的 findings | 另外報告，但不從 precision denominator 移除。 |

### Zero-denominator behavior

| 條件 | 輸出 |
|---|---|
| `|V| = 0` | Cost／tokens per verified bug 為 `null`，reason `no_verified_bugs`。 |
| `|F| = 0` | Verified precision 為 `null`，reason `no_findings`。 |
| `|B| = 0` | Clean control 的 recall 為 `null`，reason `no_bugs_in_snapshot`。 |

不得把 `null` 改寫為 0、Infinity 或省略。只有 exact duplicates 會 collapse；fuzzy deduplication 不在
primary scoring path，避免啟發式合併只會單向提高 precision。

## Registered reference result

Registration、其 hash 綁定的 `task-registry.json` 與完整 evidence 位於 `runs/reference/`。
這份 frozen registry 描述 TaskQ 1.0.4；current `tasks/v0.1.json` 描述 1.0.6，publication audit
分開驗證兩者，不將舊結果重新解讀為新 fixture evidence。

| 面向 | 觀察值 | Evidence |
|---|---:|---|
| Outcome retention | 10/10 | `summary.json.task_count` 與 10 個 `status.json` |
| Completed | 0/10 | `summary.json.counts` 沒有 `completed` |
| Budget exhausted | 10/10 | `summary.json.counts.budget_exhausted` |
| Termination | 10/10 `budget_exhausted_tokens` | 各 `run.json.termination_reason` |
| Findings／candidates | 0／0 | 各 `findings.json`、`run.json.findings_submitted` |
| Provider／harness errors | 0／0 | Status taxonomy 與 termination payload |
| Estimated cost | USD 0.097166 total | 各 `run.json.usage.estimated_cost_usd` |
| Per-task estimated cost | USD 0.008738–0.012885 | 同上 |
| Wall-clock latency | 500.316 s total；50.032 s mean | 各 `run.json.wall_clock_ms` |
| Per-task latency | 40.566–65.886 s | 同上 |
| Tool calls | 212 total | 各 `run.json.tool_calls` |

Cost 使用 `openai-gpt-5.6-luna@2026-08-06` pricing table：input USD 0.20、cached input
USD 0.02、output USD 1.20 per 1M tokens。10 個 usage payload 全部標示 `complete`；金額仍稱
`estimated_cost_usd`，不能冒充 provider invoice。

### 結果如何解讀

- Evidence pipeline 完整保留 10 個 terminal outcomes；這是 reproducibility 結果。
- Agent 沒有在任何 task 結束前提交 finding。若只描述 submitted candidate coverage，mutated tasks
  為 0/8；正式 `verified_*` results 未產生。
- 10/10 budget exhaustion 顯示這個 configuration 在固定 200,000-token per-task budget 下未完成；
  不能把它解讀成 corpus 的 defect 難度分布或 `gpt-5.6-luna` 的一般能力。
- 兩個 clean controls 也 budget-exhausted，所以 0 unsupported findings 不是完整 clean-review 的成功證據。
- 沒有 verified bug，`cost_per_verified_bug` 必須是 `null`，不是 USD 0。

## Failure taxonomy 與 timeout

Reference runner 保留以下 terminal statuses：`completed`、`provider_error`、`timeout`、
`budget_exhausted`、`harness_error`、`fixture_defect`。本次只觀察到 `budget_exhausted`，原因全部是
`budget_exhausted_tokens`；其他類別有 deterministic tests，但沒有本次 empirical frequency。

每 task 預先固定四個 budgets：200,000 tokens、60 tool calls、900 wall-clock seconds、USD 0.25
estimated cost。Suite aggregate maximum 是 2,000,000 tokens、600 tool calls、9,000 seconds、
USD 2.50。Retry policy 是 `no_automatic_retry`，較差 outcome 不會被重跑取代。

## Reproducibility contract

Current trace schema 是 0.2.0。每份 reference trace 都要求：

- contiguous `seq`；
- 唯一 `run_header`、`cost`、`termination`；
- registration-bound task、model、budget、environment fingerprint 與 immutable OCI identity；
- 新 registration 另綁定 adapter name/version、rendered system-prompt version/hash、manual
  conversation state 與 Responses `store: false`；
- 每次 LLM call 都有非負 `latency_ms`，usage 加總與 aggregate cost 一致；
- tool backend 為 `measure_container:<manifest_digest>`。

Offline publication audit 只讀 committed evidence，不呼叫 network：

```bash
uv run cae release audit --publication
```

Online 模式另以空 Docker credential context 驗證 anonymous digest-qualified pull、manifest digest 與
local config digest：

```bash
uv run cae release audit --publication --online
```

Trace 0.1.0 的 historical evidence 仍可讀，但固定不可發布。Replay 遇到 sequence、singleton event、
usage、cost、fixture、candidate、review 或 hash drift 時 fail closed。

2026-08-10 reference suite 的 trace 雖是 schema 0.2.0，agent adapter 是
`openai-responses@0.1.0`；conversation continuation 當時不符合目前官方 function-calling contract。
它保留 terminal outcomes 與 cost/failure provenance，但不是 adapter 0.3／0.4、prompt 0.2 的有效性證據。
兩個 adapter 0.2 clean smoke attempts 也因 token/tool budget exhaustion 而未通過。新的 paid
execution 必須先通過 smoke gate，再以 registration schema 1.1 建立不同 suite ID。

Adapter 0.3／prompt 0.2 attempt 3 正常完成並揭露 TaskQ 1.0.4 的 lease ownership defect。
Attempt 4 接著在 TaskQ 1.0.5 正常完成並提交兩個 clean-control findings；deterministic offline
reproducers 證實 concurrent idempotency duplication 與 non-resumable schema-migration window。
兩次 gate 都失敗，沒有執行 mutated task，且 1.0.5 不是 clean release fixture。這些是
AI-assisted engineering assessments，不是 formal human adjudication，也不產生 verified detection。

Attempt 5 在 TaskQ 1.0.6 使用 adapter 0.3／prompt 0.2，保留 12 次 provider calls、12 次 tool
results 與 USD 0.005426 cost evidence，但在 final provider turn 前達到 token budget，因此仍未通過
clean gate，也沒有執行 mutated task。事後修正的 adapter 0.4 明確區分「有 final assistant message
且零 findings」與「缺少／malformed final output」；目前只有 deterministic mocked evidence，不能回填
或重新標記 attempts 1--5。

## Sandbox boundary

Agent 的 measure tool surface 只有 read file、list directory、search tree、submit findings。
Measure container 使用 read-only filesystem、`--network none`、`--cap-drop ALL` 且無 host bind mount。
Host-process backend 仍供 deterministic baselines 使用，但 isolation 較弱，且不能成為 current reference
publication evidence。

Observed isolation tests 驗證 filesystem、network、process、resource limit、timeout、input validation 與
workspace non-mutation；它們不構成 formal security proof。詳見 [SANDBOX_VERIFICATION.md](SANDBOX_VERIFICATION.md)
與 [THREAT_MODEL.md](THREAT_MODEL.md)。

## Limitations

1. **Corpus scale**：2 fixtures、8 mutations；每 category 1–2 samples，不能比較 models。
2. **Single registered configuration**：只有 OpenAI `gpt-5.6-luna`／Responses／high；沒有 replication
   seeds、cross-provider 或 cross-model evidence。
3. **No completed reference task**：所有 tasks token-budget-exhausted，沒有 publishable `verified_*` result。
4. **No candidates to adjudicate**：dual-review protocol 已實作並 fail closed，但本次沒有實際 rulings；
   不能把 protocol availability 說成 reviewer agreement evidence。
5. **Fixture authorship**：first-party bounded services 提供乾淨 ground truth，也限制 external validity。
6. **Contamination／target leakage**：新 mutations 降低已知污染風險，但公開後會衰減；maintainer 知道答案。
7. **Cost model**：`estimated_cost_usd` 依版本化 pricing table，可能與後續價格或 invoice 不同。
8. **Sandbox**：是 observed behavior，不是 security certification；Docker／kernel／registry 都在 trust boundary。
9. **Historical evidence**：早期 schema-0.1 live diagnostics 已從 release tree 移除但仍可由 Git
   history 追溯；它們不能與 current suite 合併統計。

## Evidence traceability

重要 public claims、精確欄位與重算方式列於
[RELEASE_READINESS.md](RELEASE_READINESS.md#claim-to-evidence-matrix2026-08-11)。Release artifacts 的
bytes 與 SHA-256 列於根目錄 `release-manifest.json`。
