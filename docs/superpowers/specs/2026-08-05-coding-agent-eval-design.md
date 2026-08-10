# coding-agent-eval — Design Specification

- **Document status**: approved design baseline for v0.1 implementation
- **Date**: 2026-08-05
- **Benchmark version this spec defines**: `0.1.0`
- **Repository**: `coding-agent-eval`
- **Benchmark dataset / fixture suite product name**: `BugSeed`

> 本文件是設計基準,不是實作紀錄。文中所有 sandbox 隔離性質、CI gate 行為與效能數字都是
> **design requirements**,尚未在本機驗證(設計期間 Docker Desktop 未啟動)。實作計畫的
> 驗收任務負責把它們變成已驗證事實。

---

## 1. Positioning

### 1.1 Public one-liner

> A ground-truth benchmark for measuring whether coding agents can discover known defects,
> how many unsupported findings they produce, and what resources they consume under
> reproducible sandbox conditions.

### 1.2 主角與配角

| 角色 | 定位 |
|---|---|
| **Benchmark + evaluation methodology** | 主角。Repository 的存在理由 |
| **Fixture suite (`BugSeed`)** | 主角的資料資產 |
| **Reference agent runtime** | 配角。被測 baseline 與 adapter 範例,不是賣點 |
| **MCP server** | 不在主線,v0.1 不遷移 |

### 1.3 與相鄰工作的區別

- **不是 SWE-bench 類**:那量測「能不能修好」,本專案量測「能不能**發現**」。
- **不是 LLM-as-judge leaderboard**:headline 指標由 deterministic matcher + 凍結的
  blinded human adjudication ledger 產生。LLM judge 只作 secondary sensitivity analysis,
  獨立報告,不得混入 primary metric。
- **不是 agent 框架 demo**:agent runtime 是被量測物。

### 1.4 Provenance 陳述規則

- 可以陳述:reference runtime evolved from an internal prototype。
- **不得**陳述或暗示 private archive 可應要求提供。
- **不得**提供任何指向 private archive 的路徑、連結或 Git metadata。
- 舊專案的 incident 教訓只能以**重新撰寫的 sanitized lessons** 形式出現,不得搬入
  舊 trace、報告原文、絕對路徑、email 或第三方原始碼。

---

## 2. Scope 與 Non-goals

### 2.1 v0.1 in scope

1. 2 個 **first-party** realistic fixture repositories(自行撰寫、完整規格化)
2. 每個 fixture 4 個 **injected** bugs,合計 **8 bugs**
3. 每個 fixture 的 **clean control** snapshot 與已稽核的 defect inventory
4. 語言涵蓋 **Python** 與 **TypeScript**
5. Bug taxonomy 至少涵蓋 5 類中的 **4 類**
6. 全部 schemas(fixture / bug / finding / trace / ledger / results)
7. Patch runner 與 **machine-executable witness contract** runner
8. Deterministic candidate matcher
9. Adjudication ledger 格式、blinded 匯出/匯入工具、fail-closed evaluator
10. Evaluator 與 metrics
11. Private raw evidence store 與 public sanitized trace 的邊界(含 fail-closed sanitizer)
12. Minimal reference agent adapter 介面 + tool surface
13. 一個 **deterministic fake agent baseline**(CI 可用,無 API)
14. 一個 real provider baseline adapter —— **僅列為後續 manual run,不進 CI**
15. CPU + Docker CI gates
16. Recruiter-first README skeleton、threat model、data card、benchmark card

### 2.2 v0.1 明確不做

- 35 bugs 的完整資料集
- Historical cohort
- Upstream(第三方)fixture
- Poisoned / prompt-injection fixture
- Partial / timeout fixture
- Anthropic adapter
- MCP server
- UI dashboard、leaderboard service
- Nightly multi-model sweep
- Hugging Face 部署
- Public remote / push / tag / Release
- 任何 paid API 執行

### 2.3 硬性環境限制

- **不使用本機 GPU**(任何階段)
- CI 必須在無 API key、無付費呼叫的情況下全綠
- 開發與 CI 皆為 CPU + Docker

---

## 3. Architecture

### 3.1 Component map

```
coding-agent-eval/
├── schemas/                  # JSON Schema:所有 manifest 與 artifact 的契約
├── fixtures/                 # BugSeed fixture suite(v0.1:2 個 first-party)
├── src/coding_agent_eval/
│   ├── fixtures/             # loader、tree checksum、patch runner、witness runner、leak checker
│   ├── sandbox/              # prepare / measure profile、image builder、env fingerprint
│   ├── agent/                # adapter 介面、tool surface、fake baseline、provider baseline
│   ├── trace/                # raw run store、public trace writer、sanitizer
│   ├── evaluator/            # dedup、matcher、ledger、metrics、replay
│   └── cli.py                # `cae` 進入點
├── pricing/                  # 版本化定價表
├── ledger/                   # 凍結的 adjudication ledger(committed)
├── runs/                     # public sanitized traces + results(committed,經 sanitizer)
├── .run-store/               # private raw evidence(gitignored,永不公開)
└── docs/
```

### 3.2 三個執行階段的資料流

```
[prepare]  fixture tree + env recipe --(network ON)--> prepared image (digest pinned)
                                                        + env fingerprint + lock manifest

[measure]  prepared image (digest) --(tool container: --network none)--> agent run
           agent --tools--> sandbox                    host --> provider API (network ON)
           全部 I/O --> .run-store (raw, private)
           allowlisted 投影 --> runs/<run_id>/trace.jsonl (public, sanitized)

[evaluate] public trace + fixture manifests + frozen ledger --(local only)--> results.json
```

### 3.3 邊界原則

- **Agent 只看得到 measured tree**。bug manifest、patch、witness、defect inventory
  一律不在 measured tree 內(見 §5.2 answer-leak 邊界)。
- **Evaluator 不看 raw evidence**。它只吃 public sanitized trace + fixture manifest + ledger。
  這保證公開 artifact 足以重現評分。
- **Sanitizer 是唯一的 raw → public 通道**,且 fail-closed。

---

## 4. Bug taxonomy

| Category (enum value) | 定義 | 範例 |
|---|---|---|
| `correctness` | 程式行為與其明確規格/合約不符 | off-by-one、錯誤的 early return、邊界條件反向 |
| `security` | 可被外部輸入利用而破壞機密性/完整性/可用性 | 非 constant-time 比對、path traversal、不安全反序列化 |
| `concurrency` | 只在特定交錯下顯現的缺陷 | check-then-act race、共享可變狀態未保護 |
| `data_boundary` | 信任邊界上的驗證/編碼/型別假設失誤 | 未驗證輸入直通查詢、encoding 假設、null 處理 |
| `release_claim` | 對外宣稱(README/CHANGELOG/版本/docstring)與程式碼實際行為不符 | 文件宣稱已支援但未實作 |

`severity` enum:`critical` / `high` / `medium` / `low`。

---

## 5. Fixture lifecycle

### 5.1 Fixture 目錄配置

```
fixtures/<fixture_id>/
├── fixture.yaml                    # fixture manifest
├── tree/                           # ★ 唯一會被 materialize 進容器的內容
│   ├── src/ …                      #   first-party 服務原始碼
│   ├── tests/ …                    #   專案自身的測試(非 witness)
│   ├── README.md  LICENSE  …
├── bugs/
│   ├── B-001.yaml                  # bug manifest(含 witness contract)
│   └── B-001.patch                 # 植入 patch(相對 tree/ 的 unified diff)
├── witness/
│   └── B-001/…                     # witness artifacts(★ 絕不進 measured tree)
├── env/
│   ├── Dockerfile                  # rebuild recipe
│   └── env.lock.json               # 解析後的精確依賴
├── defects.md                      # clean tree 的完整 defect 稽核紀錄(人類可讀)
└── known_residual_defects.yaml     # clean tree 中已知且承認的殘留缺陷(機器可讀)
```

### 5.2 Answer-leak 邊界(硬性)

Measured tree = `tree/` 的內容,**加上** mutated 情形下已套用的 patch。以下一律不得出現在
measured tree 中,由 CI gate **G4** 強制:

- `bugs/`、`*.patch`、`witness/`、`defects.md`、`known_residual_defects.yaml`
- 任何 `bug_id` 字串
- 任何 `canonical_claim` / `canonical_root_cause` 的 5-gram
- 任何以 bug 為名的測試檔名、分支名、註解

Witness artifacts 只在 **CI 驗證作業**中以 overlay 方式掛入,量測階段永不存在。

### 5.3 生命週期狀態

```
authored → validated (G1) → witness-verified (G2) → checksum-frozen (G3) →
leak-audited (G4) → released (fixture_version 凍結)
```

任何 `tree/`、`bugs/`、patch 或 witness 內容變更 ⇒ **必須** bump `fixture_version`。
`fixture_version` 是 adjudication ledger key 的一部分,所以舊裁決不會無聲沿用到新內容。

### 5.4 v0.1 的兩個 fixture

| fixture_id | 語言 | 形態 | 規模目標 | bug 類別覆蓋 |
|---|---|---|---|---|
| `fx-taskq-py` | Python 3.12 | 背景任務佇列服務(HTTP API + worker + 持久化) | **1,321**(實際) | `security`、`correctness`、`data_boundary`、`release_claim` |
| `fx-ledger-ts` | TypeScript (Node 22) | 帳務記帳服務(HTTP API + 併發結算) | **1,184**(實際) | `concurrency`、`correctness`、`data_boundary`、`security` |

兩者合計覆蓋 **5 類中的 5 類**(超過「至少 4 類」的要求)。

**規模決議(2026-08-05,Technical Lead 核准)**:原訂下限 1,500 LOC。`fx-taskq-py`
實作完成後為 **1,321 in-scope LOC**(15 個模組、183 個測試),經兩次擴充
(idempotency、concurrency cap、dead-letter、metrics、client)仍差 179 行。TL 決議
**接受 1,321**,理由是該數字的原始意圖是「真實但可完整稽核」,而非行數本身:

- 上限仍為 3,000。超過該規模,`defects.md` 對「所有 benchmark-scope defects 已完整
  列舉」的主張就不再可信,而 clean control 的 headline 指標完全依賴這個主張。
- 下限改為 **~1,200**,即「足以構成一個具有多模組、持久化、認證邊界與併發面的真實
  服務」。`fx-taskq-py` 在 1,321 行內達成了這些,因此下限的目的已經滿足。

此決議記錄於此而非默默違反,因為 fixture 規模直接影響 defect 稽核的可信度。

`fx-ledger-ts` 實作完成後為 **1,184 in-scope LOC**(13 個模組、168 個測試),同樣落在
上述修訂後的 ~1,200 下限附近。兩個 fixture 的規模因此是一致的,而非其中一個被特別
放寬。1,184 行同樣可以被完整讀完——`defects.md` 中記錄的 replay 缺陷正是靠通讀發現
的,沒有任何測試覆蓋該路徑。

兩個 fixture 皆為 **first-party**,授權為 MIT,由本 repository 作者撰寫,因此:

- 無第三方 license 再散布問題
- public trace 可安全保留 first-party 內容的 allowlisted excerpt
- clean control 的 defect inventory 具備可稽核的完整性

---

## 6. Schemas(field level)

所有 schema 以 JSON Schema Draft 2020-12 定義於 `schemas/`,並由 `cae validate` 強制。
以下為欄位層規格;YAML 為書寫格式,語意由 JSON Schema 定義。

### 6.1 `fixture.yaml`

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| `schema_version` | string | ✔ | 固定 `"0.1"` |
| `fixture_id` | string(slug) | ✔ | 全域唯一,`^[a-z0-9]+(-[a-z0-9]+)*$` |
| `name` | string | ✔ | 人類可讀名稱 |
| `fixture_version` | semver | ✔ | 任何內容變更即 bump |
| `provenance` | enum | ✔ | `first_party` \| `upstream_injected` \| `upstream_historical`(v0.1 只允許 `first_party`) |
| `language` | enum | ✔ | `python` \| `typescript` \| `go` |
| `license` | SPDX id | ✔ | v0.1 限 `MIT` |
| `license_file` | path | ✔ | 相對 `tree/` |
| `authored_at` | date | ✔ | fixture 建立日,contamination cutoff 報告用 |
| `scope.in_scope_paths` | string[] (glob) | ✔ | 計入 benchmark scope 的路徑 |
| `scope.out_of_scope_paths` | string[] (glob) | ✔ | 明確排除 |
| `scope.in_scope_categories` | enum[] | ✔ | 此 fixture 承認的 bug 類別 |
| `scope.in_scope_loc` | integer | ✔ | 對 `in_scope_paths` 量測的有效行數,寫入 manifest;為 `benchmark_unsupported_findings_per_kloc` 的分母 |
| `scope.loc_tool` | string | ✔ | 量測工具與版本,固定為 repo 內建的 `cae-loc <version>` |
| `clean_control.tree_checksum` | sha256 | ✔ | clean tree 的 canonical checksum(§6.7) |
| `clean_control.witness_suite` | path | ✔ | clean 契約集合,格式見 §6.9 |
| `known_residual_defects` | path | ✔ | 指向 `known_residual_defects.yaml`,格式見 §6.8 |
| `environment.base_image_digest` | string | ✔ | `sha256:…`,digest pin |
| `environment.prepared_image_tag` | string | ✔ | prepare 產出的 tag |
| `environment.lock_manifest` | path | ✔ | `env/env.lock.json` |
| `environment.rebuild_recipe` | path | ✔ | `env/Dockerfile` |
| `environment.fingerprint` | sha256 | ✔ | prepare 時記錄的環境指紋(§9.4) |
| `bugs` | string[] | ✔ | bug_id 清單 |

### 6.2 `bugs/<bug_id>.yaml`

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| `schema_version` | string | ✔ | `"0.1"` |
| `bug_id` | string | ✔ | 格式 `<fixture_id>/B-NNN` |
| `fixture_id` | string | ✔ | 必須與所在 fixture 相符 |
| `fixture_version` | semver | ✔ | 必須與 `fixture.yaml` 相符 |
| `category` | enum | ✔ | §4 的五類之一 |
| `subcategory` | string | ✔ | 自由文字,分析用 |
| `severity` | enum | ✔ | `critical`/`high`/`medium`/`low` |
| `provenance` | enum | ✔ | v0.1 固定 `injected` |
| `authored_at` | date | ✔ | mutation 撰寫日期(contamination cutoff) |
| `patch` | path | ✔ | 相對 fixture 根;unified diff,base 為 `tree/` |
| `compound_group` | string \| null | ✔ | 非 null 時,同組 bug 允許被同一 finding 同時匹配(§8.4) |
| `localization.primary.file` | path | ✔ | 相對 measured tree 的 POSIX 路徑 |
| `localization.primary.line_start` | integer ≥1 | ✔ | mutated tree 的行號 |
| `localization.primary.line_end` | integer ≥ start | ✔ | |
| `localization.line_tolerance` | integer ≥0 | ✔ | 對稱擴張量;v0.1 預設 `8` |
| `localization.acceptable_alternates[]` | object[] | ✔ | 可為空陣列。合法的替代回報位置(如呼叫點),欄位同 `primary` |
| `canonical_claim` | string | ✔ | 一句話缺陷陳述 |
| `canonical_root_cause` | string | ✔ | 機制描述;adjudicator 的比對基準 |
| `witness` | object | ✔ | §7 |

### 6.3 Witness contract(`bugs/<bug_id>.yaml` 的 `witness` 區塊)

見 §7。

### 6.4 Finding schema(agent 輸出)

Agent 透過 `write_findings` 工具提交。每個 finding:

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| `id` | string | ✔ | run 內唯一 |
| `file` | path | ✔ | 相對 measured tree 的 POSIX 路徑 |
| `line_start` | integer ≥1 | ✔ | |
| `line_end` | integer ≥ start | ✔ | |
| `category` | enum | ✔ | §4 五類 |
| `severity` | enum | ✔ | |
| `claim` | string(1–400) | ✔ | 缺陷是什麼 |
| `root_cause` | string(1–800) | ✔ | 為什麼錯 —— adjudication 的核心比對欄位 |
| `evidence` | string(1–800) | ✔ | agent 實際觀察到什麼 |
| `suggested_verification` | string(1–400) | ✔ | 人類如何確認 |

Schema 以 `strict` 模式提供給 provider(`additionalProperties: false` + 完整 `required`),
以降低 tool-call schema 不符造成的浪費步數。

### 6.5 `finding_hash`

```
finding_hash = sha256( canonical_json({
    file, line_start, line_end, category,
    claim:      normalize(claim),
    root_cause: normalize(root_cause),
    evidence:   normalize(evidence)
}) )

normalize(s) = NFKC(s) → 折疊所有連續空白為單一空格 → strip → casefold
canonical_json = 鍵排序、UTF-8、無空白分隔
```

**`evidence` 納入雜湊**。理由:裁決不只問「描述的是不是同一個缺陷」,還問
「這個 finding 的證據是否真的成立」(§8.3)。若 `evidence` 不進 key,一個 claim 與
root_cause 相同、但引用了**幻覺證據**的 finding 會沿用先前的 `same_root_cause` 裁決,
等於讓捏造證據免費通過。

刻意排除 `id`、`severity`、`suggested_verification`:它們不影響「這是不是同一個缺陷、
證據是否成立」這個問題。此規則凍結於 `adjudication_protocol_version`。

### 6.6 Adjudication ledger entry

| 欄位 | 型別 | 說明 |
|---|---|---|
| `key.fixture_version` | semver | 三元組 key 之一 |
| `key.bug_id` | string | |
| `key.finding_hash` | sha256 | |
| `decision` | enum | `same_root_cause` \| `different_root_cause` \| `insufficient` |
| `rationale` | string(≤280) | 簡短理由 |
| `adjudication_protocol_version` | string | 協定版本 |
| `adjudicator_id` | string | 假名,例如 `A1` |
| `decided_at` | date | 只到日期,不含時間 |
| `entry_hash` | sha256 | 對上述欄位的 canonical hash,append-only 完整性 |

Ledger 為 **append-only JSONL**,置於 `ledger/adjudications.jsonl`,commit 進版本控制。

### 6.7 Tree checksum(canonical)

```
tree_checksum = sha256( 對 tree/ 內所有檔案,依 POSIX 路徑 lexicographic 排序後串接:
                        path + "\0" + mode_bits + "\0" + sha256(file_bytes) + "\n" )
mode_bits ∈ {"100644","100755"}  # 只保留可執行位,忽略其餘 POSIX/Windows 差異
```

**Checksum 對「實際 committed bytes」計算,不做任何正規化。** 由此推得三條不可妥協的性質:

1. LF → CRLF **是** byte change,checksum **必須**改變。checksum 不吸收行尾差異。
2. 跨平台穩定性來自 **Git checkout policy**,不是 checksum normalization。
3. 因此 `.gitattributes` 必須用 **path-specific 規則**,不是全 repo 一刀切:

```gitattributes
# 一般原始碼與文件:正常化行尾,方便跨平台協作
* text=auto eol=lf

# 需要 byte-stable 的資產:完全不轉換,checkout 什麼就是什麼
fixtures/**      -text
*.patch          -text
```

全 repo 使用 `* -text` 是錯的:它讓一般文件也失去正規化,卻無助於 checksum 穩定性 ——
穩定性本來就該由 checkout policy 提供。

### 6.8 `known_residual_defects.yaml`

**v0.1 硬性規則:`defects` 陣列必須為空。**

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| `schema_version` | string | ✔ | `"0.1"` |
| `fixture_id` | string | ✔ | |
| `fixture_version` | semver | ✔ | |
| `defects[]` | array | ✔ | **v0.1 schema 強制 `maxItems: 0`** |

為什麼 v0.1 不支援非空 residual defects:排除一個 finding 需要判斷它是否**真的**描述了
該殘留缺陷,而 file / line / category 三項比對只證明位置相近。用位置相近就把 finding
排除在 `unsupported_findings` 之外,等於讓「碰巧落在附近的錯誤 finding」免費獲得豁免 ——
這正是 §8.2 命名紀律所反對的那種偷渡。完整的 residual-defect **語義**裁決延後至 v0.2。

因此 v0.1 的 metrics **不存在 `R` 集合**,也不存在自動 residual exclusion。

**Clean-control run 發現疑似真實缺陷時的處理程序**(不得走捷徑):

1. **不得**直接計為 unsupported,也**不得**直接排除。
2. 立即停止該 fixture 的公開 scoring。
3. 人工確認該 finding 是否為真實缺陷。
4. 若為真,修正 clean fixture 本身。
5. Bump `fixture_version`。
6. 重跑 witness(G2)、checksum(G3)、answer-leak(G4)與全部 scoring。

亦即:v0.1 的立場是「clean control 必須真的乾淨」,而不是「把髒的部分登記下來繞過」。
非空 `defects` ⇒ 該 fixture **不具 release eligibility**,validator 與 release gate 皆失敗。

### 6.9 `witness/clean_suite.yaml`

Clean control 的契約。欄位與 §7 的 witness contract 相同,但**只有** `expected_clean`,
沒有 `expected_mutated`(它不對應任何單一 bug,而是斷言 clean tree 整體健康)。
`artifacts[]` 與 `overlay_target` 語意不變。

### 6.10 衍生雜湊與 LOC 計數規則

**`bug_set_hash`**(記入 public trace header,用於證明評分所依據的 bug 集合):

```
bug_set_hash = sha256( canonical_json(
    sorted([ {bug_id, category, localization, canonical_claim, canonical_root_cause,
              compound_group} for each bug ], key=bug_id) ) )
```

**`cae-loc` 計數規則**(取代外部工具,使 CI 可驗證且無額外相依):

```
對 in_scope_paths 匹配到的每個 UTF-8 文字檔:
  計入一行 ⟺ strip 後非空 AND 不是該語言的整行註解
  語言註解前綴:python → "#";typescript → "//";區塊註解 /* … */ 整段排除
不計入:二進位檔、符號連結、out_of_scope_paths 匹配到的檔案
```

規則凍結於 `benchmark_version`;變更即 major bump,因為它改變一個 headline 指標的分母。

### 6.11 `results.json`

必含:`benchmark_version`、`fixture_id`、`fixture_version`、`snapshot`、`run_id`、
`agent_adapter`+version、`provider`、`model`、`budget`(四維)、
`adjudication_protocol_version`、`trace_schema_version`、`pricing_table_version`、
`redaction_manifest_version`、全部 §8.5 指標、`n_valid`/`n_invalid`、`termination_reason`。

---

## 7. Witness contract

每個 bug 必須附帶 **machine-executable witness contract**。它證明「這個 bug 真的存在」,
使 ground truth 本身受 CI 驗證,而不是靠人記得。

Witness **不限 pytest**。契約以下列欄位定義:

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| `contract_version` | string | ✔ | `"0.1"` |
| `prepare` | string[] | ✔ | argv 形式的準備指令(可為空陣列) |
| `command` | string[] | ✔ | argv 形式的 witness 指令 |
| `workdir` | path | ✔ | 相對 measured tree |
| `timeout_seconds` | integer 1–600 | ✔ | |
| `environment` | map<string,string> | ✔ | 決定性環境變數;必含 `TZ=UTC`、`LC_ALL=C.UTF-8`,Python fixture 另含 `PYTHONHASHSEED=0` |
| `expected_clean.exit_code` | integer | ✔ | clean tree 上的預期 exit code |
| `expected_clean.stdout_contains` | string[] | ✔ | 全部必須出現(可為空陣列) |
| `expected_clean.stdout_not_contains` | string[] | ✔ | 全部必須不出現(可為空陣列) |
| `expected_mutated.exit_code` | integer | ✔ | mutated tree 上的預期 exit code |
| `expected_mutated.stdout_contains` | string[] | ✔ | |
| `expected_mutated.stdout_not_contains` | string[] | ✔ | |
| `artifacts[]` | object[] | ✔ | `{path, sha256}`;witness 檔案的內容雜湊 |
| `deterministic` | boolean | ✔ | v0.1 必須為 `true` |
| `overlay_target` | path | ✔ | witness artifacts 在容器內的 overlay 掛載點,必須位於 `out_of_scope_paths` 之外的暫時目錄 |

**契約要求 `expected_clean ≠ expected_mutated`**,由 schema 驗證強制:若兩者完全相同,
該 witness 不能證明任何事,CI 直接拒絕。

`expected_mutated` 刻意允許「非測試失敗」的形態(例如 exit code 為特定值、stdout 出現
特定錯誤字串),以支援 concurrency 或 TypeScript fixture 用非 pytest 的驗證方式。

---

## 8. Matching、adjudication 與 metrics

### 8.1 前置:Deduplication —— v0.1 只做 exact collapse

在任何匹配之前對 findings 去重。**v0.1 的 primary scoring 只允許 exact semantic
duplicate collapse:**

```
duplicate(f1, f2) ⟺ finding_hash(f1) == finding_hash(f2)      # §6.5,含 evidence
```

- 完全相同的 hash 才視為重複;保留 `id` 字典序最小者(deterministic)。
- 移除數量以 `exact_duplicates_removed` 報告。

**為什麼移除模糊去重**:原設計的 Jaccard ≥ 0.80 near-duplicate clustering 會把
「同一區域的兩個**不同**缺陷」誤併為一個。被併掉的那個若是 unsupported finding,
precision 分母就少了一個錯誤 —— **模糊去重會人工抬高 precision**。一個可能提升
headline 分數的啟發式,不該出現在 primary scoring 路徑上。

**Jaccard near-duplicate clustering 的允許用途**(v0.1 可完全不實作):

- 只能作 diagnostics 輸出
- **不得**刪除任何 finding
- **不得**改變任何 headline metric
- **不得**影響 precision 分母

規則凍結於 `adjudication_protocol_version`,變更即 bump。

### 8.2 Stage A — Deterministic candidate matcher

```
candidate(f, b) ⟺ ∃ loc ∈ ({b.localization.primary} ∪ b.localization.acceptable_alternates) :
        f.file == loc.file
    AND [f.line_start, f.line_end] 與 [loc.line_start - tol, loc.line_end + tol] 重疊
    AND f.category == b.category
        (tol = b.localization.line_tolerance)
```

完全 deterministic,無人工、無模型。

**命名紀律**:此階段的產出只能稱為 `localization_recall`。**不得**命名為 bug recall、
correctness recall 或任何暗示語義正確性的名稱 —— 它只證明 localization。

### 8.3 Stage B — Blinded human adjudication(headline 來源)

僅對 Stage A 產生的 candidate pair 進行。

**Adjudicator 看得到**:
- fixture 語言
- mutated tree 中該 localization 視窗(±tolerance)的原始碼節錄
- bug 的 `canonical_claim` 與 `canonical_root_cause`
- finding 的 `claim`、`root_cause`、`evidence`

**裁決必須同時回答三個問題**,三者皆為 yes 才可判 `same_root_cause`:

1. `claim` 是否描述**同一個缺陷**?
2. `root_cause` 是否**相同的機制**?
3. `evidence` 是否**受到所顯示的程式碼支撐**?

第 3 點是硬性條件:只要 evidence 明顯錯誤、捏造(引用不存在的程式碼、函式或行為),
或與該位置無關,**一律不得**判為 `same_root_cause`,即使 claim 與 root_cause 都正確。
理由:一個猜對結論但編造證據的 agent,對真實 code review 沒有價值,而且無法被信任。
`evidence` 已納入 `finding_hash`(§6.5),所以更換證據會產生新的裁決 key,不會沿用舊裁決。

**Adjudicator 看不到**(由匯出工具強制移除):
- provider、model、agent adapter 名稱與版本
- budget、cost、token 數、latency
- run_id、trace 內容
- 同一 run 的其他 finding 的裁決結果
- bug_id 與 finding_hash(以匯出批次內的隨機序號取代,匯入時再對回)

**決策**:`same_root_cause` / `different_root_cause` / `insufficient`。
`insufficient` **保守計為未驗證**。

**Ledger 契約**:
- key = `(fixture_version, bug_id, finding_hash)`
- append-only,commit 進版本控制
- 凍結後,evaluator replay 必須 **bit-identical**
- **Fail-closed**:若存在任何 candidate pair 在 ledger 中查無條目,evaluator
  **拒絕輸出** 任何 `verified_*` headline 指標,改以 `unadjudicated_pairs = N` 回報並
  以非零 exit code 結束。不得以部分裁決計算 headline。

### 8.3.1 正式 ledger 與 synthetic test ledger 的分離(硬性)

存在**兩個互不相通**的 ledger:

| | 正式 ledger | Synthetic test ledger |
|---|---|---|
| 路徑 | `ledger/adjudications.jsonl` | `tests/evaluator/fixtures/synthetic_adjudications.jsonl` |
| 內容 | **只有真人實際裁決** | 為驗證 evaluator 數學而人工編造的決策 |
| `adjudicator_id` | 真人假名(如 `A1`) | **必須**以 `SYNTHETIC-` 為前綴 |
| v0.1 狀態 | **保持空白** | 供 CI / golden tests 使用 |
| 可否產生公開 metric | 是(但 v0.1 無資料) | **否** |

**本輪(以及任何 AI session)不得代替真人填寫正式 ledger。** 具體禁止:

- 不得產生任何真人 `adjudicator_id`(如 `A1`)的決策
- 不得把 synthetic decision 說成人工裁決
- AI agent / LLM 不得冒充 human adjudicator
- `runs/` 不得提交任何以冒充真人裁決計分的 run

Evaluator 必須以**程式強制**這條界線:載入正式 ledger 時,任何
`adjudicator_id` 以 `SYNTHETIC-` 開頭的條目一律拒絕(fail-closed);
以 synthetic ledger 計算的 `results.json` 必須帶
`"ledger_kind": "synthetic"` 且 `"publishable": false`。

**Deterministic fake baseline 的 E2E 指標使用 synthetic test ledger**,其用途僅為驗證
evaluator 的數學與資料流。它**不是** benchmark model result,任何文件都不得如此呈現。

### 8.3.2 Adjudicator 獨立性(v0.1 的明確限制)

v0.1 接受 fixture 作者本人作為裁決者,但受下列限制約束:

- 只能用於 **methodology validation**。
- Data card **必須**揭露 fixture author 與 adjudicator 為同一人。
- v0.1 **不發布**任何真實 provider / model 比較或排名。
- 首次發布真實模型比較 headline **之前**,必須加入第二位獨立人類裁決者,以及明確的
  disagreement resolution protocol。

### 8.4 一對一約束與 compound defect

預設一個 finding 至多驗證一個 bug。若某 finding 對多個 bug 皆為 `same_root_cause`:

- 若這些 bug 共享同一個非 null `compound_group` ⇒ 全部計為已驗證(manifest 明確允許)。
- 否則 ⇒ 以 deterministic 規則指派給**單一** bug:選 localization 重疊區間最大者;
  平手時選 `bug_id` 字典序最小者。其餘 bug 視為未被此 finding 匹配。

反向亦然:一個 bug 可被多個 finding 驗證,但在 recall 分子中只計一次。

### 8.5 Metrics 定義與分母

令某次 run(單一 fixture、單一 snapshot):

- `B` = 該 `fixture_version` 的 bug 集合 —— **recall 的分母**
- `F` = exact-collapse 後的 finding 集合(§8.1),**含** 落在 `out_of_scope_paths` 者
- `V` = `{ b ∈ B : ∃ f ∈ F, ledger(f,b) = same_root_cause }`(套用 §8.4 指派後)
- `M` = `{ f ∈ F : f 被指派驗證了某個 b ∈ B }`
- `F_scored` = `F` —— **precision 的分母**(v0.1 無 residual exclusion,見 §6.8)

`M ⊆ F`。v0.1 **不存在** `R` 集合。

| 指標 | 定義 | 適用 snapshot |
|---|---|---|
| `localization_recall` | `|{b ∈ B : ∃f ∈ F, candidate(f,b)}| / |B|` | mutated |
| `verified_bug_recall` | `|V| / |B|` | mutated |
| `verified_finding_precision` | `|M| / |F|` | mutated |
| `unsupported_findings` | `|F \ M|` | mutated 與 clean |
| `benchmark_unsupported_findings_per_kloc` | `unsupported_findings / (scope.in_scope_loc / 1000)` | **clean control 為 headline 來源** |
| `cost_per_verified_bug` | `estimated_cost_usd / |V|` | mutated |
| `tokens_per_verified_bug` | `(input_tokens + output_tokens) / |V|` | mutated |
| `out_of_scope_findings` | 落在 `out_of_scope_paths` 的 finding 數 | 兩者 |
| `exact_duplicates_removed` | §8.1 移除數 | 兩者 |

Clean control 上 `B = ∅`,因此 `M = ∅`、`unsupported_findings = |F|` —— 任何 finding
都是 unsupported,這正是該指標的意義。

**分母規則**:
- `|V| = 0` 時,`cost_per_verified_bug` 與 `tokens_per_verified_bug` 一律輸出
  `null` 並附 `"reason": "no_verified_bugs"`。**不得**輸出 `0`、`Infinity` 或省略欄位。
- `|F| = 0` 時,`verified_finding_precision` 輸出 `null` 附 `"reason": "no_findings"`。
- `|B| = 0`(clean control)時,`verified_bug_recall` 與 `localization_recall` 輸出
  `null` 附 `"reason": "no_bugs_in_snapshot"`。
- Out-of-scope findings **計入** precision 分母。理由:對 reviewer 而言,離題雜訊仍是成本。
  同時獨立報告 `out_of_scope_findings`,使此決策透明可討論。

### 8.6 Headline 指標清單(固定命名)

```
verified_bug_recall
verified_finding_precision
benchmark_unsupported_findings_per_kloc
localization_recall
cost_per_verified_bug
tokens_per_verified_bug
```

**文件與 README 必須明示**:`verified_*` 指標包含**凍結的人工作業**
(blinded human adjudication),不是完全自動生成。凍結後可重播,但首次產生需要人工判定。

### 8.7 Stage C — LLM-as-judge(secondary only)

- 只作 sensitivity analysis,回答「若改用 LLM 裁決,結論會偏移多少」。
- 輸出至獨立檔案 `runs/<run_id>/llm_sensitivity.json`,**不進** `results.json`。
- 任何公開表格中出現時必須標註為 secondary,且不得與 headline 並列於同一欄。
- v0.1 **不實作**;僅在 spec 中固定其邊界,避免日後被順手混入 primary。

### 8.8 False-positive 用語紀律

- clean control 的指標只能稱 `benchmark_unsupported_findings_per_kloc`。
- **不得**在缺乏完整 ground truth 的第三方 repository 上稱之為
  "real-world false-positive rate"。
- 第三方 historical cohort(v0.2+)只用於 contamination-sensitive comparison,
  **不**產生 clean-control false-positive headline。

---

## 9. Sandbox

### 9.1 三階段

| Phase | 網路 | 產出 |
|---|---|---|
| **prepare** | 容器內**開啟**(需下載安裝依賴) | immutable prepared image + digest + `env.lock.json` + environment fingerprint |
| **measure** | **tool container `--network none`**;host 端 provider API 連線**不受影響** | run 的 raw evidence + public trace |
| **evaluate** | 無容器,純本機讀檔 | `results.json` |

> **澄清**:`measure --network none` 指的是 **fixture tool container 不連網**。
> Agent 的 LLM 呼叫由 host 端 harness 發出,host 網路正常。這條分界讓「被分析的程式碼」
> 無法對外通訊,同時不妨礙 agent 推理。

### 9.2 Measure profile 的設計要求

- 非 root 使用者
- `--cap-drop ALL`
- `--security-opt no-new-privileges`
- root filesystem 唯讀(`--read-only`)
- 可寫區僅限:`/tmp`(tmpfs,size 上限)與指定的 workspace(anonymous volume)
- `--network none`
- `--pids-limit`、`--memory`、`--cpus` 上限
- 每指令 timeout:容器內 `timeout --signal=KILL` + host 端 subprocess timeout 雙層
- **無 host mount**;若未來需要,只允許明確標示的 read-only fixture input
- image 以 **digest** 指定,不以 tag
- 每次 run 記錄 environment fingerprint

### 9.3 可重建性的誠實表述

**不承諾** apt package 版本永久可重建。可重建性以三個分離的東西表示:

1. **prepared image digest** —— 唯一具有 bit-level 保證的錨點,量測一律以它為準
2. **lock manifest**(`env/env.lock.json`)—— 語言層依賴的精確版本
3. **rebuild recipe**(`env/Dockerfile`)—— 盡力重建的配方,**不保證** byte-identical

文件必須明說:若 prepared image 遺失,重建結果可能與原始 digest 不同,此時該批結果
必須標記為不同的 `environment.fingerprint`,不可與舊結果直接比較。

### 9.4 Environment fingerprint

```
fingerprint = sha256( canonical_json({
    base_image_digest, prepared_image_digest,
    os_release_id, os_release_version_id,
    primary_runtime_version,          # python -V / node -v
    package_manager_version,
    lock_manifest_sha256,
    arch                              # 例如 linux/amd64
}) )
```

### 9.5 驗證狀態

**設計期間 Docker Desktop 未啟動。** §9.2 全部條目目前是 **design requirements**,
不得在任何文件中寫成已驗證事實。實作計畫的 **Task H2** 專責把它們變成有測試背書的事實。

---

## 10. Trace、privacy 與 license 邊界

### 10.1 兩層 artifact

| | Private raw evidence | Public sanitized trace |
|---|---|---|
| 位置 | `.run-store/`(gitignored) | `runs/<run_id>/`(committed) |
| 內容 | 完整 tool I/O、完整 LLM request/response | 允許清單投影 |
| 形式 | append-only JSONL + content-addressed blob store | append-only JSONL |
| 可能含 | 第三方程式碼、host 路徑、敏感資訊 | 以上皆不得含 |
| 去向 | **永不**進 GitHub / HF / Release artifact | 公開 artifact |
| Retention | 預設 30 天;`cae store prune` 清理;policy 記於 `.run-store/RETENTION.md` | 永久 |

### 10.2 Raw event schema 的三分類與 public 允許清單

Raw event schema **明確地**把每個欄位歸入三類之一。這是 §10.5 fail-closed 規則能夠成立的
前提 —— 沒有這個分類,「未知欄位」就無從定義。

| 類別 | 進 public projection? | Sanitizer 行為 |
|---|---|---|
| **public-allowlisted** | 是 | 投影到 public trace |
| **known private-only** | 否 | **安全丟棄**,不觸發拒絕 |
| **unknown**(不在 raw schema 中) | — | **拒絕整份 artifact**,fail-closed |

「known private-only」是明確登記過、經審查判定不可公開的欄位(例如完整 tool output
內容、provider 原始 response body)。它們被丟棄是**設計意圖**。

「unknown」代表有人在 raw schema 新增了欄位卻沒有分類它 —— 這種欄位**必須**讓 sanitizer
整份拒絕。若靜默忽略,新加入但未經審查的欄位就可能悄悄夾帶敏感資訊,或悄悄從公開紀錄中
消失。兩種失敗都不可接受。

以下是 **public-allowlisted** 清單。不在此清單、且未登記為 known private-only 的欄位
即為 unknown。

**共同**:`schema_version`、`seq`、`ts`、`event`

**`run_header`**:`run_id`、`benchmark_version`、`fixture_id`、`fixture_version`、
`fixture_tree_checksum`、`snapshot`、`bug_set_hash`、`agent_adapter`、`agent_adapter_version`、
`provider`、`model`、`prompt_hash`、`system_prompt_version`、`params_hash`、`seed`、
`image_digest`、`env_fingerprint`、`sandbox_profile`、
`budget.{max_tokens, max_tool_calls, max_wallclock_seconds, max_estimated_cost_usd}`、
`redaction_manifest_version`

**`llm_call`**:`request_hash`、`latency_ms`、`finish_reason`、
`usage.{input_tokens, cached_input_tokens, output_tokens, reasoning_tokens,
provider_raw_usage_normalized}`

**`tool_call`**:`tool_name`、`args_safe`(每工具一份參數允許清單 + 值域限制)、`args_hash`

**`tool_result`**:`is_error`、`content_sha256`、`content_bytes`、`excerpt`、`excerpt_policy`

**`context_compression`**:`strategy_version`、`pre_view_hash`、`post_view_hash`、
`raw_content_sha256[]`、`replaced_count`

**`findings_submitted`**:完整 finding 物件(first-party 結構化資料,全量保留 —— 這是
replay 能在不重建第三方 tool output 的情況下重現評分的關鍵)

**`termination`**:`reason`、`steps`、`tool_calls`、`wall_clock_ms`

**`cost`**:`estimated_cost_usd`、`completeness`、`unknown_fields[]`、`pricing_table_version`、
`pricing_effective_date`、`pricing_source`、`estimator_limitations`

### 10.3 Excerpt policy

| 內容來源 | 允許 |
|---|---|
| 本 harness 自產字串(工具層錯誤訊息等) | 全文,上限 2,000 bytes |
| **first-party** fixture 內容 | ≤ 400 bytes excerpt |
| **第三方 / upstream** fixture 內容(v0.2+) | 一律 `"<redacted>"`,只保留 `content_sha256` 與 `content_bytes` |

`excerpt_policy` 欄位記錄本筆採用哪一條,使稽核者不需猜測。

### 10.4 Context compression 邊界

- Compression **只產生 model-facing view**。
- **不得**改寫 raw evidence。`.run-store/` 中的原始內容永遠完整。
- Trace 必須記錄:壓縮前內容 hash、壓縮策略版本、壓縮後 view hash。

### 10.5 Sanitizer:fail-closed

Sanitizer 在偵測到下列任一項時 **拒絕輸出整份 artifact**:

- exit code 非零
- public output path **不存在**
- **不留下 partial file**(先寫暫存檔,全部檢查通過才 atomic rename)

不做 best-effort 遮蔽後續行。

1. 絕對路徑:Windows(`^[A-Za-z]:[\\/]`、`\\?\`、`\\server\share`)或 <!-- leak-scan-allow: absolute_path (rule definition quoted in the spec it defines) -->
   POSIX(`/home/`、`/Users/`、`/root/`、`/mnt/c/`)
2. OS username token(設定清單 + 執行時偵測到的當前使用者名稱)
3. Email 位址 —— **public trace sanitizer 拒絕全部 email,無 allowlist**(見 §10.8)
4. Token / private key 樣式:`AIza…`、`sk-…`、`gh[pousr]_…`、
   `-----BEGIN … PRIVATE KEY-----`、JWT 三段式
5. `.env` 形態內容:連續 ≥2 行符合 `^[A-Z][A-Z0-9_]{2,}=`
6. 超過 §10.3 允許長度的第三方 code excerpt
7. 未知或非 UTF-8 的二進位內容
8. **Unknown 欄位**(§10.2 第三類):不在 raw schema 中的欄位。
   已登記為 known private-only 的欄位則是安全丟棄,**不**觸發拒絕。

Sanitizer 另需 `redaction_manifest_version`,任何規則變更即 bump,並記入 trace header。

### 10.8 兩個掃描器,兩套 email 政策

Pattern 定義單一真相來源(`hygiene/patterns.py`),但**套用政策依情境不同**:

| | Public trace sanitizer | Tracked-file leak scanner(gate G11) |
|---|---|---|
| 掃描對象 | run artifact | 版本控制中的檔案 |
| Email 政策 | **拒絕全部 email,無例外** | 允許**唯一一個** exact literal:`61350295+kuotunyu@users.noreply.github.com` |
| 其他 email | 拒絕 | 拒絕 |

**為什麼需要分歧**:正式公開身分必然出現在 tracked 檔案中(commit metadata、
文件中的規則條文)。若 tracked scanner 一律拒絕 email,它會拒絕 repository 自己 ——
規則自相矛盾就會被繞過,那比沒有規則更糟。而 run artifact 沒有任何理由包含 email,
所以那裡維持零容忍。

Allowlist 規則:

- **只能** exact literal 比對,不得用 regex 或 domain 比對
- 必須放在明確的 **versioned hygiene policy**(`hygiene/policy.py`,帶
  `hygiene_policy_version`)
- 相似拼字(如 `61350295+kuotunyu@users.noreply.github.com.evil.com`、 <!-- leak-scan-allow: email (rule definition quoted in the spec it defines) -->
  `kuotunyu@users.noreply.github.com`)、舊 Gmail 位址、任何其他 email 一律拒絕 <!-- leak-scan-allow: email (rule definition quoted in the spec it defines) -->
- 同一個正式 email 若出現在 **public trace sanitizer** 的輸入中,**仍然被拒絕**

### 10.6 Replay 契約

```
cae evaluate replay runs/<run_id>
```

必須僅以 **public sanitized trace + fixture manifests + 凍結 ledger** 重現
`results.json`,且與原始輸出 **byte-identical**。這是公開 artifact 充分性的證明,
也是 CI gate **G6**。

### 10.7 舊專案素材的處理

舊 EVAL_REPORT 的 incident 分析 **不得原文搬入**。只能依已閱讀的內容**重新撰寫**為
sanitized lessons,且不得帶入舊 trace 檔、絕對路徑、email、第三方原始碼或任何
Git metadata。

**v0.1 不產出任何 lessons 文件** —— 它不在 §2.1 的 scope 內。此規則在此固定,是為了在
v0.2 撰寫時已有明確約束,而不是留下待辦事項。任何未來的 lessons 文件與其他 tracked
檔案一樣受 gate G11 檢查。

---

## 11. Cost accounting

### 11.1 每次 run 必須保存

| 欄位 | 說明 |
|---|---|
| `input_tokens` | |
| `cached_input_tokens` | provider 未提供時為 `null` |
| `output_tokens` | |
| `reasoning_tokens` | provider 未提供時為 `null` |
| `provider_raw_usage_normalized` | provider 原始 usage payload 的 sanitized normalized mapping(保留原始欄位名 → 值) |
| `pricing_table_version` | |
| `pricing_effective_date` | |
| `pricing_source` | 官方定價頁 URL |
| `estimator_limitations` | 字串陣列,列出已知偏差 |

### 11.2 `unknown` 語意

- Provider 未回報 reasoning token、cache token 或其他計價相關欄位時,該欄位為
  `null`,並在 `unknown_fields[]` 列名。
- **絕不**以 `0` 代替 `unknown`。
- `estimated_cost_usd.completeness ∈ {complete, partial}`;任一計價成分為 unknown
  即為 `partial`。

### 11.3 命名紀律

美元金額一律命名為 **`estimated_cost_usd`**,不得使用 `cost_usd`。
文件必須明說:**不同 provider 的美元成本不是完全 apples-to-apples**
(cache 計價、reasoning token 計價、分層定價各家不同)。

### 11.4 Budget 的四個維度

比較時同時保留,不得只用美元:

```
budget.max_tokens                  # provider-native token budget
budget.max_tool_calls
budget.max_wallclock_seconds
budget.max_estimated_cost_usd      # 上限,非事實成本
```

---

## 12. Contamination 用語

### 12.1 允許用語

- `novel, privately authored mutations at benchmark creation time`
- `contamination-resistant`
- `lower contamination risk than public historical fixes`

### 12.2 禁止用語

- 「injected bug 的 contamination risk 是 zero」或任何等價說法。

### 12.3 必須揭露

- `benchmark_version` 與 **cutoff date**(每個 bug 的 `authored_at`,以及 fixture 的
  `authored_at`)。
- 明示:公開之後,injected bugs 未來也可能進入模型訓練資料,因此
  contamination-resistance 隨時間衰減。

### 12.4 Historical cohort 規則(v0.2+,此處先固定契約)

Historical fixture 必須:

- 只提供 **buggy tree snapshot**
- **不含** fix commit object
- **不含** future history(不得夾帶 `.git` 或任何後續 ref)
- **不含** issue / PR / changelog / 測試名稱等答案洩漏
- 結果 **分開報告**,不與 injected headline 混成單一平均值

---

## 13. Error handling

### 13.1 Termination reason enum

| 值 | 說明 | 計分處理 |
|---|---|---|
| `completed` | agent 主動收尾且已提交 findings | 正常計分 |
| `partial` | 已提交 findings 但預算/步數在收尾前耗盡 | 正常計分,標記 `partial=true` |
| `budget_exhausted_tokens` | | 無 findings ⇒ recall 0,precision `null` |
| `budget_exhausted_cost` | | 同上 |
| `budget_exhausted_wallclock` | | 同上 |
| `step_exhausted` | 超過 `max_tool_calls` | 同上 |
| `loop_detected` | | 同上 |
| `no_output` | 從未呼叫 `write_findings` | 同上 |
| `adapter_error` | agent adapter 內部錯誤 | **排除於彙總指標之外**,計入 `n_invalid` |
| `provider_error` | provider 不可恢復錯誤 | 同上 |
| `sandbox_error` | 容器啟動/執行失敗 | 同上 |
| `harness_error` | harness 自身 bug | 同上 |

### 13.2 彙總紀律

- 任何彙總表必須同時呈現 `n_valid` 與 `n_invalid`。
- **不得**把 invalid run 當作 0 分平均進去,也不得靜默丟棄。
- 某個 (fixture, model) 儲存格若含 invalid run,必須重跑或標記為 incomplete。

### 13.3 工具層錯誤

- **預期內失敗**(檔案不存在、路徑逃逸、參數不符)→ 訊息回饋給 agent,不中斷 run。
- **非預期例外** → 同樣回饋,但 trace 記錄完整型別;連續 3 次非預期例外 ⇒
  `harness_error` 終止(避免用壞掉的 harness 產生看似有效的分數)。

### 13.4 Evaluator 的 fail-closed 點

1. 任何 candidate pair 缺 ledger 條目 ⇒ 拒絕輸出 `verified_*`
2. `fixture_version` 與 trace 記錄不符 ⇒ 拒絕評分
3. `tree_checksum` 與 manifest 不符 ⇒ 拒絕評分
4. Ledger `entry_hash` 驗證失敗 ⇒ 拒絕評分
5. `trace_schema_version` 不受支援 ⇒ 拒絕評分

---

## 14. Testing 與 release gates

全部 gate 在 **CPU + Docker、無 API key、無 GPU** 下執行。

| Gate | 名稱 | 內容 | 需 Docker |
|---|---|---|---|
| **G1** | schema validation | 所有 fixture / bug / ledger / results manifest 通過 JSON Schema | ✗ |
| **G2** | witness contract | 每個 bug:clean 契約通過 → `git apply --check` 成功 → 套用後 mutated 契約符合預期 → `git apply -R` 還原 → clean 契約再次通過 | ✔ |
| **G3** | fixture rebuild determinism | 重建 `tree/` 後 `tree_checksum` 相同;以 `cae-loc` 重數 `in_scope_loc` 並斷言與 manifest 相符(防止 headline 分母無聲漂移) | ✗ |
| **G4** | answer-leak | measured tree 不得含 `bugs/`、`*.patch`、`witness/`、`defects.md`、`known_residual_defects.yaml`、任何 `bug_id`、任何 canonical 文字的 5-gram | ✗ |
| **G5** | sanitizer fail-closed | 毒化輸入語料(每條 §10.5 規則至少 2 例)全部被拒 | ✗ |
| **G6** | replay determinism | 黃金 public trace → `results.json` byte-identical | ✗ |
| **G7** | matcher correctness | 人工撰寫的合成 finding 集,斷言已知的 `localization_recall` / precision 數值 | ✗ |
| **G8** | evaluator fail-closed | §13.4 五個條件各有測試,皆須非零 exit | ✗ |
| **G9** | deterministic baseline E2E | fake agent 端到端跑完 2 fixture × 2 snapshot,產出已知指標值 | ✔ |
| **G10** | lint / type / unit | ruff + mypy + pytest 全綠 | ✗ |
| **G11** | tracked-file leak scan | 對**已納入版本控制**的檔案套用 §10.5 規則集,搭配 §10.8 的 tracked-file email allowlist;任一命中即失敗 | ✗ |

`G2` 是整個設計的核心 gate:它讓「評估標準本身是對的」成為 CI 每次驗證的事實。
`G11` 與 sanitizer 共用同一份 pattern 定義(單一真相來源),政策差異見 §10.8。

**Release 條件**:G1–G11 全綠,且正式 ledger 無 synthetic 條目。
v0.1 的正式 ledger 為空,因此**不產生任何可發布的 `verified_*` 數字**;
committed E2E 結果一律為 synthetic evaluator validation。

### 14.1 CI 平台矩陣

**非 Docker quality CI —— 雙平台**(`ubuntu-latest` 與 `windows-latest`),兩者皆執行:

- locked install
- `ruff check`
- `ruff format --check`
- `mypy`(strict)
- 非 Docker pytest
- schema validation(G1)
- tracked-file leak scan(G11)
- replay golden tests(G6)

雙平台是必要的:tree checksum、行尾處理與路徑語意都是跨平台敏感的,而開發在 Windows、
CI 在 Linux —— 只測一邊等於不測。

**Docker jobs —— 僅 Linux**:witness gates(G2)、sandbox observed-behaviour(H2)、
deterministic baseline E2E(G9)。

**Workflow 硬性要求**:Actions 以 commit SHA pin、最小 `permissions`、
`checkout` 設 `persist-credentials: false`。

### 14.2 Release verification

除 G1–G11 外,發布前另需:

- package build(wheel + sdist)
- wheel 於**隔離環境** install 後 smoke test
- sdist 內容稽核
- 從 **clean committed export**(`git archive`)安裝並跑測試
- public 檔案不得含:raw store、cache、絕對路徑、舊使用者名稱、舊 email、私人 history、
  超過門檻的大檔
- README / cards / metrics 文件的連結檢查
- Git author / committer / trailer 稽核

**Claims discipline**:hosted CI 尚未實際執行時,只能陳述
「workflow 已建立」或「local equivalent passed」,**不得**宣稱 GitHub CI green。

---

## 15. Versioning

| 版本欄位 | 語意 | bump 規則 |
|---|---|---|
| `benchmark_version` | 資料集 + 協定整體 | 新增 fixture/bug ⇒ minor;變更 matching 或 metric 定義 ⇒ major |
| `fixture_version` | 單一 fixture | `tree/`、bug、patch、witness 任何變更即 bump |
| `adjudication_protocol_version` | 裁決協定(含 `finding_hash` 欄位集、dedup 規則、blinding 規則) | 任何規則變更即 bump;**bump 後既有 ledger 條目不再適用** |
| `trace_schema_version` | public trace 欄位契約 | |
| `pricing_table_version` | 定價表 | |
| `redaction_manifest_version` | sanitizer 規則 | |

`results.json` 必須同時攜帶全部六個版本欄位。跨版本比較時,任一版本不同即需在報告中標註。

---

## 16. Roadmap(不屬於 v0.1 implementation plan)

### v0.2

- Permissive-license upstream **injected** snapshots(MIT/BSD/Apache-2.0)
- Historical bug cohort(依 §12.4 契約),分開報告
- Poisoned fixtures(prompt injection / tool-output poisoning),以 deterministic canary
  判定,非 LLM 判斷
- Partial / timeout fixtures
- Bug 數擴充至約 35,達成 v1.0 的 ~70% injected / ~30% historical 比例
- Anthropic adapter
- LLM-as-judge sensitivity analysis 實作(§8.7)

### v1.0

- 完整 5 類 taxonomy 均衡覆蓋
- 多 model × 多 budget sweep 與 baseline 凍結
- Regression gate(recall/precision 相對 baseline 的門檻)
- MCP server 作為 optional extra 重新引入(仍不進主線敘事)
- 公開發佈流程(remote / release artifact),含完整 sanitizer 稽核

---

## 17. 已知限制(必須寫進公開文件)

1. `verified_*` 指標包含凍結的人工裁決,不是完全自動生成。
2. **Adjudicator 獨立性有限**。§8.3 的 blinding 移除 provider / model / budget,消除品牌偏差;
   但若裁決者同時是 fixture 作者,他知道每個 bug 的正確答案,無法對「這個 finding 是否
   真的描述了該 root cause」保持完全中立。v0.1 接受此限制並要求:
   (a) `adjudicator_id` 與 fixture 作者身分的關係必須在 data card 中揭露;
   (b) 每筆裁決都必須留下 `rationale`,使第三方可事後複查;
   (c) ledger 公開且 append-only,任何人可對特定裁決提出異議。
   完全獨立的第二裁決者與 disagreement resolution protocol 是發布真實模型比較的
   **前置條件**(§8.3.2),列為 v0.2。
3. **v0.1 沒有任何真實模型結果**。正式 ledger 為空,無 live provider run,
   committed 的 E2E 數字全部來自 deterministic fake baseline 搭配 synthetic test ledger,
   目的是驗證 evaluator 的數學與資料流,**不是**模型能力量測。
4. **v0.1 不支援非空 residual defects**(§6.8)。clean fixture 必須真的乾淨,
   發現疑似缺陷時走修正流程而非登記豁免。
5. **v0.1 不做模糊去重**(§8.1)。同一位置的多個相異 finding 各自計入 precision 分母。
6. v0.1 只有 2 個 fixture、8 個 bug,統計解析度低,**不足以排名模型**;它的用途是
   驗證方法論端到端可行。
7. First-party fixture 的真實性有上限:它們是為量測而寫的服務,不是生產系統。
8. Contamination-resistance 隨公開時間衰減。
9. 跨 provider 的美元成本不是完全可比。
10. Clean control 的 `benchmark_unsupported_findings_per_kloc` 依賴 `defects.md` 的
   完整性;完整性主張只在本規模(≤3,000 LOC)下成立。
11. Sandbox 隔離性質在 v0.1 完成前皆為 design requirement。
