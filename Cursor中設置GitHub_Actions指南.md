# 在 Cursor 中設置 GitHub Actions 指南

## ✅ 可以在 Cursor 中做的

### 1. 查看和編輯 Workflow 文件

您已經可以在 Cursor 中看到這些文件了：

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

**這些文件已經設置好了，通常不需要修改！**

如果需要修改執行時間，可以編輯這些 `.yml` 文件中的 `cron` 表達式。

---

### 2. 通過 Cursor 的 GitHub 擴展查看 Actions

如果您安裝了 GitHub 擴展，可能可以：
- 在側邊欄看到 GitHub 相關選項
- 查看 Actions 執行狀態（可能需要連接 GitHub 帳號）

---

## ❌ 必須在 GitHub 網站做的

### GitHub Secrets 設置

**重要**：API 金鑰（Secrets）必須在 GitHub 網站設置，無法在 Cursor 中完成。

---

## 🚀 推薦操作流程

### 方式 1：在 Cursor 中查看，在 GitHub 網站設置（推薦）

1. **在 Cursor 中**：
   - ✅ 確認 workflow 文件已存在（`.github/workflows/` 目錄）
   - ✅ 可以查看和編輯 workflow 文件
   - ✅ 使用 Git 推送代碼到 GitHub

2. **在 GitHub 網站**：
   - ⚙️ 設置 Secrets（API 金鑰）
   - ⚙️ 啟用 GitHub Actions
   - ⚙️ 測試執行 workflow

---

### 方式 2：完全在 Cursor 中（如果擴展支持）

如果 Cursor 的 GitHub 擴展支持完整功能，您可以：

1. **連接 GitHub 帳號**：
   - 查看 Cursor 側邊欄是否有 GitHub 相關圖標
   - 點擊並登入 GitHub 帳號

2. **設置 Secrets**：
   - 如果擴展支持，可能可以在 Cursor 中設置
   - 否則仍需要去 GitHub 網站

3. **查看 Actions**：
   - 可能可以在 Cursor 中查看執行狀態

---

## 📝 具體操作步驟

### 步驟 1：在 Cursor 中確認文件

1. 在 Cursor 的左側檔案列表中
2. 找到 `.github/workflows/` 資料夾
3. 確認裡面有 9 個 `.yml` 文件

**如果文件都在** → 很好！可以跳到步驟 2

**如果文件不在** → 需要先推送代碼：
```bash
git add .
git commit -m "Add GitHub Actions workflows"
git push
```

---

### 步驟 2：連接 GitHub（如果 Cursor 擴展支持）

1. **查看 Cursor 側邊欄**：
   - 找到 GitHub 相關的圖標或選項
   - 可能是 👤 圖標、GitHub logo 或「Source Control」相關選項

2. **登入 GitHub**：
   - 點擊並按照提示登入 GitHub 帳號
   - 可能需要授權 Cursor 訪問 GitHub

3. **確認連接成功**：
   - 側邊欄應該會顯示您的 GitHub 帳號資訊

---

### 步驟 3：設置 Secrets

**如果在 Cursor 中可以設置**：
- 查找 Secrets 相關的選項或命令
- 按照提示添加 Secrets

**如果無法在 Cursor 中設置**（最常見）：
- 打開瀏覽器，前往 GitHub 網站
- 按照「GitHub後台操作步驟.md」中的步驟設置 Secrets

---

### 步驟 4：測試執行

**在 Cursor 中**（如果支持）：
- 查找 Actions 相關的視圖或命令
- 查看 workflow 執行狀態

**在 GitHub 網站**（如果 Cursor 不支持）：
- 前往 GitHub 倉庫的 Actions 頁面
- 手動觸發 workflow 測試

---

## 🔍 如何確認 Cursor 擴展的功能

### 檢查側邊欄

1. 查看 Cursor 左側的圖標列表
2. 尋找以下可能的圖標：
   - 🐙 GitHub logo
   - ⚙️ Settings
   - 📊 或圖表相關圖標
   - 🔄 Actions 或 CI/CD 相關圖標

### 檢查命令面板

1. 按 `Ctrl+Shift+P`（Windows）或 `Cmd+Shift+P`（Mac）
2. 輸入「GitHub」或「Actions」
3. 查看是否有相關命令，例如：
   - 「GitHub: View Actions」
   - 「GitHub: Manage Secrets」
   - 「GitHub: Run Workflow」

### 檢查擴展資訊

1. 點擊 Cursor 左下角的 ⚙️ 圖標
2. 選擇「Extensions」（擴展）
3. 搜索「GitHub Actions」
4. 查看已安裝的擴展功能說明

---

## 💡 實際建議

基於實際經驗，**最簡單可靠的方式**是：

1. **在 Cursor 中**：
   - ✅ 編輯和推送代碼
   - ✅ 查看 workflow 文件

2. **在 GitHub 網站**：
   - ⚙️ 設置 Secrets（必須）
   - ⚙️ 查看 Actions 執行狀態
   - ⚙️ 測試 workflow

**為什麼？**
- GitHub Secrets 的安全性要求較高，通常只能在官方網站設置
- GitHub 網站有完整的 Actions 管理界面
- 功能更穩定可靠

---

## ❓ 如果 Cursor 擴展支持更多功能

如果您發現 Cursor 的 GitHub Actions 擴展支持更多功能，您可以：

1. **試試看**：
   - 查看擴展的文檔
   - 嘗試在 Cursor 中直接設置

2. **告訴我**：
   - 您看到了哪些功能
   - 我可以幫您確認是否可以使用

---

## 🎯 快速開始（推薦方式）

**最簡單的方法**：

1. **在 Cursor 中**：
   - 確認 workflow 文件已存在
   - 使用 Git 推送代碼（如果還沒推送）

2. **打開瀏覽器**：
   - 前往 `https://github.com/您的用戶名/您的倉庫名`
   - 按照「GitHub後台操作步驟.md」設置 Secrets

3. **完成！**

這樣既利用了 Cursor 的編輯功能，又使用了 GitHub 官方的完整管理界面。

---

**您目前看到了什麼功能？告訴我，我可以幫您確認下一步該怎麼做！** 😊


