# GitHub 後台操作步驟 - 圖文詳細指南

這是完整的 GitHub 後台操作步驟，一步一步教您如何找到和設置 GitHub Actions。

---

## 📍 第一步：確認您在正確的倉庫

1. **確認您已經登入 GitHub**
   - 網址應該是：`https://github.com`
   - 右上角應該顯示您的頭像

2. **找到您的 JackBot 倉庫**
   - 如果剛好就在這個倉庫頁面，那很好！
   - 如果不在，請：
     - 點擊左上角的 GitHub logo 或首頁
     - 在「Repositories」列表中找到您的 JackBot 倉庫
     - 點擊進入倉庫

3. **確認倉庫頁面**
   - 您應該看到倉庫的檔案列表
   - 上方有這些標籤：
     ```
     [Code] [Issues] [Pull requests] [Actions] [Projects] [Wiki] [Settings] [Security]
     ```

---

## 🎯 第二步：找到 Actions 標籤（兩種方法）

### 方法 1：直接點擊 Actions 標籤（最簡單）

1. **在倉庫頁面頂部**，找到這排標籤：
   ```
   Code  Issues  Pull requests  Actions  Projects  Wiki  Settings  Security
                      ↑
                  點擊這裡！
   ```

2. **點擊「Actions」標籤**

3. **如果您看到這個畫面**：
   ```
   Get started with GitHub Actions
   
   GitHub Actions makes it easy to automate all your software workflows...
   
   [I understand my workflows, go ahead and enable them]
   ```
   → 請繼續看第三步

4. **如果您看到左側有 workflow 列表**：
   ```
   All workflows
   ├── 主流板塊排行榜推播
   ├── 巨鯨持倉動向
   ...
   ```
   → 恭喜！Actions 已經啟用，請跳到第五步

---

### 方法 2：如果找不到 Actions 標籤（較少見）

有時候 Actions 標籤可能被隱藏，需要：

1. 點擊 **Settings（設置）** 標籤
2. 在左側選單中找到 **Actions** → **General**
3. 確認 **Actions permissions** 已啟用
4. 回到倉庫首頁，Actions 標籤應該會出現

---

## ✅ 第三步：啟用 GitHub Actions

**如果您看到「Get started with GitHub Actions」的提示**：

1. **閱讀提示訊息**（大約 2-3 行說明文字）

2. **找到這個按鈕**：
   ```
   [I understand my workflows, go ahead and enable them]
   ```
   或
   ```
   [了解並啟用 workflows]
   ```
   
   **這個按鈕通常在：**
   - 頁面中央或下方
   - 可能是綠色或藍色的按鈕

3. **點擊這個按鈕**

4. **完成！** Actions 現在已經啟用

---

## 📋 第四步：確認 Workflow 文件存在

啟用後，您應該會看到左側有 workflow 列表。如果沒有看到，請確認：

1. **回到倉庫首頁**（點擊「Code」標籤）

2. **查看檔案列表**，確認有這個資料夾：
   ```
   .github/
   └── workflows/
       ├── sector-ranking.yml
       ├── whale-position.yml
       ├── position-change.yml
       ├── economic-data.yml
       ├── news.yml
       ├── funding-rate.yml
       ├── long-term-index.yml
       ├── liquidity-radar.yml
       └── altseason-radar.yml
   ```

3. **如果看不到 `.github` 資料夾**：
   - 可能需要刷新頁面（按 F5）
   - 或者檔案還沒推送到 GitHub（需要先 git push）

---

## ⚙️ 第五步：設置 GitHub Secrets（最重要！）

現在需要設置 API 金鑰，這樣 workflow 才能正常執行。

### 5.1 進入 Settings 頁面

1. **在倉庫頁面頂部**，找到 **Settings（設置）** 標籤
   ```
   Code  Issues  Pull requests  Actions  Projects  Wiki  Settings  Security
                                                              ↑
                                                          點擊這裡！
   ```

2. **點擊「Settings」**

3. **您會進入設置頁面**，左側有選單：
   ```
   General
   Access
   Secrets and variables
   Environments
   ...
   ```

### 5.2 找到 Secrets 設置

1. **在左側選單中**，找到 **Secrets and variables**
   ```
   General
   Access
   Secrets and variables  ← 點擊這裡！
     ├── Actions         ← 再點擊這個！
     └── Dependabot
   ```

2. **點擊「Actions」**

3. **您會看到這個頁面**：
   ```
   Repository secrets
   
   Secrets and variables for Actions are used to store sensitive information
   like API keys and passwords that you want to use in your workflows.
   
   [New repository secret]  ← 這是添加 secret 的按鈕
   
   Repository secrets (0)   ← 目前應該顯示 0 或少量
   ```

### 5.3 添加第一個 Secret

讓我們從最重要的開始：

1. **點擊「New repository secret」按鈕**（通常在右上角，綠色或藍色按鈕）

2. **您會看到表單**：
   ```
   Name:  [這裡輸入 secret 名稱]
   
   Secret: [這裡輸入 secret 的值]
   
   [Cancel]  [Add secret]
   ```

3. **添加第一個 Secret**：`TG_TOKEN`
   - **Name**: 輸入 `TG_TOKEN`（大寫，底線）
   - **Secret**: 貼上您的 Telegram Bot Token
   - **點擊「Add secret」**

4. **確認添加成功**
   - 您會回到 Secrets 列表頁面
   - 應該能看到：
     ```
     Repository secrets (1)
     └── TG_TOKEN  •••••••••••  ← 顯示為隱藏
     ```

### 5.4 添加其他必需的 Secrets

重複上述步驟，添加以下 Secrets：

| Secret 名稱 | 說明 | 在哪裡找 |
|------------|------|---------|
| `CHAT_ID` | Telegram 聊天室 ID | Telegram 群組或頻道 |
| `CG_GECKO_API_KEY` | CoinGecko API 金鑰 | CoinGecko 網站 |
| `CG_API_KEY` | CoinGlass API 金鑰 | CoinGlass 網站 |
| `TREE_API_KEY` | Tree of Alpha API 金鑰 | Tree of Alpha 網站 |

**添加完成後**，您的 Secrets 列表應該顯示：
```
Repository secrets (5)
├── TG_TOKEN           •••••••••••
├── CHAT_ID            •••••••••••
├── CG_GECKO_API_KEY   •••••••••••
├── CG_API_KEY         •••••••••••
└── TREE_API_KEY       •••••••••••
```

### 5.5 添加可選的 Thread IDs（如果需要）

如果您使用 Telegram 話題（Thread）功能，可以添加：

- 方法 1：添加 JSON 格式的 `TG_THREAD_IDS`
- 方法 2：單獨添加各個 `TG_THREAD_*` secrets

**詳細說明請看：** `GITHUB_ACTIONS_快速設置指南.md`

---

## 🧪 第六步：測試執行（手動觸發）

現在來測試一下 workflow 是否能正常執行：

### 6.1 回到 Actions 頁面

1. **點擊頂部的「Actions」標籤**

2. **您應該看到左側有 workflow 列表**：
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

### 6.2 選擇一個 workflow 測試

1. **點擊「主流板塊排行榜推播」**（或其他任意一個）

2. **您會看到 workflow 的執行歷史**（如果第一次使用，可能是空的）

3. **在右側**，找到 **「Run workflow」** 按鈕
   ```
   This workflow has a workflow_dispatch event trigger.
   
   [Run workflow ▼]  ← 點擊這個下拉按鈕
   ```

4. **點擊「Run workflow」按鈕**，會出現下拉選單：
   ```
   Branch: [main ▼]  ← 選擇分支（通常是 main）
   
   [Cancel]  [Run workflow]  ← 點擊這個綠色按鈕
   ```

5. **確認分支是 `main`（或 `master`）**

6. **點擊綠色的「Run workflow」按鈕**

### 6.3 查看執行狀態

1. **執行會立即開始**，您會看到一個新的執行記錄：
   ```
   #1234  主流板塊排行榜推播  main   ⏳  ← 黃色圓圈表示執行中
   ```

2. **點擊這個執行記錄**（標題會顯示黃色圓圈 ⏳）

3. **查看執行過程**：
   - 左側會顯示步驟：
     ```
     ✓ Checkout code
     ✓ Set up Python
     ✓ Install dependencies
     ⏳ Run sector ranking task  ← 正在執行
     ```

4. **等待執行完成**（通常 1-3 分鐘）

5. **查看結果**：
   - ✅ **綠色勾號** = 執行成功
   - ❌ **紅色叉號** = 執行失敗
   - ⏳ **黃色圓圈** = 正在執行

### 6.4 查看詳細日誌（如果失敗）

如果執行失敗（紅色叉號）：

1. **點擊失敗的執行記錄**

2. **點擊失敗的步驟**（例如：Run sector ranking task）

3. **展開查看詳細錯誤訊息**：
   ```
   Run sector ranking task
   TASK="${{ github.event.inputs.task || steps.schedule_task.outputs.task }}"
   echo "執行任務: $TASK"
   python jackbot.py $TASK
   
   Error: ...  ← 這裡會顯示錯誤訊息
   ```

4. **根據錯誤訊息修正問題**：
   - 如果提示找不到 Secret → 回到步驟 5 檢查 Secrets
   - 如果提示 API 錯誤 → 檢查 API 金鑰是否正確

---

## ✅ 第七步：確認設置完成

完成以下檢查清單：

- [ ] ✅ Actions 標籤已顯示並可以點擊
- [ ] ✅ 看到 workflow 列表（至少看到幾個 workflow 名稱）
- [ ] ✅ 已添加 5 個必需的 Secrets（TG_TOKEN, CHAT_ID, CG_GECKO_API_KEY, CG_API_KEY, TREE_API_KEY）
- [ ] ✅ 手動測試了一個 workflow，執行成功（綠色勾號）
- [ ] ✅ （可選）檢查 Telegram 有收到測試訊息

**如果以上都完成，恭喜您設置成功！** 🎉

---

## 🔍 常見問題（FAQ）

### Q1: 我找不到 Actions 標籤

**可能原因**：
1. Actions 功能被停用
   - **解決**：Settings → Actions → General → 確認已啟用

2. 瀏覽器視窗太小
   - **解決**：調整視窗大小或使用滾動查看

3. 權限不足
   - **解決**：確認您是倉庫的擁有者或協作者

### Q2: 點擊 Actions 後顯示「404 Not Found」

**可能原因**：Actions 功能未啟用

**解決**：
1. Settings → Actions → General
2. 確認「Allow all actions and reusable workflows」已選擇
3. 保存設置
4. 重新進入 Actions 頁面

### Q3: 看不到 workflow 列表

**可能原因**：`.github/workflows/` 目錄下的文件還沒推送到 GitHub

**解決**：
1. 確認本地有這些文件
2. 使用 git 推送：
   ```bash
   git add .github/
   git commit -m "Add GitHub Actions workflows"
   git push
   ```
3. 刷新 GitHub 頁面

### Q4: 點擊「Run workflow」沒反應

**可能原因**：需要選擇分支

**解決**：
1. 確保點擊了下拉選單中的「Run workflow」
2. 選擇正確的分支（main 或 master）
3. 點擊綠色的「Run workflow」按鈕

### Q5: Secrets 添加後找不到

**確認位置**：
- Settings → Secrets and variables → Actions
- 確認在「Repository secrets」標籤下，不是「Environment secrets」

---

## 📞 需要幫助？

如果以上步驟都完成後仍有問題：

1. **檢查執行日誌**：在 Actions 頁面點擊失敗的執行，查看詳細錯誤
2. **確認 Secrets 正確**：檢查所有 Secret 名稱是否完全正確（大小寫、底線等）
3. **查看詳細指南**：`GITHUB_ACTIONS_快速設置指南.md`

---

**祝您設置順利！** 🚀


