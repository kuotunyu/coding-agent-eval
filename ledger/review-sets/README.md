# Dual blinded human adjudication review sets

本目錄保存 current-contract run 的公開 review-set evidence。每個 review set 綁定單一
`run_id`、完整 trace bytes、findings、fixture manifest、candidate set 與 environment
fingerprint；任一輸入改變都必須建立新的 review set，舊裁決不會自動沿用。

## 不可跨越的 human-only boundary

AI 與 scripts 只可以初始化、blind、shuffle、hash、驗證及匯入 worksheet 格式。正式
`DECISION` 與 `RATIONALE` **只能由指定的 human reviewer 親自填寫**。AI 不得建議、補寫、
改寫、推測或代填任何正式裁決，也不得冒用 reviewer identity。

Primary 與 independent reviewer 必須各自獨立作業，不得查看對方的 worksheet、答案或
private keymap。Independent reviewer 必須不是 fixture author、run operator 或 primary
reviewer。只有兩者意見不一致的項目可以交給第三位 resolver；resolver 也必須與上述所有
角色不同。

## Directory contract

每個可公開的 review set 使用以下結構：

```text
<review_set_id>/
  manifest.json
  candidates.json
  primary.jsonl
  independent.jsonl
  resolutions.jsonl
```

`manifest.json` 是 closed-world identity contract；三個 JSONL 是 formal human rulings。
`candidates.json` 保存已 blind 的 candidate materials，不含 reviewer 答案。Worksheet 與
`*.keymap.json` 是 local working files，不屬於 review set，也不得 commit、push、放入 GitHub
Release、OCI artifact 或 Zenodo deposit。Repository 的 `.gitignore` 已排除 `*.keymap.json`。
Manifest 會綁定 candidate materials hash，private keymap 也會綁定 worksheet 的唯讀內容；
除了 `DECISION` 與 `RATIONALE` 以外的文字一旦改變，import 必須拒絕寫入。

## Workflow

先從 immutable trace 與 fixture 建立空白 review set；此步驟不會產生任何 ruling：

```powershell
uv run cae evaluate init --trace TRACE --bugs BUGS --fixture FIXTURE_DIR `
  --review-set REVIEW_SET_DIR --fixture-author-id FIXTURE_AUTHOR `
  --run-operator-id RUN_OPERATOR --primary-id PRIMARY `
  --independent-id INDEPENDENT
```

分別輸出兩份具有不同 deterministic shuffle、且綁定各自 slot 的 worksheet 與 private
keymap：

```powershell
uv run cae evaluate export --review-set REVIEW_SET_DIR --slot primary `
  --worksheet PRIMARY.txt --keymap PRIMARY.keymap.json
uv run cae evaluate export --review-set REVIEW_SET_DIR --slot independent `
  --worksheet INDEPENDENT.txt --keymap INDEPENDENT.keymap.json
```

兩位 human 各自填完全部 `DECISION` 與單行 `RATIONALE` 後，才可以匯入。Keymap 不能跨
slot 使用；缺項、格式錯誤、identity 或 hash 不符時，import 會 fail closed，不寫入部分結果：

```powershell
uv run cae evaluate import --review-set REVIEW_SET_DIR --slot primary `
  --worksheet PRIMARY.txt --keymap PRIMARY.keymap.json
uv run cae evaluate import --review-set REVIEW_SET_DIR --slot independent `
  --worksheet INDEPENDENT.txt --keymap INDEPENDENT.keymap.json
```

若有 disagreement，只輸出不一致項目給 distinct human resolver：

```powershell
uv run cae evaluate resolve-export --review-set REVIEW_SET_DIR `
  --worksheet RESOLVER.txt --keymap RESOLVER.keymap.json
uv run cae evaluate resolve-import --review-set REVIEW_SET_DIR --resolver-id RESOLVER `
  --worksheet RESOLVER.txt --keymap RESOLVER.keymap.json
```

工具建立 review set 不代表 evidence 已完成。只有 primary 與 independent 達到 100%
coverage、全部 disagreement 由符合獨立性條件的 resolver 解決，而且所有 identity/hash/schema
驗證都通過時，該 review set 才能成為 `dual_review_complete` publication evidence。
