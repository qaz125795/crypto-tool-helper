# 在 Cursor 中使用 GitHub Actions 插件指南

## 🎯 您看到了什麼

在 Cursor 的右側欄（Marketplace/市集區域），您看到了：
- **GitHub Actions** 插件
- 顯示「GitHub Actions workflows and runs..」
- 有 GitHub 的驗證標記 ✓
- 可能有通知數量標記

---

## 🚀 操作步驟

### 步驟 1：點擊 GitHub Actions 插件

1. **在右側欄中找到「GitHub Actions」**
   - 應該顯示在 Marketplace/市集區域
   - 可能有「1」個通知標記

2. **點擊「GitHub Actions」插件**

3. **應該會展開或打開新的面板**，顯示：
   - Workflows 列表
   - 執行歷史
   - 或其他相關選項

---

### 步驟 2：探索插件功能

點擊後，您可能會看到以下內容：

#### 可能的選項：

1. **Workflows 列表**
   - 顯示您倉庫中的所有 workflow
   - 例如：sector-ranking、whale-position 等

2. **執行歷史（Runs）**
   - 顯示最近執行的記錄
   - 可能顯示成功/失敗狀態

3. **設置/配置選項**
   - Secrets 管理（如果支持）
   - Workflow 配置

4. **操作按鈕**
   - 「Run workflow」（執行 workflow）
   - 「View logs」（查看日誌）
   - 其他操作

---

### 步驟 3：確認 Workflows

在 GitHub Actions 插件中，您應該能看到：

```
Workflows:
├── 主流板塊排行榜推播 (sector-ranking.yml)
├── 巨鯨持倉動向 (whale-position.yml)
├── 持倉變化篩選 (position-change.yml)
├── 重要經濟數據推播 (economic-data.yml)
├── 新聞快訊推播 (news.yml)
├── 資金費率排行榜 (funding-rate.yml)
├── 長線牛熊導航儀 (long-term-index.yml)
├── 流動性獵取雷達 (liquidity-radar.yml)
└── 山寨爆發雷達 (altseason-radar.yml)
```

**如果看到了** → 很好！workflow 文件已經被 GitHub 識別

**如果沒看到** → 可能需要先推送到 GitHub：
```bash
git add .github/
git commit -m "Add GitHub Actions workflows"
git push
```

---

### 步驟 4：設置 Secrets（API 金鑰）

#### 選項 A：如果插件支持設置 Secrets

1. **在 GitHub Actions 插件中查找**：
   - 「Secrets」選項
   - 「Settings」選項
   - 「Configure」選項
   - 或類似的設置按鈕

2. **點擊進入 Secrets 設置頁面**

3. **添加以下 Secrets**：
   - `TG_TOKEN`
   - `CHAT_ID`
   - `CG_GECKO_API_KEY`
   - `CG_API_KEY`
   - `TREE_API_KEY`

#### 選項 B：如果插件不支持設置 Secrets（更常見）

**需要到 GitHub 網站設置**：

1. **在插件中找到「Open on GitHub」或「View on GitHub」按鈕**
   - 點擊會打開瀏覽器，跳轉到 GitHub Actions 頁面

2. **或者手動打開瀏覽器**：
   - 前往：`https://github.com/qaz125795/crypto-tool-helper`（您的倉庫）
   - 點擊「Actions」標籤
   - 點擊「Settings」→「Secrets and variables」→「Actions」

---

### 步驟 5：測試執行 Workflow

#### 在 Cursor 的 GitHub Actions 插件中：

1. **找到一個 workflow**（例如：主流板塊排行榜推播）

2. **查找執行按鈕**：
   - 可能顯示為「▶️ Run」
   - 或「Run workflow」
   - 或類似的按鈕

3. **點擊執行按鈕**

4. **查看執行狀態**：
   - 應該會顯示執行進度
   - 可能顯示日誌輸出

#### 如果找不到執行按鈕：

- 可能需要先在 GitHub 網站設置 Secrets
- 或者使用「View on GitHub」功能在瀏覽器中執行

---

## 🔍 常見功能說明

### 如果插件顯示以下內容：

#### 1. **Workflows 列表**
- ✅ 表示 workflow 文件已識別
- 點擊可以查看詳細資訊

#### 2. **執行歷史（Runs）**
- 顯示最近執行的記錄
- ✅ 綠色勾號 = 成功
- ❌ 紅色叉號 = 失敗
- ⏳ 黃色圓圈 = 執行中

#### 3. **日誌查看（Logs）**
- 點擊執行記錄可以查看詳細日誌
- 幫助診斷問題

#### 4. **Secrets 管理**
- 如果支持，可以直接在這裡添加 Secrets
- 如果不支持，會引導您到 GitHub 網站

---

## 💡 推薦操作流程

### 最有效的方式：

1. **在 Cursor 中**：
   - ✅ 使用 GitHub Actions 插件查看 workflows
   - ✅ 查看執行狀態和日誌
   - ✅ 測試執行 workflow（如果支持）

2. **在 GitHub 網站**（必需）：
   - ⚙️ 設置 Secrets（API 金鑰）
   - ⚙️ 確認所有配置正確

3. **在 Cursor 中**：
   - ✅ 通過插件監控執行狀態
   - ✅ 查看日誌和結果

---

## ❓ 請告訴我

點擊 GitHub Actions 插件後，您看到了什麼？

1. **看到了哪些選項/按鈕？**
   - Workflows 列表？
   - 執行歷史？
   - Secrets 設置？
   - 其他功能？

2. **是否可以看到您的 workflows？**
   - 能看到 9 個 workflow 嗎？

3. **是否有「Run workflow」或執行按鈕？**

4. **是否有 Secrets 相關的選項？**

告訴我您看到的具體內容，我可以給您更精確的下一步指引！ 😊

---

## 🎯 快速測試

**現在試試這個**：

1. **點擊右側的「GitHub Actions」插件**
2. **看看顯示了什麼內容**
3. **告訴我您看到了什麼**

我會根據您看到的內容，給您下一步的具體操作指引！


