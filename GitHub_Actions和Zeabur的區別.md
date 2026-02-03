# GitHub Actions 和 Zeabur 的區別說明

## ⚠️ 重要：這是兩個完全獨立的系統！

### GitHub Actions
- **用途**：在 GitHub 上執行定時任務（定時觸發 Python 腳本）
- **環境變量來源**：GitHub Secrets（在 GitHub 倉庫的 Settings → Secrets 中設置）
- **執行位置**：GitHub 的伺服器

### Zeabur
- **用途**：部署 Web 服務（運行 Flask 應用，提供 HTTP 端點）
- **環境變量來源**：Zeabur 專案的環境變量設置
- **執行位置**：Zeabur 的伺服器

## 🔍 問題分析

您遇到的問題：
1. ✅ Zeabur 部署正常（https://crypto-tool-helper.zeabur.app/ 可以訪問）
2. ✅ GitHub Actions workflow 執行成功（看到綠色勾號）
3. ❌ 但沒有收到 Telegram 訊息

**原因**：
- GitHub Actions 執行時，需要從 **GitHub Secrets** 讀取 API 金鑰
- 如果 GitHub Secrets 沒有正確設置，workflow 執行時就讀不到金鑰
- 即使 Zeabur 的環境變量設置正確，GitHub Actions 也讀不到！

---

## ✅ 解決方法

### 步驟 1：確認 GitHub Secrets 是否正確設置

1. 前往 GitHub 倉庫
2. Settings → Secrets and variables → Actions
3. 確認以下 5 個 Secrets **都存在且值正確**：

| Secret 名稱 | 必須存在 |
|------------|---------|
| `TG_TOKEN` | ✅ |
| `CHAT_ID` | ✅ |
| `CG_GECKO_API_KEY` | ✅ |
| `CG_API_KEY` | ✅ |
| `TG_THREAD_SECTOR_RANKING`（或其他對應的 Thread ID） | ✅ |

### 步驟 2：檢查執行日誌

1. 前往 Actions 頁面
2. 點擊一個執行記錄
3. 點擊「Run sector ranking task」步驟
4. 查看日誌，尋找：
   - 是否有錯誤訊息
   - 是否看到「Telegram 訊息發送成功」
   - 或是否看到「發送失敗」的錯誤

### 步驟 3：測試 API 金鑰是否正確

如果 Secrets 設置了但還是不行，可能是：
- API 金鑰值不正確
- Telegram Token 或 Chat ID 錯誤

---

## 🧪 快速測試

### 測試 1：檢查 Secrets 是否被讀取

在執行日誌中搜索：
- 如果看到 `KeyError` 或 `None` → Secrets 未設置
- 如果看到 `401 Unauthorized` → Token 或 Chat ID 錯誤
- 如果看到 `Telegram 訊息發送成功` → 應該收到訊息

### 測試 2：確認 Thread ID

如果訊息發送成功但沒收到，可能是 Thread ID 錯誤：
- 確認 Telegram 群組/頻道有啟用話題功能
- 確認 Thread ID 是正確的

---

## 📋 請立即檢查

請告訴我執行日誌的內容：

1. **點擊任意一個執行記錄**（例如：主流板塊排行榜推播）
2. **點擊「Run sector ranking task」步驟**
3. **查看日誌最後幾行**
4. **告訴我看到了什麼**：
   - 是否有錯誤訊息？
   - 是否看到「Telegram 訊息發送成功」？
   - 或者看到了什麼？

這樣我才能準確找出問題！


