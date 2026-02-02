# 修復 GitHub Actions 錯誤指南

## ⚠️ 如果是「全部掛掉」或 Runner / 內部伺服器錯誤

若你看到的是：

- **「The job was not acquired by Runner of type hosted even after multiple attempts」**
- **「Internal server error. Correlation ID: ...」**
- 多個 workflow（持倉變化、經濟數據、Hyperliquid、流動性雷達、Keep Repository Active）同時失敗

→ 這是 **GitHub 端** 的問題，不是你的設定錯誤。請直接看：

- **[GitHub Actions Runner 與伺服器錯誤排查](./GitHub_Actions_Runner與伺服器錯誤排查.md)**  
  內有說明與建議（查 GitHub Status、手動 Re-run 等）。

---

## ❌ 錯誤訊息（以下為 workflow 檔案問題）

```
Invalid workflow file: .github/workflows/main.yml#L1
(Line: 38, Col: 1): 'name' is already defined
(Line: 40, Col: 1): 'on' is already defined
...
```

## 🔍 問題原因

`main.yml` 文件包含了多個 workflow 定義，但 GitHub Actions 要求每個文件只能有一個 workflow。

## ✅ 解決方法

### 方法 1：在 GitHub 網站刪除 main.yml（推薦）

1. **前往 GitHub 網站**
   - 打開瀏覽器
   - 前往：`https://github.com/qaz125795/crypto-tool-helper`

2. **進入 workflows 目錄**
   - 點擊 `.github` 資料夾
   - 點擊 `workflows` 資料夾

3. **找到並刪除 main.yml**
   - 找到 `main.yml` 文件
   - 點擊文件進入
   - 點擊右上角的「✏️ Edit」（編輯）或「🗑️ Delete」（刪除）
   - 確認刪除

4. **確認其他 9 個文件存在**
   - 應該看到：
     - `sector-ranking.yml`
     - `whale-position.yml`
     - `position-change.yml`
     - `economic-data.yml`
     - `news.yml`
     - `funding-rate.yml`
     - `long-term-index.yml`
     - `liquidity-radar.yml`
     - `altseason-radar.yml`

### 方法 2：使用 Git 命令刪除（如果熟悉命令行）

```bash
# 進入專案目錄
cd C:\Users\USER\Desktop\JackBot

# 刪除遠程的 main.yml（如果存在）
git rm .github/workflows/main.yml

# 提交更改
git commit -m "Remove invalid main.yml file"

# 推送到 GitHub
git push
```

## 🎯 完成後的下一步

### 1. 驗證 Workflows

刪除 `main.yml` 後，回到 Cursor 的 GitHub Actions 插件：

1. **刷新或重新打開 GitHub Actions 插件**
2. **應該會看到 9 個 workflow**，而不是錯誤訊息
3. **每個 workflow 都應該顯示正常**

### 2. 設置 Secrets（如果還沒設置）

現在 workflow 文件正常了，需要設置 API 金鑰：

#### 在 Cursor 中（如果插件支持）：

1. 在 GitHub Actions 插件中查找「Secrets」選項
2. 添加以下 Secrets：
   - `TG_TOKEN`
   - `CHAT_ID`
   - `CG_GECKO_API_KEY`
   - `CG_API_KEY`
   - `TREE_API_KEY`

#### 在 GitHub 網站（如果 Cursor 不支持）：

1. 前往倉庫 → **Settings** → **Secrets and variables** → **Actions**
2. 點擊 **New repository secret**
3. 添加上述 5 個 Secrets

### 3. 測試執行

1. **在 GitHub Actions 插件中**：
   - 選擇一個 workflow（例如：主流板塊排行榜推播）
   - 點擊「Run workflow」或執行按鈕

2. **或前往 GitHub 網站**：
   - 進入 **Actions** 標籤
   - 選擇 workflow
   - 點擊 **Run workflow**

## ✅ 完成檢查清單

完成以下項目後，就設置完成了：

- [ ] ✅ 已刪除 `main.yml` 文件
- [ ] ✅ 確認 9 個 workflow 文件都存在且沒有錯誤
- [ ] ✅ 已添加 5 個必需的 Secrets
- [ ] ✅ 測試執行了一個 workflow，執行成功

## 🎉 完成！

刪除 `main.yml` 後，您的 GitHub Actions 應該就能正常工作了！

---

**下一步**：設置 Secrets 和測試執行。


