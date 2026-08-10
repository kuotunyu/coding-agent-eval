# coding-agent-eval

> 可重現的 AI coding agent defect-discovery benchmark：用已知 ground truth 的
> seeded defects，測量 agent 的 detection coverage、unsupported findings、cost、
> latency 與 failure mode。

Benchmark dataset 與 fixture suite 名為 **BugSeed**。主要語言為正體中文（`zh-TW`），
技術專有名詞保留原文。

- **Benchmark version**：`0.1.0`
- **Release 狀態**：evidence-backed release candidate；尚未建立 GitHub Release 或 Zenodo record
- **Reference suite**：10 tasks（2 clean controls＋8 single-mutation tasks）
- **Suite ID**：`suite-ca6834e720ce87309847af909c342789286f7cffb943b03e9e140c73e040d80b`
- **Agent configuration**：OpenAI `gpt-5.6-luna`、Responses API、`reasoning_effort=high`
- **Claim boundary**：一個已註冊的 configuration；不是 leaderboard、model ranking 或一般能力估計

## v0.1 現況

v0.1 已具備可稽核的 corpus、immutable OCI identity、trace schema 0.2.0、fail-closed
sanitization／replay、pre-registration、retained failures、publication audit 與 owner-only
release provenance。它適合發布為小型、可重現的 benchmark software/dataset release；
它不具備足以比較 models 的 corpus 規模，也沒有成功完成的 reference task 可支持
human-verified performance claim。

### 2026-08-10 reference suite 結果

| 項目 | 觀察值 |
|---|---:|
| Registered tasks | 10/10 retained terminal outcomes |
| `completed` | 0/10 |
| `budget_exhausted` | 10/10 |
| Termination reason | 10/10 `budget_exhausted_tokens` |
| Provider／harness errors | 0／0 |
| Submitted findings | 0 |
| Mutated-task candidate coverage | 0/8 |
| Human rulings required | 0（candidate set 為空） |
| Estimated cost | USD 0.097166 total；USD 0.008738–0.012885/task |
| Wall-clock latency | 500.316 s total；50.032 s mean；40.566–65.886 s range |

這些數字只描述該次已註冊 execution。`10/10 retained` 是 evidence coverage，
不是 task success。所有 tasks 都在 200,000-token budget 後停止；因此 clean controls 的
0 findings 也不能解讀為 agent 已成功完成 clean review。沒有 finding 就沒有 candidate pair，
所以本次不需要、也沒有虛構 human adjudication；`verified_*` metrics 未產生，
`verified_finding_precision` 與 `cost_per_verified_bug` 都不應被報成 0。

可重算來源：[`runs/reference/registration.json`](runs/reference/registration.json)、
[`runs/reference/summary.json`](runs/reference/summary.json) 與
[`runs/reference/tasks/`](runs/reference/tasks/)。完整 claim 對照在
[`docs/RELEASE_READINESS.md`](docs/RELEASE_READINESS.md)。

## Benchmark 評估什麼

核心問題是：agent 在不知道 defect 位置與答案的情況下，能否從 source tree 找出刻意植入的
defect。Discovery 與 verification 分成兩階段：

1. **Deterministic matching**：依 file、line range 與 category 建立 finding／bug candidate pair。
2. **Blinded human adjudication**：只有 candidate pairs 進入 formal review；完整 publication result
   要求 primary 與 independent reviewer 一致，disagreement 則由第三位 resolver 處理。

主要量測面向：

| 問題 | Metric／evidence |
|---|---|
| 是否定位 seeded defect？ | `localization_recall`；只代表 matcher candidate coverage。 |
| Finding 是否與 ground truth 同一 root cause？ | `verified_bug_recall`、`verified_finding_precision`；只能來自完整 human review。 |
| 產生多少 noise？ | Clean control 的 `benchmark_unsupported_findings_per_kloc`。 |
| 花費多少資源？ | Input／cached input／output／reasoning tokens、tool calls、wall-clock latency、`estimated_cost_usd`。 |
| 如何失敗？ | `provider_error`、`timeout`、`budget_exhausted`、`harness_error`、`fixture_defect` 等 retained status。 |

本 benchmark 不評估 defect repair，也不允許 model-as-judge 取代 primary human adjudication。

## Corpus

兩個 fixtures 都是 first-party、MIT licensed；每個 mutation 都必須躲過 fixture 自己的完整 test
suite，並由獨立 witness 證明 clean→mutated→reverted behavior。

| Fixture | Language | In-scope LOC | Own tests | Seeded bugs | OCI tag |
|---|---:|---:|---:|---:|---|
| `fx-taskq-py` 1.0.4 | Python 3.12 | 1,367 | 205 | 4 | `ghcr.io/kuotunyu/coding-agent-eval-fx-taskq-py:1.0.4` |
| `fx-ledger-ts` 1.0.3 | TypeScript / Node 22 | 1,183 | 174 | 4 | `ghcr.io/kuotunyu/coding-agent-eval-fx-ledger-ts:1.0.3` |

八個 bugs 涵蓋 correctness 2、security 2、data_boundary 2、concurrency 1、release_claim 1；
每一 category 最多只有兩個樣本，不能據此做 category-level conclusion。

## Contamination 與 target leakage

Seeded bugs 是 benchmark 建立時新撰寫、未取自公開 issue／fix commit 的 mutations，因此只能稱為
**contamination-resistant**，不能稱為 contamination-free。

- Fixtures 與 bugs 的 `authored_at` cutoff 是 `2026-08-05`。
- 公開後可能進入未來 training data，contamination resistance 會隨曝光衰減。
- Public task registry 不含 patch bytes 或 canonical answer；agent 的 measure-container tool surface
  只暴露 source tree，不暴露 `bugs/`、`patches/`、witnesses、ledger 或 host paths。
- Benchmark maintainer／fixture author仍知道 ground truth。Blinding 能移除 provider／model brand bias，
  不能消除此結構性限制。

## OCI sandbox 與 reproducibility

Current runs 只接受 manifest-digest-qualified image。Manifest digest 與 config digest 分開驗證，
environment fingerprint 由九個固定 components 計算，mutable tag 不影響 fingerprint。

| Fixture | OCI manifest digest | OCI config digest |
|---|---|---|
| `fx-taskq-py` | `sha256:392d4fbb33427c4fee63ee6b00fa055665ae37ec099acbc140594ed2010c19ad` | `sha256:8796584be151aa59e641a7c4d70202f7d147ef6130241478a96f67459157e6d1` |
| `fx-ledger-ts` | `sha256:38450742408270a0e48ae053499dd626f61a4cf09139d40ae494838def4b0312` | `sha256:c7d310f6a41a47132484bddc47969547c9e34cb7628456415696c59af223d583` |

Measure container 使用 `--network none`、read-only root filesystem、`--cap-drop ALL`，沒有 host
bind mount。這是 tests 與 runtime probes 觀察到的 implementation property，不是 security
certification。細節見 [`docs/SANDBOX_VERIFICATION.md`](docs/SANDBOX_VERIFICATION.md)。

每個 current public trace 都必須：

- 使用 trace schema 0.2.0；
- 以唯一 `run_header` 開始、唯一 `cost` 與 `termination` 結束；
- 記錄 immutable image identity、`measure_container:<manifest_digest>`、per-call latency 與 usage；
- 先寫入本機 `.run-store/` private raw events，再經 fail-closed allowlist sanitizer；
- 能由 committed public evidence 驗證 sequence、usage、cost 與 identity，且拒絕 hash drift。

Historical trace schema 0.1.0 保留可讀，但永遠不可升格為 publication evidence。

## v0.1 acceptance criteria

Release candidate 必須同時滿足：

1. `tasks/v0.1.json` 恰好解析為 2 clean controls 與 8 mutations，所有 checksum、patch、bug、
   witness 與 fixture version 都一致。
2. 兩個 OCI images 可由匿名 digest-qualified pull 取得，且 manifest／config digest 與宣告值一致。
3. Registration 在第一個 provider call 前固定 task order、provider、model、API、reasoning、budgets、
   retry policy、OCI identities 與 environment fingerprints。
4. 全部 10 tasks 都保留 terminal outcome；errors、timeout 與 budget exhaustion 不得被刪除或重跑美化。
5. Current traces 通過 schema、sanitizer 與 replay contract；legacy evidence 保持明示不可發布。
6. 每個實際 candidate pair 都有完整 dual blinded human review；candidate set 為空時不得製造 ruling。
7. README／Benchmark Card／Data Card 的重要 claim 都能追到 committed artifact 與欄位。
8. Offline publication audit、online OCI audit、tests、Ruff、format、strict mypy、package build、Docker／
   Linux gates、leak scan 與 owner-only provenance 全部通過。
9. Git author／committer 與 GitHub Contributors 僅能是 `kuotunyu`；不得加入 `Co-authored-by`。
10. Source/tag、GitHub Release、Zenodo draft 與 Zenodo publish 是四個分離的 owner approval gates。

完整判定與 claim-to-evidence matrix 見
[`docs/RELEASE_READINESS.md`](docs/RELEASE_READINESS.md)。

## 本機驗證

需要 Python 3.12、[`uv`](https://docs.astral.sh/uv/)；Docker gates 另需可用的 Docker Engine。

```bash
uv sync --locked
uv run cae validate fixtures
uv run cae release audit --publication
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict src
uv build
```

驗證 GHCR anonymous pull 與 OCI identity（會讀取外部 registry，但不使用 credentials）：

```bash
uv run cae release audit --publication --online
```

完整 Docker gates：

```bash
uv run cae fixture verify fixtures/fx-taskq-py
uv run cae fixture verify fixtures/fx-ledger-ts
uv run pytest -q -m docker
```

Native Linux 的 clean-export／wheel smoke verification：

```bash
bash scripts/verify_release.sh
```

上述 offline commands 不需要 API key，也不會呼叫 paid provider。重新執行 reference suite 是另一個
明確付費 gate；流程見 [`docs/REFERENCE_SUITE.md`](docs/REFERENCE_SUITE.md) 與
[`docs/MANUAL_RUN.md`](docs/MANUAL_RUN.md)。

## Evidence 與文件索引

| 路徑 | 用途 |
|---|---|
| [`tasks/v0.1.json`](tasks/v0.1.json) | 10-task versioned registry |
| [`runs/reference/registration.json`](runs/reference/registration.json) | Pre-registered model/configuration、budgets、OCI identity |
| [`runs/reference/summary.json`](runs/reference/summary.json) | 10 個 retained outcomes 的 exact counts |
| [`runs/reference/tasks/`](runs/reference/tasks/) | Per-task run、status、findings、public trace |
| [`docs/BENCHMARK_CARD.md`](docs/BENCHMARK_CARD.md) | Metrics、denominators、結果解讀與 limitations |
| [`docs/DATA_CARD.md`](docs/DATA_CARD.md) | Corpus composition、provenance、license、contamination／leakage |
| [`docs/REFERENCE_SUITE.md`](docs/REFERENCE_SUITE.md) | Registration、run、replay 與 public/private boundary |
| [`docs/RELEASE_READINESS.md`](docs/RELEASE_READINESS.md) | Acceptance criteria、claim evidence、publication decision |
| [`release-manifest.json`](release-manifest.json) | Release artifact bytes 與 SHA-256 |

## Publication 與 citation

`CITATION.cff` 與 `.zenodo.json` 是待審 metadata，不代表 DOI 或 Zenodo record 已存在。
建立 source tag、GitHub Release、Zenodo draft 與正式 publish 前，仍需逐關取得 owner 明確批准。

## License

MIT，詳見 [`LICENSE`](LICENSE)。兩個 fixtures 也各自包含 MIT `LICENSE`。

## English summary

`coding-agent-eval` is a reproducible, ground-truth benchmark for coding-agent defect
discovery. BugSeed v0.1 contains two first-party fixtures, eight seeded mutations, and two
clean controls. Its single registered `gpt-5.6-luna` reference suite retained all ten
outcomes, but every task exhausted its token budget and submitted no findings. The release
therefore supports reproducibility and failure-analysis claims—not model ranking or a
human-verified performance claim.
