# coding-agent-eval

> 可重現的 AI coding agent benchmark，用來測量 agent 能否發現已知缺陷、產生多少
> `unsupported findings`，以及在可重現 `sandbox` 條件下消耗多少資源。

這套 benchmark dataset 與 fixture suite 稱為 **BugSeed**。

- **主要語言**：正體中文（`zh-TW`）；專有名詞保留原文
- **Benchmark version**：`0.1.0`
- **目前狀態**：v0.1 methodology preview；不是正式 benchmark release，也不是 leaderboard

## 評估什麼

核心問題是：agent 能否在事前不知道缺陷位置的情況下，從已知 ground truth 的 source tree
找出刻意植入的 defect。

以下四個問題分開衡量，因為它們彼此有 trade-off。Agent 可以藉由回報更多 findings 提高
recall，因此 noise 與 cost 必須同時呈現。

| 問題 | 回答方式 |
|---|---|
| Agent 是否找到 seeded defect？ | deterministic matcher 依 file 與 line range 計算 `localization_recall`。 |
| Finding 是否真的正確？ | 由 human adjudication 產生 `verified_*` metrics；其他程序不得產生這些數字。 |
| Agent 產生多少 noise？ | 以 clean control 上的 `unsupported findings`，除以每千行 in-scope LOC。 |
| 執行成本是多少？ | 同時保留 tokens、tool calls、wall-clock latency 與 estimated USD cost。 |

## 不評估什麼

- **不評估 agent 是否能修復 defect。** 本 benchmark 在 discovery 階段停止。
- **不使用另一個 model 取代 human adjudication。** `model-as-judge` 最多只能作為 sensitivity
  analysis，不能進入 primary metrics。
- **不是 agent framework 展示。** Reference runtime 是 measurement instrument，不是產品比較標的。

## v0.1 的定位與 evidence

v0.1 是一個 **methodology vertical slice**。它證明 measurement contract 可以運作，但不足以
支持 model ranking。兩個 fixtures 與八個 seeded bugs 無法可靠區分不同 agent，也不得用來建立
ranking、league table 或 category-level capability claim。

目前保存八次 `gpt-5.6-luna` live attempts：兩次為 zero-usage provider errors，另六次為
billable runs，記錄的 estimated cost 合計 `$0.399115`。其中三次透過
`/v1/chat/completions` 關閉 reasoning，三次透過 `/v1/responses` 使用 `reasoning=high`；後三次
reasoning tokens 分別占 output tokens 的 `86.95%`、`85.58%`、`91.55%`。

在有提交 matching finding 的 mutated-snapshot runs 中，`localization_recall` 都是 `1.0`；對應
clean control 沒有回報同一個 file。這只是同一個 model、同一個 fixture、同一個 bug 的重複觀察，
不是 capability claim。

這八份 traces 是明確標示的 **legacy evidence**。它們早於目前 Gate A raw-event contract，缺少
strict replay 所需的 `run_header` 與 aggregate `cost` events。新的 live run 會先寫入 private raw
store，再經過 fail-closed sanitizer 產生 public artifact；歷史檔案不會被回填或偽裝成可重播 evidence。

Live runs 也揭露過一個 harness defect：沒有 past-turn memory 的 agent，在 51-step run 內以七個
不同 ID 重複提交同一位置。修正後，`write_findings` 會提示 overlapping location；下一次相同設定的
live verification 將單一位置的重複提交上限從七次降為兩次。完整限制記錄在
[BENCHMARK_CARD.md](docs/BENCHMARK_CARD.md)。

Repository 中所有 end-to-end baseline numbers 都是 deterministic scripted baseline 搭配
synthetic adjudication ledger，用來驗證 evaluator arithmetic 與 data flow。它們不描述任何 model，
並強制帶有 `decision_source: synthetic`、`publication_reason: synthetic_adjudication` 與
`publishable: false`。

`verified_*` metrics 只接受完整、evidence-bound 的 dual human review。既有 Formal ledger 只有
`fx-taskq-py/B-001` 的兩筆 owner rulings，會固定標示為 `legacy_formal` 與
`single_adjudicator_legacy`，不得成為 publishable result。AI 不得撰寫 formal adjudication；synthetic
rulings 必須使用 `SYNTHETIC-` 前綴，並強制標示 `synthetic_adjudication` 與
`publishable: false`。

## Fixtures

兩個 fixtures 都是 first-party、MIT licensed，並在植入 bug 前完成 clean-tree audit。

| Fixture | Language | In-scope LOC | Own tests | Bugs | Categories |
|---|---:|---:|---:|---:|---|
| `fx-taskq-py` 1.0.3 | Python 3.12 | 1,367 | 205 | 4 | security, data_boundary, correctness, release_claim |
| `fx-ledger-ts` 1.0.2 | TypeScript / Node 22 | 1,183 | 174 | 4 | concurrency, correctness, data_boundary, security |

兩者合計涵蓋全部五種 defect categories。

每個 seeded bug 都必須通過 fixture 自己的 test suite。若 test suite 原本就能抓到 mutation，測到的只會是
agent 是否執行 `pytest` 或 `npm test`，而不是 code-reading 能力。因此候選 mutation 會逐一套用、執行
完整 suite，只有 survivors 能進入 corpus。

每個 fixture 也包含沒有 seeded defect 的 **clean control**。Clean control 上的 findings 會計為
unsupported，因此每個 fixture 都附帶 audited `defects.md`。兩輪人工 audit 修正了四個 clean-tree
defects；後續 live agent 又找到第五個並促成 `fx-taskq-py` 1.0.3。這同時說明 clean-control
completeness 不是永久保證。

## Contamination 與 target leakage

Seeded bugs 是 benchmark 建立時私下撰寫的新 mutations，相較於公開歷史修復，較不容易已存在於
training data；但這只能稱為 **contamination-resistant**，不能稱為 contamination-free。

- **Benchmark version**：`0.1.0`
- **Cutoff date**：所有 fixtures 與 bugs 都記錄 `authored_at: 2026-08-05`
- **Exposure decay**：公開之後，這些 bugs 也可能進入未來 training data；contamination resistance
  會隨時間與曝光下降
- **Target leakage**：task schema、patch、witness 與 private raw evidence 的權限邊界仍必須被遵守

## Provenance

Reference agent runtime 源自內部 prototype；fixtures、schemas、evaluator 與 trace pipeline 都是為本
benchmark 撰寫。公開 release lineage 使用全新的 owner-only initial commit，不匯入帶有額外 contributor
attribution 的開發歷史；原始開發 checkout 保留在本機，沒有被 reset、刪除或改寫。

## v0.1 acceptance contract

Mechanical acceptance criteria 已明確定義並由機器檢查：

- task registry 必須恰好包含兩個 clean controls 與八個 mutated tasks；
- 每個 clean suite 與 seeded-bug witness 都必須在宣告的 container 中執行；
- 目前 runner 產生的 traces 必須先進 private raw store，再經 fail-closed sanitizer，並能 strict replay；
- committed results 必須符合 current result schema 與 fixture version；
- release audit 必須檢查 metadata、links、checksums、ledger integrity，以及 synthetic baseline 的
  non-publishability；
- CI 必須執行 non-Docker checks 與完整 Docker witness matrix。

通過這些 gates 只代表 v0.1 是可重現的 **methodology preview**。正式 publication 仍需要：

1. 完整且由第二位 independent human 完成的 blinded adjudication；
2. 可由 independent evaluator 取得、以 digest 固定的 OCI artifacts；
3. owner 對 GitHub Release 與 Zenodo publication 的最終核准。

完整 acceptance/evidence matrix 與 Zenodo no-go decision 請見
[RELEASE_READINESS.md](docs/RELEASE_READINESS.md)。

### Gate vocabulary

| Gate | Evidence contract |
|---|---|
| G1 schema validation | fixture、bug、task、trace、ledger、finding 與 result schema tests |
| G2 witness contract | clean suite 加上全部八個 focused witnesses；container 不使用 bind mount |
| G3 rebuild determinism | 從 Git 重建兩個 trees，驗證 checksum 與 LOC |
| G4 answer leak | 掃描全部八個 mutated trees |
| G5 sanitizer fail-closed | unknown/private-field rejection 與 atomic-write tests |
| G6 replay determinism | sequence、singleton events、usage sum 與 aggregate cost tests |
| G7 matcher correctness | deterministic matching 與 assignment tests |
| G8 evaluator fail-closed | incomplete/mismatched ledger 與 version tests |
| G9 deterministic baseline E2E | 兩個 fixtures、clean 與 mutated snapshots（[runs/](runs/)） |
| G10 lint / type / unit | Ruff、format、strict mypy 與 non-Docker pytest |
| G11 tracked-file leak scan | repository leak scanner |

G3 評估的是 **committed tree**：它以 `git archive` 從 `HEAD` 重建每個 fixture tree，再驗證 checksum、
LOC 與 working copy。Fixture tree 被修改但尚未 commit 時，G3 預期會 fail；這表示 environment identity
尚未被記錄，不是 gate malfunction。

## Sandbox isolation

Agent 只能使用四個 tools：read file、list directory、search tree、submit findings。Tool surface 可由
兩種 backend 提供相同 bytes：

| Backend | `tool_backend` | 與 host 之間的隔離 |
|---|---|---|
| Measure container | `measure_container:<prepared_image_digest>` | Kernel boundary；`--network none`、`--read-only`、`--cap-drop ALL`，沒有 host mount，也沒有 host path。 |
| Host process | `host_process` | 只有 tool surface 自己的 path checks，隔離強度較低。 |

兩個 backend 在真實 fixture trees 上的 tool output 必須 byte-identical，E2E scores 也必須逐項一致。
每份 result 都會記錄 `tool_backend`；committed scripted baselines 使用 `host_process`。

Sandbox containment 是 observed implementation property，不是 security certification。平台、驗證範圍與
限制記錄於 [SANDBOX_VERIFICATION.md](docs/SANDBOX_VERIFICATION.md)。

## 執行驗證

以下 gates 不會呼叫 provider API，也不需要 API key。快速腳本只執行 non-Docker checks：

```bash
bash scripts/check.sh
```

驗證 manifests、task registry 與 schema：

```bash
uv run cae validate
```

重建 committed fixture trees 並驗證 checksum／LOC：

```bash
uv run cae fixture rebuild fixtures
```

執行完整 clean/witness cycles：

```bash
uv run cae fixture verify fixtures/fx-taskq-py
uv run cae fixture verify fixtures/fx-ledger-ts
```

執行 Docker regression tests：

```bash
uv run pytest -q -m docker
```

若本機具有 manifest 指定的 exact prepared images，可重新推導 environment identity：

```bash
uv run cae fixture environment fixtures
```

目前保存的原始 prepared images 並未發布；以 Dockerfile 重建出的 candidate images 會有不同 image ID，
因此在 OCI artifacts 正式 re-pin 並分發之前，這個 environment command 預期會報告
`prepared_image_digest` mismatch。不得把 candidate image 的其他通過項目解讀成 exact-image gate 通過。

請直接執行 `scripts/check.sh`，不要把它 pipe 到其他 command；腳本使用 `set -euo pipefail`。Git-dependent
checks 應從 native checkout 執行。WSL 的 `/mnt/c` 可能重寫 file modes，導致 tree checksum 合理地失敗。

## Repository layout

```text
fixtures/  兩個 fixtures：clean tree、seeded bugs、patches、witnesses、audits
schemas/   manifests 使用的 JSON Schemas
tasks/     v0.1 task registry：兩個 clean controls 與八個 mutations
src/       validation、fixtures、evaluator、trace、sandbox 與 agent runtime
ledger/    append-only formal human adjudication ledger
runs/      synthetic baselines 與明確標示的 legacy live evidence
docs/      design、implementation、data/benchmark cards 與 threat model
```

## 文件索引

| 文件 | 回答的問題 |
|---|---|
| [DATA_CARD.md](docs/DATA_CARD.md) | Dataset 包含什麼、由誰建立、license 與適用限制 |
| [BENCHMARK_CARD.md](docs/BENCHMARK_CARD.md) | Metrics、denominators、zero-denominator behavior 與限制 |
| [METRICS.md](docs/METRICS.md) | Formulas、matcher 與 assignment rules |
| [THREAT_MODEL.md](docs/THREAT_MODEL.md) | Trust boundaries、sandbox phases 與 residual risks |
| [SANDBOX_VERIFICATION.md](docs/SANDBOX_VERIFICATION.md) | Docker 上實際觀察到的 isolation behavior |
| [runs/README.md](runs/README.md) | Committed baselines 為何不描述任何 model |
| [MANUAL_RUN.md](docs/MANUAL_RUN.md) | Live-run evidence flow 與 historical replay limitation |
| [RELEASE_READINESS.md](docs/RELEASE_READINESS.md) | v0.1 acceptance criteria、evidence map 與 publication blockers |

## Licence

MIT，詳見 [LICENSE](LICENSE)。兩個 fixtures 也都是 MIT licensed，並各自包含 `LICENSE`。

## English summary

`coding-agent-eval` is a reproducible ground-truth benchmark for measuring whether coding
agents discover deliberately seeded defects, how many unsupported findings they produce,
and what resources they consume under controlled sandbox conditions. BugSeed v0.1 contains
two first-party fixtures and eight mutations. It is a methodology preview—not a model-ranking
benchmark or leaderboard—and currently has no publishable model result because independent
human adjudication and distributable digest-pinned OCI artifacts are still required.
