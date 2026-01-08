# GitHub Actions 設置指南

本指南將幫助您將 JackBot 的定時任務從 cron-job.org 遷移到 GitHub Actions。

## 為什麼要使用 GitHub Actions？

1. ✅ **無執行時間限制**：GitHub Actions 免費版單次任務最多可運行 6 小時
2. ✅ **完全免費**：公開倉庫完全免費，私有倉庫每月有 2000 分鐘免費額度
3. ✅ **更好的日誌**：可以在 GitHub 上直接查看執行日誌
4. ✅ **手動觸發**：可以隨時手動執行任務進行測試
5. ✅ **版本控制**：workflow 配置與代碼一起管理，方便版本控制

## 設置步驟

### 第一步：準備 GitHub 倉庫

1. 確保您的代碼已經推送到 GitHub 倉庫
2. 如果還沒有，請先推送：
   ```bash
   git add .
   git commit -m "Add GitHub Actions workflows"
   git push
   ```

### 第二步：設置 GitHub Secrets

1. 進入您的 GitHub 倉庫
2. 點擊右上角的 **Settings（設置）**
3. 在左側選單中找到 **Secrets and variables** → **Actions**
4. 點擊 **New repository secret** 添加以下 secrets：

#### 必需的 Secrets：

| Secret 名稱 | 說明 | 範例 |
|------------|------|------|
| `CG_GECKO_API_KEY` | CoinGecko API 金鑰 | `CG-RR9dam92RCAGpdV5VF7km59o` |
| `CG_API_KEY` | CoinGlass API 金鑰 | `4a2fd6ee6d2e49b091d81f1cfdf6315c` |
| `TREE_API_KEY` | Tree of Alpha API 金鑰 | `131c5449bc84d0b1f9cb17f399c62c21f9f4c06a70d0911e76cfbfa8cdbc070d` |
| `TG_TOKEN` | Telegram Bot Token | `8522999860:AAEIxFmxNWMCMZSzGJPwHF3JZaIDLbUs2BE` |
| `CHAT_ID` | Telegram 聊天室 ID | `-1003611242392` |

#### 可選的 Thread ID Secrets（如果不設置，會使用預設值）：

| Secret 名稱 | 說明 | 預設值 |
|------------|------|--------|
| `TG_THREAD_SECTOR_RANKING` | 主流板塊排行榜 Thread ID | `5` |
| `TG_THREAD_WHALE_POSITION` | 巨鯨持倉動向 Thread ID | `246` |
| `TG_THREAD_POSITION_CHANGE` | 持倉變化篩選 Thread ID | `250` |
| `TG_THREAD_ECONOMIC_DATA` | 重要經濟數據推播 Thread ID | `13` |
| `TG_THREAD_NEWS` | 新聞快訊推播 Thread ID | `7` |
| `TG_THREAD_FUNDING_RATE` | 資金費率排行榜 Thread ID | `244` |
| `TG_THREAD_LONG_TERM_INDEX` | 長線牛熊導航儀 Thread ID | `248` |
| `TG_THREAD_LIQUIDITY_RADAR` | 流動性獵取雷達 Thread ID | `3` |
| `TG_THREAD_ALTSEASON_RADAR` | 山寨爆發雷達 Thread ID | `254` |

#### 或者使用 JSON 格式（推薦）：

如果您的 Thread IDs 已經組合成 JSON，可以直接設置：

| Secret 名稱 | 值（範例） |
|------------|-----------|
| `TG_THREAD_IDS` | `{"sector_ranking":5,"whale_position":246,"position_change":250,"economic_data":13,"news":7,"funding_rate":244,"long_term_index":248,"liquidity_radar":3,"altseason_radar":254}` |

### 第三步：確認 Workflow 文件

確保 `.github/workflows/` 目錄下有以下 workflow 文件：
- `sector-ranking.yml` - 主流板塊排行榜推播
- `whale-position.yml` - 巨鯨持倉動向
- `position-change.yml` - 持倉變化篩選
- `economic-data.yml` - 重要經濟數據推播
- `news.yml` - 新聞快訊推播
- `funding-rate.yml` - 資金費率排行榜
- `long-term-index.yml` - 長線牛熊導航儀
- `liquidity-radar.yml` - 流動性獵取雷達
- `altseason-radar.yml` - 山寨爆發雷達

所有文件已經包含在倉庫中，無需額外設置。

### 第四步：啟用 GitHub Actions

1. 在倉庫頁面，點擊上方的 **Actions** 標籤
2. 如果這是第一次使用 Actions，點擊 **I understand my workflows, go ahead and enable them**
3. 這樣就啟用了 GitHub Actions

### 第五步：測試執行

1. 在 **Actions** 頁面，找到任意一個 workflow（例如：**主流板塊排行榜推播**）
2. 點擊該 workflow
3. 點擊右側的 **Run workflow** 按鈕
4. 選擇分支（通常是 `main` 或 `master`）
5. 點擊 **Run workflow** 執行
6. 等待執行完成，查看日誌確認是否成功

您也可以為每個任務單獨測試，每個 workflow 都可以獨立手動觸發。

## 定時任務時間表

GitHub Actions 使用 UTC 時間，以下是各任務的執行時間（UTC）：

| 任務 | Cron 表達式 | 頻率 | UTC 時間 | 台灣時間（UTC+8） |
|------|------------|------|----------|------------------|
| 主流板塊排行榜 | `0 * * * *` | 每小時 | 整點 | 整點 + 8 小時 |
| 巨鯨持倉動向 | `0 * * * *` | 每小時 | 整點 | 整點 + 8 小時 |
| 持倉變化篩選 | `*/15 * * * *` | 每 15 分鐘 | 00, 15, 30, 45 分 | 同上 + 8 小時 |
| 重要經濟數據推播 | `0 0 * * *` | 每天 | 00:00 | 08:00 |
| 新聞快訊推播 | `*/30 * * * *` | 每 30 分鐘 | 00, 30 分 | 同上 + 8 小時 |
| 資金費率排行榜 | `0 0,4,8,12,16,20 * * *` | 每天 6 次 | 00:00, 04:00, 08:00, 12:00, 16:00, 20:00 | 08:00, 12:00, 16:00, 20:00, 00:00, 04:00 |
| 長線牛熊導航儀 | `0 0,12 * * *` | 每 12 小時 | 00:00, 12:00 | 08:00, 20:00 |
| 流動性獵取雷達 | `0 * * * *` | 每小時 | 整點 | 整點 + 8 小時 |
| 山寨爆發雷達 | `0 */4 * * *` | 每 4 小時 | 00:00, 04:00, 08:00, 12:00, 16:00, 20:00 | 08:00, 12:00, 16:00, 20:00, 00:00, 04:00 |

### 注意事項

⚠️ **時區問題**：
- GitHub Actions 使用 **UTC 時間**（協調世界時）
- 台灣時間 = UTC + 8 小時
- 例如：台灣時間 08:00 = UTC 00:00
- 當前 cron 表達式使用 UTC 時間，如果需要在台灣時間的特定時間執行，請調整：

| 台灣時間 | UTC 時間 | Cron 表達式調整 |
|---------|---------|----------------|
| 08:00 | 00:00 | `0 * * * *` → `0 0 * * *` (如需固定台灣 08:00) |
| 整點 | 8 小時前 | `0 * * * *` (UTC 整點 = 台灣時間 08:00, 16:00, 00:00...) |

**時區調整範例**：
如果需要台灣時間每小時整點執行（00:00, 01:00, 02:00...），需要設置為 UTC 的 16:00, 17:00, 18:00...，也就是 UTC-8，使用：
```
0 16-23,0-7 * * *  # UTC 16:00-23:59 和 00:00-07:59 = 台灣時間 00:00-23:59
```

⚠️ **新聞快訊推播**：
- 只抓取 Tree of Alpha 新聞（不抓取 CoinGlass）
- 每 30 分鐘執行一次（`*/30 * * * *`）

⚠️ **重要經濟數據推播**：
- 調整為每天執行一次（UTC 00:00，台灣時間 08:00）

⚠️ **執行時間**：
- 持倉變化篩選：超時設置為 30 分鐘（處理 904 個幣種）
- 重要經濟數據推播：超時設置為 15 分鐘
- 其他任務：超時設置為 10-15 分鐘

⚠️ **任務執行時間衝突**：
- 每小時整點會同時執行：主流板塊排行榜、巨鯨持倉動向、流動性獵取雷達
- GitHub Actions 會自動排隊執行，不會重疊

## 手動執行任務

除了定時執行，您也可以手動觸發任何任務：

1. 進入 **Actions** 頁面
2. 在左側選單中選擇要執行的 workflow（例如：**主流板塊排行榜推播**）
3. 點擊右側的 **Run workflow** 按鈕
4. 選擇分支（通常是 `main` 或 `master`）
5. 點擊 **Run workflow** 執行

每個任務都有獨立的 workflow，可以分別手動觸發測試。

## 查看執行日誌

1. 進入 **Actions** 頁面
2. 在左側選單中選擇要查看的 workflow（例如：**主流板塊排行榜推播**）
3. 點擊要查看的執行記錄（最新的會在頂部）
4. 展開 **Run [task name]** 步驟查看詳細日誌

每個 workflow 都有獨立的執行歷史，方便追蹤各任務的執行狀況。

## 故障排除

### 問題 1：任務執行失敗

**解決方案**：
1. 檢查 Secrets 是否正確設置
2. 查看執行日誌中的錯誤訊息
3. 確認 API 金鑰是否有效

### 問題 2：任務沒有按時執行

**可能原因**：
1. GitHub Actions 可能會有延遲（最多 5-10 分鐘）
2. 倉庫必須有活動才會觸發（可以設置一個定期 commit）

**解決方案**：
- 使用 `workflow_dispatch` 手動觸發測試
- 檢查 Actions 頁面確認是否有排程任務

### 問題 3：執行時間過長

**解決方案**：
- 默認超時時間已設置為 30 分鐘
- 如果需要更長，可以修改 `.github/workflows/jackbot-tasks.yml` 中的 `timeout-minutes`

### 問題 4：環境變量未生效

**解決方案**：
- 確認所有 Secrets 都已正確設置
- 重新觸發 workflow 執行
- 檢查 workflow 文件中的環境變量名稱是否正確

## 與 cron-job.org 的對比

| 特性 | cron-job.org | GitHub Actions |
|------|-------------|----------------|
| 執行時間限制 | 30 秒（免費版） | 6 小時（免費版） |
| 費用 | 免費（有限制） | 公開倉庫完全免費 |
| 日誌查看 | 有限 | 完整且易於查看 |
| 手動觸發 | 需要付費 | 免費 |
| 版本控制 | 無 | 與代碼一起管理 |
| 設置難度 | 簡單 | 中等（需要設置 Secrets） |

## 下一步

1. ✅ 設置 GitHub Secrets
2. ✅ 測試手動執行任務
3. ✅ 確認定時任務正常執行
4. ✅ 監控執行日誌
5. ✅ （可選）取消 cron-job.org 的定時任務

## 參考資源

- [GitHub Actions 文檔](https://docs.github.com/en/actions)
- [Cron 表達式語法](https://crontab.guru/)
- [GitHub Actions 定時任務](https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows#schedule)

---

**完成設置後，您的 JackBot 將完全在 GitHub Actions 上運行，不再受執行時間限制！** 🎉

