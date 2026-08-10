# 正體中文發布介面 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立正體中文為主的 GitHub 發布介面，並準備只有 `kuotunyu` 的單一提交 release lineage。

**Architecture:** 原 repository 繼續保存完整歷史與 working tree；發布用 repository 由核准後的 source snapshot 重新初始化，不攜帶舊 commit graph。README 與 GitHub About 共用相同定位，但 README 保留完整 evidence 與限制，About 僅提供精簡摘要。

**Tech Stack:** Markdown、Git、GitHub repository settings、既有 `uv`／`cae` 驗證工具。

## Global Constraints

- 以臺灣正體中文為主，專有名詞、CLI、schema 與 metric 名稱保留原文。
- 不新增 benchmark 功能，不提高 v0.1 的 publication claim。
- 不改寫目前 repository history，不遺失任何既有使用者修改。
- 不 push、不建立 GitHub Release、不上傳 Zenodo，直到使用者明確授權。
- remote URL 固定為 `https://github.com/kuotunyu/coding-agent-eval.git`。

---

### Task 1: 正體中文 README

**Files:**
- Modify: `README.md`
- Modify: `docs/RELEASE_READINESS.md`
- Modify: `docs/HANDOFF.md`

**Interfaces:**
- Consumes: `docs/RELEASE_READINESS.md`、fixture manifests、committed run evidence。
- Produces: GitHub 首頁的正體中文主文件與簡短 English summary。

- [ ] **Step 1: 保存既有 claim inventory**

  核對 README 的版本、fixture 數量、bug 數量、LOC、test 數、live run 成本、reasoning ratios、publication blockers 與驗證命令。

- [ ] **Step 2: 改寫為正體中文主版**

  保留 `unsupported findings`、`localization_recall`、`verified_*`、`sandbox`、`Docker`、`Zenodo` 等原文，修正既有亂碼與不正確 CLI 範例。

- [ ] **Step 3: 加入 English summary**

  只摘要用途、v0.1 methodology preview 定位與禁止排行榜結論，不平行複製整份 README。

- [ ] **Step 4: 對齊 lineage 文件**

  將舊開發 history 與 clean release lineage 明確分開；公開文件不得把未匯入的 co-author trailers
  誤寫成 release repository 的目前狀態。

- [ ] **Step 5: 驗證文件內容**

  Run: `rg -n "\?\?|�|cae validate fixtures|measure_container:sha256:\?" README.md`

  Expected: 找不到結果。

### Task 2: 重新產生 release evidence 並驗證

**Files:**
- Modify: `release-manifest.json`

**Interfaces:**
- Consumes: 完成後的 README 與現有 release artifacts。
- Produces: 與目前 working tree 一致的 SHA-256 manifest。

- [ ] **Step 1: 重新產生 manifest**

  Run: `uv run python scripts/build_release_manifest.py`

- [ ] **Step 2: 執行靜態 gates**

  Run: `uv run cae validate`

  Run: `uv run cae release audit`

  Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src`

- [ ] **Step 3: 執行 non-Docker tests**

  Run: `uv run pytest -m "not docker"`

  Expected: 全部 selected tests 通過；只允許既有環境相關 skip。

### Task 3: 建立 clean single-commit lineage

**Files:**
- Create: clean release working copy outside the historical repository

**Interfaces:**
- Consumes: `git ls-files --cached --others --exclude-standard` 列出的核准 source snapshot。
- Produces: `main` 分支、單一 `kuotunyu` commit，以及尚未 push 的 `origin`。

- [ ] **Step 1: 驗證 remote 為空**

  Run: `git ls-remote https://github.com/kuotunyu/coding-agent-eval.git`

  Expected: 成功且沒有 refs。

- [ ] **Step 2: 建立 clean working copy**

  逐檔複製 tracked 與非 ignored untracked source；不得複製 `.git`、cache、`dist/` 或其他 ignored output。

- [ ] **Step 3: 建立單一提交**

  在新 copy 內執行 `git init --initial-branch=main`，設定 repository-local author/committer 為 `kuotunyu`，加入全部 source 後建立一筆無 co-author trailer 的初始提交。

- [ ] **Step 4: 驗證 provenance 與 gates**

  Run: `git rev-list --count HEAD`

  Expected: `1`

  Run: `uv run cae release audit --check-git-history`

  Expected: 沒有 `git.coauthor` 或 identity blocker；允許 8 筆已揭露的 `trace.legacy` warning。

### Task 4: 設定 GitHub About 並交付 push gate

**Files:**
- External setting: GitHub About description and Topics

**Interfaces:**
- Consumes: 設計文件中的 exact description 與 Topics。
- Produces: 正體中文 repository summary；不包含 source push。

- [ ] **Step 1: 設定 About 與 Topics**

  在 GitHub repository UI 寫入核准內容並讀回確認。

- [ ] **Step 2: 保持 remote repository 無 commits**

  在使用者最終核准 push 前，不執行 `git push`。

- [ ] **Step 3: 提交最終摘要與一次性 push 授權問題**

  報告 clean commit id、驗證結果、About 設定與仍需 human adjudication／OCI distribution 的 publication blockers。
