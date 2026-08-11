# v0.1 Release Readiness 與 Acceptance Contract

## Decision

BugSeed v0.1 的 corpus、OCI environments、retained outcomes、trace、cost／latency、failure taxonomy、
private/public boundary 與 replay rules 都有 machine-checkable evidence；但 2026-08-10 reference suite
使用 `openai-responses@0.1.0`，其多輪 tool conversation 不符合目前官方 contract。因此目前是
**methodology-fixed, smoke-gate-pending release candidate**，不是已閉合的 model-evaluation release。

這不等於 AI Developer Tools／LLM Evaluation 的 model-ranking flagship。Reference suite 的 10 個
tasks 全部因 token budget 終止且沒有 findings；它能支持 reproducibility、instrumentation 與
failure-analysis claims，不能支持 model effectiveness、human-verified recall 或跨 model ranking。

Adapter 0.3／prompt 0.2 attempt 4 在 TaskQ 1.0.5 正常 completion，但提交兩個 clean-control
findings；兩者皆經 deterministic offline reproduction 證實為工程缺陷，因此 gate 失敗且 1.0.5
失去 release eligibility。TaskQ 1.0.6 已修復兩者、bump fixture／OCI identity 並通過離線 contracts。
Attempt 5 使用 1.0.6，但在 final provider turn 前達到 token budget；它仍是 failed gate，不是 clean
validation。Adapter 0.4 已離線修正 clean completion／no-output 分類；需以新批准的 paid
clean／mutated smoke 驗證後，才決定是否建立 schema 1.1 registration。Source/tag、GitHub Release、
Zenodo draft、Zenodo publish 仍各需獨立批准。

## Claim-to-evidence matrix（2026-08-11）

下表是 README、GitHub About、Benchmark Card 與 release metadata 可公開敘述的上限；沒有列在表內或
無法由指定欄位重算的數字，不得升格為 release claim。

| 公開 claim | Machine-checkable evidence | 可重算值／限制 |
|---|---|---|
| Corpus 規模 | `tasks/v0.1.json` 的 `tasks[*].snapshot` 與 `bug_id`；`fixtures/*/fixture.yaml` 的 `bugs` | 10 tasks＝2 clean controls＋8 single-mutation tasks，分布於 2 fixtures。 |
| Fixture 規模 | `fixtures/*/fixture.yaml` 的 `fixture_version`、`scope.in_scope_loc`；`fixtures/*/witness/clean_suite.yaml` 的 `expected_clean.stdout_contains` | `fx-taskq-py` 1.0.6：1,467 LOC／222 tests；`fx-ledger-ts` 1.0.3：1,183 LOC／174 tests。 |
| 舊 agent configuration | `runs/reference/registration.json` 與各 trace header | `openai`／`gpt-5.6-luna`／Responses API／`high`／adapter 0.1；只代表舊協定 retained outcomes。 |
| Pre-registration identity | `runs/reference/registration.json` 的 `suite_id`、`ordered_task_ids`、`task_registry_sha256`；`runs/reference/task-registry.json` 的 exact bytes | `suite-ca6834e720ce87309847af909c342789286f7cffb943b03e9e140c73e040d80b`；順序固定，TaskQ 1.0.4 snapshot 與 current 1.0.6 registry 分離。 |
| Budget 與 retry | `runs/reference/registration.json` 的 `budgets`、`retry_policy` | 每 task：200,000 tokens／60 tool calls／900 s／USD 0.25；suite 上限 USD 2.50；`no_automatic_retry`。 |
| Retained outcomes | `runs/reference/summary.json` 的 `task_count`、`counts`；各 `status.json` | 10/10 有 terminal outcome；10 個皆為 `budget_exhausted`。這不是 10/10 task success。 |
| Failure classification | 各 `run.json` 的 `termination_reason`；各 `trace.jsonl` 最後一筆 `termination.payload.reason` | 10/10 為 `budget_exhausted_tokens`；provider error 與 harness error 皆為 0。 |
| Finding／review coverage | 各 `findings.json` 的 `findings`；各 `run.json` 的 `findings_submitted` | 0 findings、0 candidate pairs，因此本次沒有 human ruling；不得宣稱 human-verified recall。 |
| Detection 結果 | 8 個 mutated task 的 `findings.json` 與 `run.json` | 舊協定 submitted candidate coverage 0/8；不是 current-adapter effectiveness evidence；正式 `verified_*` 未產生。 |
| Estimated cost | 10 個 `run.json` 的 `usage.estimated_cost_usd`、`pricing_table_version`、`usage.completeness` | 合計 USD 0.097166；每 task USD 0.008738–0.012885；全部 usage `complete`。這是估值，不是帳單。 |
| Latency | 10 個 `run.json` 的 `wall_clock_ms` | 合計 500.316 s；平均 50.032 s；範圍 40.566–65.886 s。 |
| Trace／sandbox | 每份 `trace.jsonl` 的首筆 `run_header`、唯一 `cost`、末筆 `termination` | 10/10 使用 trace schema 0.2.0 與 `measure_container:<manifest_digest>`；sandbox 是 observed property，不是 security certification。 |
| OCI identity | `runs/reference/registration.json` 的 `image_identities`、`environment_fingerprints`；`fixtures/*/fixture.yaml` 的 `environment` | 兩個 GHCR images 同時固定 manifest digest、config digest 與 environment fingerprint；mutable tag 不進 fingerprint。 |
| Reproducibility | `uv run cae release audit --publication`；online 再加 `--online`；`release-manifest.json` | Offline 驗證 committed evidence；online 額外驗證匿名 digest-qualified pull。 |
| Contributor provenance | `git log --format='%an <%ae>|%cn <%ce>'`、`git log --format='%B'`、publication audit | Release lineage 的 author／committer 僅 `kuotunyu`，且不得有 `Co-authored-by`。 |

GitHub About 建議文字：

> 可重現的 AI coding agent defect-discovery benchmark；以 seeded defects、OCI sandbox、trace replay
> 與 evidence-backed release 評估 recall、unsupported findings、cost 與 latency。

About 必須與 README 使用同一 claim boundary，不加入「leaderboard」、「state-of-the-art」或未觀察到的
success-rate 敘述。

## v0.1 Acceptance Criteria

| ID | Requirement | Evidence／current disposition |
|---|---|---|
| R1 | Versioned registry 恰好包含 2 clean＋8 mutated tasks；所有 fixture、checksum、patch、bug、witness reference 一致。 | `tasks/v0.1.json`、schemas、`tests/test_tasks.py`、fixture rebuild／witness gates。 |
| R2 | 兩個 current fixtures 同時固定 GHCR repository、versioned tag、OCI manifest digest 與 config digest；digest-qualified anonymous pull 可驗證。 | `fixtures/*/fixture.yaml`、`cae fixture environment --online`、`cae release audit --publication --online`。 |
| R3 | 在第一個 provider call 前 pre-register adapter/prompt identity、conversation state、provider、model、API、reasoning、task order、budgets、retry 與 OCI identity。 | Schema 1.1 與 tests 已實作；舊 schema 1.0 僅可讀，新的 paid registration 尚未建立。 |
| R4 | 十個 tasks 每一個都保留 terminal outcome；failure 不得移除或重跑取代。 | `runs/reference/summary.json` 與 10 個 `status.json`；目前 10/10 `budget_exhausted`。 |
| R5 | Current public traces 使用 schema 0.2.0，具唯一 header／cost／termination、per-call latency、usage、OCI identity；legacy contract 固定不可發布。 | Current `trace.jsonl` artifacts、trace schema、sanitizer／replay tests；已由測試取代的 schema-0.1 provider diagnostics 不納入 release tree，仍可由 Git history 追溯。 |
| R6 | 每個 completed mutated outcome 的 candidate pairs 都需 dual blinded human review 與 deterministic replay；空 candidate set 不得製造 rulings。 | Attempts 3／4 是 failed clean controls，attempt 5 是 budget-exhausted clean；沒有 mutated candidate pair 或 formal ruling；machine reproduction 不冒充 human review。 |
| R7 | 新舊 evidence 分層，cost、latency、failure 與 metric null behavior 清楚；不得跨 adapter／prompt／fixture identity 回填結果。 | README、Benchmark Card、Reference Suite 與本 matrix 已分層；attempts 3--5 保持各自 1.0.4／1.0.5／1.0.6 與 adapter 0.3 identity，adapter 0.4 目前僅有離線證據。 |
| R8 | Offline publication audit、full tests、Ruff、format、strict mypy、build、Docker／Linux gates 全部可從公開 source 執行，不需 API key。 | `.github/workflows/ci.yml`、`scripts/verify_release.sh`；paid provider 不在 CI。 |
| R9 | Release lineage 僅 `kuotunyu`，沒有 `Co-authored-by`；public artifacts 不含 secrets、raw store、worksheet keymaps 或 Docker credentials。 | `git.owner_only`、`artifact.private_data`、leak scan 與 history commands。 |
| R10 | Source/tag、GitHub Release、Zenodo draft、Zenodo publish 各自是不可合併的 explicit owner gate。 | Release process policy；目前全數停在外部寫入前。 |

## Reference execution interpretation

舊 registration 使用 OpenAI `gpt-5.6-luna`、Responses API、high reasoning、每 task 200,000-token
budget 與 `no_automatic_retry`。10 個 outcomes 的 estimated cost 合計 USD 0.097166，wall-clock 合計
500.316 s；全部是 `budget_exhausted_tokens`，沒有 provider／harness error，也沒有 finding。

因此：

- 可發布「舊協定所有預先註冊 outcomes 都保留且可稽核」；
- 可發布舊協定 cost、latency、budget 與 termination distribution，但需緊鄰 adapter 0.1 限制；
- 可描述舊協定 submitted candidate coverage 為 0/8，不得當成 current adapter effectiveness；
- 不可發布 `verified_bug_recall`、`verified_finding_precision` 或 cost per verified bug 數字；
- 不可把 clean-control silence 當成完整 code-review 成功；
- 不可將沒有 candidates 說成 independent reviewers 達成一致。

早期 `runs/live-*` diagnostics 已從 release tree 移除並保留於 Git history；deterministic baselines
保留 arithmetic value。兩者都不是本次 registration 的結果，也不能合併到上述數字。

## Flagship threshold

尚未達到。程式層的 conversation validity、privacy boundary、registration identity、recruiter-first
README 與 conversation validity 已閉合；adapter 0.2 的兩次 paid clean smoke 保留為
budget-exhausted outcomes，adapter 0.3／prompt 0.2 attempts 3／4 分別揭露 1.0.4／1.0.5 fixture
defects，attempt 5 則在 1.0.6 final provider turn 前耗盡 token budget。Adapter 0.4 的 clean terminal
semantics 目前只有離線證據。至少要在修正版 TaskQ 通過一個 clean＋一個 mutated smoke，且完整
保留 terminal outcomes，才能重新評估旗艦門檻。

## Reproducibility 與 release gates

最小 offline gate：

```powershell
uv run cae release audit --publication
```

Online OCI gate：

```powershell
uv run cae release audit --publication --online
```

完整本機驗證：

```powershell
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict src
uv build
uv run pytest -q -m docker
```

Native Linux／clean-export／installed-wheel gate：

```bash
bash scripts/verify_release.sh
```

CI 只執行 secret-free offline publication audit、tests、build 與 Docker gates；不會呼叫 paid provider、
push GHCR、建立 GitHub Release 或操作 Zenodo。

## Git provenance 與 privacy

Release lineage 的 author 與 committer 必須精確為：

```text
kuotunyu <61350295+kuotunyu@users.noreply.github.com>
```

不允許 `Co-authored-by` trailer。Independent reviewers 若未來提供 worksheet，只提供 decision evidence；
由 `kuotunyu` 驗證並提交公開 artifacts，不以 commit／trailer 改變 GitHub Contributors。

禁止追蹤或發布：`.env`、`.run-store/`、API key、raw provider／tool payload、Docker auth、worksheet
keymaps 或 reviewer private identity。`release-manifest.json` 只列 public benchmark contracts／evidence。

## Zenodo disposition

**Metadata／artifact readiness：NO-GO，等待 adapter 0.4 paid smoke gate。External publication：尚未執行。**

`CITATION.cff` 與 `.zenodo.json` 已按 v0.1.0、正體中文 description、creator `kuotunyu` 與 release
limitations 對齊；`release-manifest.json` 提供 deterministic artifact bytes／SHA-256。沒有捏造 DOI，
也不表示 Zenodo record 已建立。

進入外部發布時依序停在：

1. Source branch／annotated tag push approval；
2. GitHub Release approval；
3. Zenodo draft upload approval；
4. Zenodo publish approval。

Zenodo 指派 DOI 後，需以新的 owner-only source commit 更新 citation／README、重跑全部 gates，並再次
取得後續 source/tag action 批准。

## Remaining limitations

- 2 fixtures／8 bugs 對 model comparison 太小；
- 只有一個 provider／model／configuration，沒有 repeated seeds；
- 10 tasks 全部 budget-exhausted，沒有 completed／human-verified result；
- First-party fixtures、fixture-author ground-truth knowledge 與 public exposure 限制 external validity；
- Cost 是版本化 estimate，不是 invoice；
- Sandbox 與 anonymous OCI availability 是 observed／operational properties，不是長期 SLA 或認證。
