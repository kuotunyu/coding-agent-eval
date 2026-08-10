# 正體中文發布介面設計

## 目標

讓 `coding-agent-eval` 的 GitHub 首頁以臺灣正體中文為主要閱讀語言，同時保留
`AI coding agent benchmark`、`sandbox`、`baseline agent`、`reproducibility` 等專有名詞原文，
並以乾淨的單一提交 lineage 發布，確保 GitHub Contributors 只顯示 `kuotunyu`。

## 範圍

- 將根目錄 `README.md` 改為正體中文主版。
- 保留指令、schema 欄位、metric 名稱、API 名稱與其他專有名詞原文。
- README 末尾提供簡短的 English summary，供國際讀者與搜尋索引理解專案定位。
- GitHub About 使用正體中文描述，Topics 使用穩定的英文 slug，另含 `zh-tw`。
- 從目前 working tree 建立全新的單一提交 Git lineage；不匯入舊 repository history。

## 不在範圍內

- 不增加 fixture、task、agent adapter、metric 或其他 benchmark 功能。
- 不改變 v0.1 的 methodology preview 定位。
- 不補寫或偽造歷史 trace，也不把 synthetic baseline 宣稱為模型能力結果。
- 不發布 GitHub Release、不上傳 Zenodo、不使用 provider secrets。
- 不改寫、刪除或 reset 目前 repository 的既有 Git history。

## README 資訊架構

1. 專案名稱、正體中文摘要與明確的 v0.1 狀態。
2. 評估內容與不評估內容。
3. v0.1 evidence、限制與 publication blockers。
4. fixture、contamination、provenance 與 acceptance contract。
5. sandbox isolation、驗證指令、目錄結構與文件索引。
6. Licence 與簡短 English summary。

既有的重要數字、限制、版本與 evidence claim 必須保留；翻譯不得將 methodology preview
提升成 benchmark release 或 leaderboard。

## GitHub About

Description：

> 可重現的 AI coding agent benchmark，用於評估已知缺陷發現、unsupported findings、成本、延遲與 sandbox 行為。

Topics：

- `ai-evaluation`
- `llm-evaluation`
- `coding-agents`
- `benchmark`
- `reproducibility`
- `sandbox`
- `zh-tw`

## Clean lineage

新 lineage 必須符合：

- 預設分支為 `main`。
- 首次提交包含目前核准的完整 source snapshot。
- author 與 committer 都必須是
  `kuotunyu <61350295+kuotunyu@users.noreply.github.com>`。
- commit message 不得包含 `Co-Authored-By` trailer。
- 原 repository 與其中的使用者未提交修改保持不被覆蓋或改寫。
- remote 指向 `https://github.com/kuotunyu/coding-agent-eval.git`，但 push 必須等候最終明確授權。

## 驗收

- README 以正體中文為主，沒有簡體中文段落或亂碼。
- README 中的數字與 release-readiness evidence 可由 repository artifacts 追溯。
- `cae validate`、release artifact audit、Ruff、mypy 與 non-Docker tests 通過。
- release manifest 在 README 修改後重新產生且 audit 通過。
- clean lineage 只有一筆 commit，且 immutable provenance audit 沒有 contributor blocker。
- GitHub About description 與 Topics 符合本文件。

