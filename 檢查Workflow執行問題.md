# 檢查 Workflow 執行問題指南

## 🔍 問題診斷步驟

### 步驟 1：查看執行日誌

1. **點擊任意一個執行記錄**（例如：主流板塊排行榜推播）

2. **點擊執行記錄後**，會展開顯示步驟：
   ```
   ✓ Checkout code
   ✓ Set up Python
   ✓ Install dependencies
   ✓ Run sector ranking task  ← 點擊這個查看詳細日誌
   ```

3. **點擊「Run sector ranking task」步驟**，查看詳細日誌

4. **查看日誌中是否有錯誤訊息**：
   - 尋找紅色錯誤訊息
   - 尋找「Telegram API 錯誤」
   - 尋找「發送 Telegram 訊息失敗」

---

## 🔍 常見問題和解決方法

### 問題 0：全部掛掉 — Runner 無法取得 / Internal server error

**症狀**：Annotations 顯示  
- `The job was not acquired by Runner of type hosted even after multiple attempts`  
- `Internal server error. Correlation ID: ...`

**說明**：這是 **GitHub 服務端** 問題，不是 Secrets 或程式錯誤。

**解決方法**：
1. 查看 [GitHub Status](https://www.githubstatus.com)，確認 Actions 是否正常。
2. 服務恢復後，到 Actions 頁面對失敗的 run 點 **Re-run all jobs**。
3. 詳細說明見：[GitHub_Actions_Runner與伺服器錯誤排查.md](./GitHub_Actions_Runner與伺服器錯誤排查.md)

---

### 問題 1：Secrets 未設置或名稱錯誤

**症狀**：日誌中顯示 `KeyError` 或 `None` 相關錯誤

**檢查方法**：
1. 前往 Settings → Secrets and variables → Actions
2. 確認以下 Secrets 都存在：
   - ✅ `TG_TOKEN`
   - ✅ `CHAT_ID`
   - ✅ `CG_GECKO_API_KEY`
   - ✅ `CG_API_KEY`
   - ✅ `TG_THREAD_SECTOR_RANKING`（或對應的 Thread ID）

**解決方法**：
- 如果缺少，添加缺失的 Secret
- 如果名稱不對，刪除錯誤的並重新添加正確的

---

### 問題 2：Telegram Bot Token 或 Chat ID 錯誤

**症狀**：日誌中顯示「Telegram API 錯誤」或「401 Unauthorized」

**檢查方法**：
1. 確認 `TG_TOKEN` 的值是否正確
   - 格式應該是：`123456789:ABCdefGHIjklMNOpqrsTUVwxyz`
   - 不要有多餘的空格或引號

2. 確認 `CHAT_ID` 的值是否正確
   - 應該是數字（可能是負數，例如：`-1003611242392`）
   - 不要有多餘的空格或引號

**解決方法**：
- 重新檢查並更新 Secret 的值
- 確保沒有複製到多餘的空格

---

### 問題 3：Thread ID 不存在或錯誤

**症狀**：訊息發送成功，但沒有收到（可能發送到錯誤的 Thread）

**檢查方法**：
1. 確認 Telegram 群組/頻道有啟用話題（Topics）功能
2. 確認 Thread ID 是否正確：
   - 在 Telegram 群組中，點擊話題名稱
   - 查看 URL，Thread ID 通常在 URL 中

**解決方法**：
- 如果沒有使用話題，Thread ID 應該設為 `None` 或 `0`
- 或者確保 Thread ID 是正確的

---

### 問題 4：執行成功但沒有數據

**症狀**：日誌顯示「執行成功」，但沒有發送訊息

**可能原因**：
- API 返回空數據（沒有新的數據需要推送）
- 例如：經濟數據沒有新數據、新聞沒有更新等

**解決方法**：
- 這是正常的！如果 API 沒有新數據，就不會發送訊息
- 可以測試「持倉變化篩選」，它應該會返回數據

---

## 🧪 測試方法

### 測試 1：檢查 Secrets 是否正確讀取

在日誌中搜索以下關鍵字：
- `TG_TOKEN` - 應該不會顯示實際值（GitHub 會隱藏）
- 如果看到 `None` 或 `KeyError`，表示 Secret 未設置

### 測試 2：檢查 Telegram API 調用

在日誌中搜索：
- `Telegram 訊息發送成功` - 表示發送成功
- `Telegram API 錯誤` - 表示發送失敗
- `401` 或 `403` - 表示認證錯誤

### 測試 3：測試持倉變化篩選

這個 workflow 應該會返回數據並發送訊息：
1. 點擊「持倉變化篩選」workflow
2. 點擊「Run workflow」
3. 等待執行完成
4. 查看日誌
5. 檢查 Telegram

---

## 📋 請告訴我

請檢查執行日誌並告訴我：

1. **點擊一個執行記錄**（例如：主流板塊排行榜推播）
2. **點擊「Run sector ranking task」步驟**
3. **複製日誌中的錯誤訊息**（如果有）
4. **或者告訴我日誌最後幾行的內容**

這樣我才能幫您找到問題！


