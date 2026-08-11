# Data Card — BugSeed v0.1

BugSeed 是 `coding-agent-eval` 的 first-party defect corpus。v0.1 包含兩個 bounded service fixtures、
八個 single-defect mutations 與兩個 clean controls，目的是提供可重播、可逐項追溯的 AI coding
agent defect-discovery evaluation vertical slice。

- **Dataset／benchmark version**：`0.1.0`
- **Authored-at cutoff**：`2026-08-05`
- **Language of documentation**：正體中文（`zh-TW`）；technical terms 保留原文
- **License**：MIT；每個 fixture tree 都含獨立 `LICENSE`
- **Creator**：`kuotunyu`
- **Reference suite**：`suite-ca6834e720ce87309847af909c342789286f7cffb943b03e9e140c73e040d80b`

## Dataset composition

`tasks/v0.1.json` 是 current versioned task registry，共 10 tasks：

- `fx-taskq-py`：1 clean control＋4 mutations；
- `fx-ledger-ts`：1 clean control＋4 mutations。

| 欄位 | `fx-taskq-py` | `fx-ledger-ts` |
|---|---|---|
| Fixture version | 1.0.5 | 1.0.3 |
| Language／runtime | Python 3.12 | TypeScript／Node 22 |
| Service shape | Background task queue、HTTP API、worker、SQLite | Double-entry ledger、HTTP API、locks、journal、batch settlement |
| In-scope paths | `src/**` | `src/**` |
| Out-of-scope paths | `tests/**` | `tests/**`、`node_modules/**`、`dist/**` |
| In-scope LOC | 1,456 | 1,183 |
| Own test suite | 220 tests | 174 tests |
| Seeded bugs | 4 | 4 |
| Runtime dependencies | 無 third-party runtime dependency | 無 third-party runtime dependency |
| License | MIT | MIT |

`in_scope_loc` 由 repository 內的 `cae-loc 0.1.0` 對 committed Git tree 計算；非空白且不是
whole-line comment 的行才計數。Gate 會從 `git archive HEAD` 重建 tree 並重算，避免 working-copy
build output 或 dependencies 汙染 denominator。

## Seeded bugs

| Bug | Category | Severity | Subcategory |
|---|---|---|---|
| `fx-taskq-py/B-001` | security | critical | authentication_bypass |
| `fx-taskq-py/B-002` | data_boundary | high | cross_tenant_identifier_exposure |
| `fx-taskq-py/B-003` | correctness | medium | pagination |
| `fx-taskq-py/B-004` | release_claim | low | documented_boundary_off_by_one |
| `fx-ledger-ts/B-001` | concurrency | high | lost_atomicity |
| `fx-ledger-ts/B-002` | correctness | medium | boundary_off_by_one |
| `fx-ledger-ts/B-003` | data_boundary | high | internal_detail_exposure |
| `fx-ledger-ts/B-004` | security | medium | timing_side_channel |

Category distribution 是 correctness 2、security 2、data_boundary 2、concurrency 1、
release_claim 1；severity distribution 是 critical 1、high 3、medium 3、low 1。
每類最多兩個樣本，不支持 category-level comparison。

## Creation 與 mutation screening

Fixtures、schemas、bugs、patches、witnesses 與 evaluator 都為本 benchmark 新作。Bugs 沒有從公開
issue tracker、CVE、歷史 fix commit 或既有 benchmark 搬運。

候選 mutation 逐一套用至 clean tree，並執行 fixture 自己的完整 test suite。只有測試仍全數通過的
survivors 可進 corpus：

- `fx-taskq-py`：23 candidates，13 被 tests 捕捉，10 survivors，選 4；
- `fx-ledger-ts`：18 candidates，12 被 tests 捕捉，6 survivors，選 4。

每個入選 bug 另有 machine-executable witness contract。Gate G2 驗證 clean pass、patch apply、
mutated behavior、patch revert、clean-after-revert pass。Mutation 若已被原始 tests 抓到，只能測量 agent
是否執行 tests，不能測量 code-reading discovery，因此不納入。

## Clean controls

每個 fixture 都保留同版本的無 seeded-defect snapshot。`fixtures/<id>/defects.md` 記錄兩輪人工
clean-tree audit 與後續發現；`known_residual_defects.yaml` 必須為空，否則 fixture 不具 release
eligibility。

Cleanliness 不是永久保證。早期 audit 與 live run 曾找出真實 fixture defects，修正後皆 bump version、
重建 OCI 並重新 pin。Attempt 4 又在 TaskQ 1.0.5 clean tree 產生兩個經離線重現的工程缺陷，
因此 1.0.5 已失去 release eligibility；Ledger 1.0.3 不受此 observation 影響。TaskQ 必須修復、
bump version、重建／re-pin OCI 後才可重建 clean evidence，不得以 adjudication 把缺陷標成
unsupported。

## OCI distribution

兩個 execution environments 以公開 GHCR versioned tags 分發，但可比較 identity 只使用 digest：

| Fixture | Repository／tag | Manifest digest | Config digest |
|---|---|---|---|
| `fx-taskq-py` | `ghcr.io/kuotunyu/coding-agent-eval-fx-taskq-py:1.0.5` | `sha256:db6a0afabe3acfd9c704e020b27a5b55ccef430b4864d8e565711b0b9cbc8966` | `sha256:7d2299a679f1a00d4e45d0901876e4894c2e5f4c5916bf97dbcfe1210277c09a` |
| `fx-ledger-ts` | `ghcr.io/kuotunyu/coding-agent-eval-fx-ledger-ts:1.0.3` | `sha256:38450742408270a0e48ae053499dd626f61a4cf09139d40ae494838def4b0312` | `sha256:c7d310f6a41a47132484bddc47969547c9e34cb7628456415696c59af223d583` |

Image index／manifest 與 local image config 是不同 OCI objects，必須分開驗證。Current environment
fingerprint 包含 base image digest、prepared manifest digest、prepared config digest、OS id／version、
runtime、package manager、lock manifest hash 與 architecture；不包含 mutable tag、hostname 或 timestamp。

## Contamination

新作 mutations 相較公開歷史 fixes 降低已知 contamination 風險，但不能證明任何 model 的 training
corpus 未包含它們。因此本 dataset 稱為 **contamination-resistant**，不稱 contamination-free。

- Cutoff 為 `2026-08-05`。
- 公開後，code、bugs 與 patches 可能進入未來 training corpus，保護力會隨時間下降。
- 使用者引用結果時應同時報 benchmark version、suite registration date、provider／model configuration
  與 exposure context。

## Target leakage controls

Public release 為可重現性必須包含 canonical bugs、patches 與 witnesses；因此 repository 層級無法對
已下載完整 dataset 的 agent 保密答案。Evaluation contract 的控制點在 runtime boundary：

- Agent measure container 只接收選定 source snapshot；
- `bugs/`、`patches/`、witnesses、task metadata、ledger、host repository 與 `.run-store/` 不會 mount
  進 measure container；
- Tool surface 只有 read/list/search/submit，不提供 host shell 或 network；
- Public trace 不含 raw provider payload、private tool output 或 secret；
- Operator 若把完整 repository 或 ground truth 放入 prompt，該 run 即不符合本 benchmark contract。

這些措施降低 accidental target leakage，不能防止已記住公開 benchmark 的 model。未來要做較強的
model comparison，需建立 private／rotating holdout corpus，而不是只擴寫 public v0.1。

## Reference evidence 與 annotation

2026-08-10 reference suite 使用 OpenAI `gpt-5.6-luna`、Responses API、high reasoning，按固定順序
執行 10 tasks 且 `no_automatic_retry`。所有 outcomes 都保留為 `budget_exhausted`，共提交 0 findings。
該 suite 綁定的是 TaskQ 1.0.4；`runs/reference/task-registry.json` 保存 registration hash 所指的
exact registry bytes。Current `tasks/v0.1.json` 與 TaskQ 1.0.5 不會回填到這份歷史 evidence。

因此本次沒有 candidate pairs，也沒有 human rulings。這不是缺少應做的 annotation：review domain 是
空集合；它也不能被描述為 independent adjudication evidence。未來任何產生 candidate 的 completed
run 都必須進入 dual blinded review，否則 publication audit fail closed。

Legacy `ledger/adjudications.jsonl` 是 append-only single-review historical evidence；synthetic ledger
只供 evaluator tests。兩者均固定不可成為 current publication result。

## Public／private boundary 與 retention

| Artifact | Location | Release／retention |
|---|---|---|
| Fixtures、bugs、patches、witnesses | Git／release artifacts | Permanent |
| Current task registry；reference registry snapshot、registration、status、sanitized trace、findings | Git／release artifacts | Permanent |
| Formal review sets（若有 candidates） | `ledger/review-sets/` | Public、append-only evidence |
| Release manifest | `release-manifest.json` | Public；records bytes＋SHA-256 |
| Raw provider／tool events | `.run-store/` | Local only；預設 30 days，可由 `cae store prune` 清除 |
| API key、`.env`、Docker credentials、worksheet keymaps | Local only | 永不進 Git／release |

Sanitizer 使用 closed allowlist：每個欄位必須被分類為 public 或 known-private；unknown field 使輸出
整體失敗且不留下 partial public artifact。

## Known limitations

- Corpus 小、first-party、dependency-light，external validity 有限。
- Fixture author 知道 ground truth；blinding 不能消除 authorship bias。
- Public bugs 使 contamination 與 memorization risk 隨時間增加。
- 本次只有一個 provider／model／configuration，沒有 random-seed replication。
- Reference suite 沒有 completed task 或 finding；不能提供 human-verified effectiveness estimate。
- Cost 是依版本化 pricing table 計算的 estimate，不是 invoice。
- OCI reproducibility 依賴 GHCR、Docker／OCI implementation 與 pinned upstream base images。

## Maintenance 與版本規則

修復 clean fixture、改變 source bytes、bug／witness、runtime dependencies、OCI identity 或 task registry
都必須 bump 對應 version／checksum，重新驗證並建立新的 suite registration。既有 registration、trace、
result 與 ledger 不得原地改寫或回填。
