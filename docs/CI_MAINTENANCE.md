# CI maintenance — 2026-09 主線修復紀錄

這份文件記錄 2026-09 main CI 從紅燈恢復到綠燈的根因與流程，供之後遇到同類問題時
比對。CI 定義在 [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)；本機 release
gates 見 [Release Readiness](RELEASE_READINESS.md)。

## 事件摘要

| | |
|---|---|
| 最後一次綠燈 | run 33263746547，commit `bc02669`，2026-08-29 |
| 失敗 | run 33630856926，commit `8fd4bdb`，2026-09-02 |
| 失敗位置 | quality (ubuntu-latest) 與 quality (windows-latest) 都停在 **Format check**；docker gates 通過 |
| 來源 | 2026-08-30 的 14 個 external-agent commits（`bd482ef` … `8fd4bdb`）整批直接推上 main |
| 修復 | PR #2，4 個 commit，merge `7b26275`；main run 34258336555 全綠，2026-09-09 |

quality job 會在第一個失敗步驟停止。Format check 的紅燈把後面 mypy 與 tests 的失敗都
遮住了，所以修完第一層之後又浮現三個，總共四個根因。

## 根因

### 1. Format 漂移（兩個平台）— `6f6ebc0`

- 症狀：`ruff format --check .` 回報 7 個檔案需要重排。
- 原因：external-agent 系列在本機沒有跑 format gate 就整批推上 main。CI 在推送到 main
  之前沒有機會看到這批 commit。
- 修法：以鎖定版 ruff 0.16.1 執行 `uv run ruff format .`。只有排版變動，每個檔案的 AST
  與修改前相同。

### 2. Linux mypy 失敗 — `97ee41b`

- 症狀：ubuntu 的 Type check 回報 `stdio_adapter.py` 兩個 `attr-defined`：
  `subprocess.CREATE_NO_WINDOW` 與 `ctypes.WinDLL`。
- 原因：typeshed 只在 `sys.platform == "win32"` 下宣告這兩個名稱，而 mypy 無法用
  `os.name == "nt"` 做平台收斂。開發在 Windows 上進行，本機 mypy 看不到這個錯誤。
- 修法：兩處 guard 改用 `sys.platform`，並寫成 if 敘述，因為 mypy 不會在三元式裡收斂。
  執行期行為不變；CPython 上 `os.name == "nt"` 與 `sys.platform == "win32"` 等價。
- 本機重現：`uv run mypy --platform linux`。

### 3. Windows broken-pipe 測試失敗 — `7d8ae01`

- 症狀：`tests/test_stdio_live_run.py` 的 broken_pipe 案例得到 `request_write == "complete"`，
  預期 `"partial"`。
- 原因：探針子行程關閉自己的 stdin 來製造 broken pipe，但 CI 的 venv 把 `python.exe`
  做成 launcher 行程：它啟動真正的直譯器作為子行程，並保留一份 stdin pipe 的讀端。
  直譯器關閉 stdin 後 pipe 並未真正斷開，父行程的寫入仍然成功。本機 venv 使用 uv 的
  trampoline，不持有該 handle，所以本機通過。
- 修法：以 `sys._base_executable` 直接啟動探針子行程，使直譯器成為 pipe 唯一的讀端。
  adapter 與預期證據不變。
- 本機重現：用 CPython venvlauncher 型的 venv 執行該測試，修改前 1 failed，修改後通過。

### 4. Linux dot-relative 路徑測試失敗 — `7ff150d`

- 症狀：`tests/test_stdio_runconfig.py` 的 dot-relative executable 案例在 Linux 回報找不到檔案。
- 原因：測試把指令硬編成 Windows 形式（點加反斜線加檔名）。Linux 上反斜線不是路徑分隔符，
  resolver 正確回報該檔案不存在。
- 修法：改用 `os.path.join(".", name)`。Windows 仍得到原本的反斜線形式，POSIX 得到斜線形式。
  resolver 不變。

## 修復流程

1. **核對現況**。`git fetch` 後確認 HEAD 等於 origin/main、工作區乾淨；用 `gh run view`
   取得失敗步驟與 log；在本機重現第一個失敗（`uv run ruff format --check .`）。
2. **隔離分支，一個根因一個 commit**。commit message 寫根因與驗證方式。不加
   `Co-Authored-By` trailer，publication audit 會擋。
3. **每個 commit 前跑本機檢查的單一入口**：

   ```bash
   bash scripts/check.sh
   ```

   它涵蓋 lint、format、mypy（host 與 Linux）、tests、fixture rebuild、leak scan 與
   release audit。schema validation、`--publication` audit、build 與 Docker gates 交給 PR 的 CI。

4. **推分支、開 PR，用 PR 的 CI 看 Linux 側**。quality job 每次只揭露一個失敗，所以
   會來回幾次；每次只修剛揭露的那一個。
5. **整合用本機 merge**。`git merge --no-ff`，subject 沿用 `merge: integrate ...`，然後推
   main。不用 GitHub 的 merge 按鈕：committer 會變成 GitHub，audit 會擋。
6. **確認 main 的 run 全綠**，再刪除已合併的分支。

## 觸發條件的決定

修復後曾把 push 觸發改成所有分支（PR #3，merge `4e74297`），隨後改回只有 main push 與
pull_request（PR #4，merge `cf8b727`），理由是規則要短到一眼看懂。現行規則只有一條：
改動走 PR 就會在合併前被檢查；直接推 main 只會在推送後檢查。

## 之後如何避免

- 系列改動走 PR，不直接批次推 main。
- 推送前跑 `bash scripts/check.sh`。它包含 `mypy --platform linux`，這是在 Windows 上唯一能在
  本機看到 Linux 型別錯誤的方法。
- 涉及子行程、pipe 或路徑的測試，記得 CI 的 venv `python.exe` 可能是 launcher 行程，
  而 Linux 沒有反斜線分隔符。
- 一個紅燈步驟後面可能還藏著別的紅燈；修完第一個就再跑一次完整 job。

## 這份紀錄不涵蓋

- 沒有改任何檢查條件，也沒有動評測結果、fixtures 或 release evidence。
- 第 3 項的修法依賴 `sys._base_executable`。它自 Python 3.8 起存在並由 venv 模組使用，
  但屬底線命名的屬性。
- launcher 的判斷來自本機用 venvlauncher venv 重現，CI runner 上的 venv 建立細節
  （uv 0.12.10 加 hosted toolcache CPython 3.12.10）沒有直接在 runner 上檢視。
