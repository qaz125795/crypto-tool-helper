# GitHub Actions 快速設置指南

這是一個完整的步驟指南，幫助您從零開始設置 GitHub Actions 定時任務。

---

## 📋 前置準備

1. ✅ 已經有 GitHub 帳號
2. ✅ 代碼已經在 GitHub 倉庫中
3. ✅ 已經有所有需要的 API 金鑰（CoinGecko、CoinGlass、Tree of Alpha、Telegram）

---

## 🚀 完整設置流程

### 步驟 1：確認 Workflow 文件已存在

您的倉庫應該已經包含以下文件（如果沒有，請先推送代碼）：

```
.github/
└── workflows/
    ├── sector-ranking.yml          # 主流板塊排行榜推播
    ├── whale-position.yml          # 巨鯨持倉動向
    ├── position-change.yml         # 持倉變化篩選
    ├── economic-data.yml           # 重要經濟數據推播
    ├── news.yml                    # 新聞快訊推播
    ├── funding-rate.yml            # 資金費率排行榜
    ├── long-term-index.yml         # 長線牛熊導航儀
    ├── liquidity-radar.yml         # 流動性獵取雷達
    └── altseason-radar.yml         # 山寨爆發雷達
```

**確認方法**：
1. 進入您的 GitHub 倉庫
2. 點擊 `.github` 資料夾
3. 進入 `workflows` 資料夾
4. 確認所有 `.yml` 文件都在

---

### 步驟 2：設置 GitHub Secrets（API 金鑰）

這是**最重要**的步驟！所有 API 金鑰都需要在這裡設置。

#### 2.1 進入 Secrets 設置頁面

1. 在 GitHub 倉庫頁面，點擊上方的 **Settings（設置）** 標籤
   ```
   [Code] [Issues] [Pull requests] [Actions] [Projects] [Wiki] [Settings] [Security]
   ```
2. 在左側選單中找到 **Secrets and variables** → **Actions**
   ```
   General
   Access
   Secrets and variables
     → Actions          ← 點擊這裡
   Environments
   ...
   ```

#### 2.2 添加必需的 Secrets

點擊 **New repository secret** 按鈕，逐個添加以下 secrets：

##### ✅ 必需的 Secrets（5 個）

| Secret 名稱 | 說明 | 在哪裡找 |
|------------|------|---------|
| `CG_GECKO_API_KEY` | CoinGecko API 金鑰 | CoinGecko 網站 |
| `CG_API_KEY` | CoinGlass API 金鑰 | CoinGlass 網站 |
| `TREE_API_KEY` | Tree of Alpha API 金鑰 | Tree of Alpha 網站 |
| `TG_TOKEN` | Telegram Bot Token | Telegram BotFather |
| `CHAT_ID` | Telegram 聊天室 ID | Telegram 群組或頻道 |

**添加步驟**：
1. 點擊 **New repository secret**
2. **Name（名稱）**：輸入 secret 名稱（例如：`CG_GECKO_API_KEY`）
3. **Secret（值）**：貼上對應的 API 金鑰或 Token
4. 點擊 **Add secret**
5. 重複以上步驟添加其他 secrets

**⚠️ 注意**：Secret 的值一旦添加後就無法再次查看，如果忘記了需要重新添加。

##### ✅ 可選的 Thread ID Secrets（9 個）

如果您使用 Telegram 群組的話題（Thread）功能，可以設置以下 secrets：

| Secret 名稱 | 說明 | 預設值（如果不設置） |
|------------|------|-------------------|
| `TG_THREAD_SECTOR_RANKING` | 主流板塊排行榜 Thread ID | `5` |
| `TG_THREAD_WHALE_POSITION` | 巨鯨持倉動向 Thread ID | `246` |
| `TG_THREAD_POSITION_CHANGE` | 持倉變化篩選 Thread ID | `250` |
| `TG_THREAD_ECONOMIC_DATA` | 重要經濟數據推播 Thread ID | `13` |
| `TG_THREAD_NEWS` | 新聞快訊推播 Thread ID | `7` |
| `TG_THREAD_FUNDING_RATE` | 資金費率排行榜 Thread ID | `244` |
| `TG_THREAD_LONG_TERM_INDEX` | 長線牛熊導航儀 Thread ID | `248` |
| `TG_THREAD_LIQUIDITY_RADAR` | 流動性獵取雷達 Thread ID | `3` |
| `TG_THREAD_ALTSEASON_RADAR` | 山寨爆發雷達 Thread ID | `254` |

**或者使用 JSON 格式（推薦）**：

可以設置一個名為 `TG_THREAD_IDS` 的 secret，值是 JSON 格式：
```json
{
  "sector_ranking": 5,
  "whale_position": 246,
  "position_change": 250,
  "economic_data": 13,
  "news": 7,
  "funding_rate": 244,
  "long_term_index": 248,
  "liquidity_radar": 3,
  "altseason_radar": 254
}
```

#### 2.3 確認 Secrets 已添加

添加完成後，您應該能在 **Repository secrets** 列表中看到所有添加的 secrets：

```
Repository secrets (14)
├── CG_GECKO_API_KEY        ← 顯示為 ********
├── CG_API_KEY              ← 顯示為 ********
├── TREE_API_KEY            ← 顯示為 ********
├── TG_TOKEN                ← 顯示為 ********
├── CHAT_ID                 ← 顯示為 ********
└── ...（其他 Thread IDs）
```

**⚠️ 重要**：如果某個 secret 沒有正確添加，對應的任務會執行失敗！

---

### 步驟 3：啟用 GitHub Actions

1. 在倉庫頁面，點擊上方的 **Actions（動作）** 標籤
   ```
   [Code] [Issues] [Pull requests] [Actions] [Projects] [Wiki] [Settings]
   ```

2. 如果這是第一次使用 GitHub Actions，您會看到提示：
   ```
   Get started with GitHub Actions
   
   GitHub Actions makes it easy to automate all your software workflows, 
   now with world-class CI/CD. Build, test, and deploy your code right 
   from GitHub.
   
   [I understand my workflows, go ahead and enable them]  ← 點擊這個按鈕
   ```

3. 點擊 **I understand my workflows, go ahead and enable them**

4. 完成！現在 GitHub Actions 已經啟用

---

### 步驟 4：測試執行（手動觸發）

在等待定時任務自動執行之前，建議先手動測試一下，確保設置正確。

#### 4.1 手動觸發任務

1. 在 **Actions** 頁面，您會看到左側有一個列表，顯示所有可用的 workflows：
   ```
   All workflows
   ├── 主流板塊排行榜推播
   ├── 巨鯨持倉動向
   ├── 持倉變化篩選
   ├── 重要經濟數據推播
   ├── 新聞快訊推播
   ├── 資金費率排行榜
   ├── 長線牛熊導航儀
   ├── 流動性獵取雷達
   └── 山寨爆發雷達
   ```

2. 選擇任意一個 workflow（建議先測試 **主流板塊排行榜推播**）

3. 點擊右側的 **Run workflow** 下拉按鈕

4. 選擇分支（通常是 `main` 或 `master`）

5. 點擊綠色的 **Run workflow** 按鈕

#### 4.2 查看執行結果

1. 點擊執行記錄（通常顯示在頂部，標題是黃色的圓圈 ⏳ 表示執行中）

2. 等待執行完成（通常需要 1-3 分鐘）

3. 查看執行日誌：
   - 點擊 **Run sector ranking task**（或對應的步驟名稱）
   - 展開查看詳細日誌

4. 檢查執行狀態：
   - ✅ **綠色勾號**：執行成功
   - ❌ **紅色叉號**：執行失敗
   - ⏳ **黃色圓圈**：正在執行

#### 4.3 如果執行失敗怎麼辦？

**常見問題**：

1. **錯誤：找不到某個 Secret**
   - 解決：回到步驟 2，確認所有必需的 secrets 都已添加

2. **錯誤：API 金鑰無效**
   - 解決：檢查 API 金鑰是否正確，是否已過期

3. **錯誤：Telegram 發送失敗**
   - 解決：檢查 `TG_TOKEN` 和 `CHAT_ID` 是否正確

4. **錯誤：Python 依賴安裝失敗**
   - 解決：檢查 `requirements.txt` 是否正確

**查看詳細錯誤**：
- 在執行記錄中，點擊失敗的步驟
- 查看紅色的錯誤訊息
- 根據錯誤訊息調整設置

---

### 步驟 5：確認定時任務已啟用

1. 在 **Actions** 頁面，點擊任意一個 workflow

2. 您會看到右側有一個 **⏰ Schedule** 標籤（如果有的話）

3. 或者，可以查看 workflow 文件中的 `schedule` 部分確認定時設置

**定時任務執行時間（UTC）**：

| 任務 | 頻率 | UTC 時間 |
|------|------|----------|
| 主流板塊排行榜 | 每小時 | 整點（:00） |
| 巨鯨持倉動向 | 每小時 | 整點（:00） |
| 持倉變化篩選 | 每 15 分鐘 | :00, :15, :30, :45 |
| 重要經濟數據推播 | 每天 | 00:00 |
| 新聞快訊推播 | 每 30 分鐘 | :00, :30 |
| 資金費率排行榜 | 每天 6 次 | 00:00, 04:00, 08:00, 12:00, 16:00, 20:00 |
| 長線牛熊導航儀 | 每 12 小時 | 00:00, 12:00 |
| 流動性獵取雷達 | 每小時 | 整點（:00） |
| 山寨爆發雷達 | 每 4 小時 | 00:00, 04:00, 08:00, 12:00, 16:00, 20:00 |

**⚠️ 注意**：GitHub Actions 使用 UTC 時間，台灣時間 = UTC + 8

---

### 步驟 6：監控執行狀態

#### 6.1 查看執行歷史

1. 在 **Actions** 頁面，點擊左側的 workflow 名稱

2. 您會看到所有的執行歷史：
   - 最新的執行在頂部
   - 綠色勾號 ✅ = 成功
   - 紅色叉號 ❌ = 失敗
   - 黃色圓圈 ⏳ = 執行中

#### 6.2 設置通知（可選）

1. 在倉庫頁面，點擊右上角的 **Watch（關注）** 按鈕

2. 選擇 **Custom（自定義）**

3. 勾選 **Actions** → **Runs of this workflow**

4. 這樣當 workflow 執行失敗時，您會收到通知郵件

---

## ✅ 設置完成檢查清單

完成以下所有項目後，您的 GitHub Actions 就設置完成了：

- [ ] ✅ Workflow 文件已存在（`.github/workflows/` 目錄下有 9 個 `.yml` 文件）
- [ ] ✅ 必需的 Secrets 已添加（5 個：CG_GECKO_API_KEY, CG_API_KEY, TREE_API_KEY, TG_TOKEN, CHAT_ID）
- [ ] ✅ 可選的 Thread ID Secrets 已添加（如果需要的話）
- [ ] ✅ GitHub Actions 已啟用
- [ ] ✅ 至少手動測試了一個 workflow，執行成功
- [ ] ✅ 確認 Telegram 有收到測試訊息

---

## 🔧 常見問題（FAQ）

### Q1: 如何修改定時任務的執行時間？

**A**: 編輯對應的 workflow 文件（例如：`.github/workflows/sector-ranking.yml`），修改 `cron` 表達式：

```yaml
on:
  schedule:
    - cron: '0 * * * *'  # 修改這裡的時間
```

Cron 表達式格式：`分 時 日 月 星期`

### Q2: 如何停止某個定時任務？

**A**: 有兩種方法：

1. **臨時停用**：編輯 workflow 文件，註釋掉 `schedule` 部分：
   ```yaml
   # on:
   #   schedule:
   #     - cron: '0 * * * *'
   ```

2. **完全刪除**：刪除對應的 workflow 文件

### Q3: 如何修改某個任務的執行頻率？

**A**: 編輯對應的 workflow 文件中的 `cron` 表達式。常見的 cron 表達式：

| 需求 | Cron 表達式 | 說明 |
|------|------------|------|
| 每小時 | `0 * * * *` | 每小時整點 |
| 每 30 分鐘 | `*/30 * * * *` | 每小時的 00 和 30 分 |
| 每 15 分鐘 | `*/15 * * * *` | 每小時的 00, 15, 30, 45 分 |
| 每天 00:00 | `0 0 * * *` | 每天午夜 |
| 每天 08:00 | `0 8 * * *` | 每天上午 8 點 |
| 每 4 小時 | `0 */4 * * *` | 00:00, 04:00, 08:00, 12:00, 16:00, 20:00 |
| 每 12 小時 | `0 0,12 * * *` | 00:00 和 12:00 |

### Q4: 執行失敗了怎麼辦？

**A**: 
1. 點擊失敗的執行記錄
2. 查看錯誤訊息
3. 檢查 Secrets 是否正確
4. 檢查 API 金鑰是否有效
5. 查看詳細日誌找出問題

### Q5: 如何查看執行日誌？

**A**: 
1. 進入 **Actions** 頁面
2. 點擊要查看的 workflow
3. 點擊執行記錄（最新的在頂部）
4. 點擊步驟名稱（例如：**Run sector ranking task**）
5. 查看詳細日誌

### Q6: 可以同時執行多個任務嗎？

**A**: 可以！GitHub Actions 會自動排隊執行。如果多個任務同時觸發（例如每小時整點），它們會依次執行，不會互相干擾。

### Q7: 執行需要多長時間？

**A**: 
- 大部分任務：1-3 分鐘
- 持倉變化篩選（處理 904 個幣種）：10-15 分鐘
- 其他任務：通常 1-5 分鐘

---

## 💾 關於資料持久化

**重要說明**：GitHub Actions 是無狀態的，每次執行後文件會消失。

目前 JackBot 使用文件系統存儲去重數據（經濟數據 ID、新聞時間戳）。在 GitHub Actions 中：
- **短期解決方案**：接受無狀態（可能偶爾有重複推送，但影響不大）
- **長期解決方案**：使用資料庫或 GitHub Actions Cache

詳細說明請查看：**[`GITHUB_ACTIONS_資料庫設置指南.md`](GITHUB_ACTIONS_資料庫設置指南.md)**

---

## 📞 需要幫助？

如果遇到問題，可以：

1. 查看 [GitHub Actions 官方文檔](https://docs.github.com/en/actions)
2. 查看本項目的 `GITHUB_ACTIONS_SETUP.md` 文件（更詳細的說明）
3. 查看 `GITHUB_ACTIONS_資料庫設置指南.md`（關於資料持久化）
4. 檢查 GitHub Actions 的執行日誌找出錯誤原因

---

## 🎉 完成！

恭喜！您的 GitHub Actions 定時任務已經設置完成。現在您的 JackBot 會自動按照設定的時間執行任務，無需手動操作。

**下一步**：
- 等待定時任務自動執行
- 監控執行狀態
- 檢查 Telegram 是否收到推播訊息
- （可選）取消 cron-job.org 的定時任務

---

**最後更新**：2024年

