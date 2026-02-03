# 修復 Telegram 404 錯誤指南

## ❌ 錯誤訊息

```
Telegram HTTP 錯誤: 404 - {"ok":false,"error_code":404,"description":"Not Found"}
```

## 🔍 問題原因

404 Not Found 通常表示：
1. **CHAT_ID 錯誤**：Chat ID 不存在或格式不正確
2. **TG_TOKEN 錯誤**：Bot Token 不正確或已失效
3. **Bot 未加入群組**：Bot 沒有加入到指定的群組/頻道
4. **Thread ID 不存在**：如果使用 Thread ID，該話題可能不存在

---

## ✅ 解決步驟

### 步驟 1：確認 Telegram Bot Token

1. **在 Telegram 搜尋 `@BotFather`**
2. **發送 `/mybots`**
3. **選擇您的 Bot**
4. **點擊 "API Token"**
5. **複製 Token**（格式：`123456789:ABCdefGHIjklMNOpqrsTUVwxyz`）

6. **在 GitHub Secrets 中更新**：
   - Settings → Secrets and variables → Actions
   - 找到 `TG_TOKEN`
   - 點擊編輯（鉛筆圖標）
   - 更新為正確的 Token
   - 保存

### 步驟 2：確認 Chat ID

#### 方法 A：獲取群組 Chat ID

1. **將 Bot 加入群組**
2. **在群組中發送任意訊息**
3. **訪問**：`https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
   - 將 `<YOUR_TOKEN>` 替換為您的 Bot Token
4. **在返回的 JSON 中查找**：
   ```json
   "chat":{"id":-1001234567890}
   ```
   - 這個數字就是 Chat ID

#### 方法 B：獲取頻道 Chat ID

頻道 Chat ID 通常是：
- `-100` 開頭的負數（例如：`-1003611242392`）
- 可以通過轉發頻道訊息到 `@userinfobot` 來確認

#### 方法 C：使用 Bot 確認

1. **將 Bot 加入群組/頻道**
2. **發送 `/start` 給 Bot**
3. **訪問**：`https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
4. **查看返回的 JSON 中的 `chat.id`**

### 步驟 3：確認 Bot 已加入群組/頻道

1. **確保 Bot 已經是群組/頻道的成員**
2. **如果使用頻道**，確保 Bot 是管理員或有發送訊息權限

### 步驟 4：確認 Thread ID（如果使用）

1. **確認群組已啟用話題（Topics）功能**
2. **確認 Thread ID 是否正確**
3. **如果沒有使用話題**，Thread ID 應該是 `0` 或 `None`

---

## 🧪 測試方法

### 測試 1：測試 Bot Token 是否正確

訪問（將 `<YOUR_TOKEN>` 替換為實際 Token）：
```
https://api.telegram.org/bot<YOUR_TOKEN>/getMe
```

**應該返回**：
```json
{"ok":true,"result":{"id":123456789,"is_bot":true,"first_name":"Your Bot Name",...}}
```

**如果返回錯誤**：Token 不正確

### 測試 2：測試 Chat ID 是否正確

訪問（替換 `<YOUR_TOKEN>` 和 `<CHAT_ID>`）：
```
https://api.telegram.org/bot<YOUR_TOKEN>/getChat?chat_id=<CHAT_ID>
```

**應該返回**：
```json
{"ok":true,"result":{"id":-1001234567890,"title":"群組名稱",...}}
```

**如果返回 404**：Chat ID 不正確或 Bot 未加入

---

## 🔧 在 GitHub Secrets 中更新

### 更新 TG_TOKEN

1. Settings → Secrets and variables → Actions
2. 找到 `TG_TOKEN`
3. 點擊編輯（鉛筆圖標）
4. 更新為正確的 Token
5. 保存

### 更新 CHAT_ID

1. Settings → Secrets and variables → Actions
2. 找到 `CHAT_ID`
3. 點擊編輯（鉛筆圖標）
4. 更新為正確的 Chat ID（數字，不要引號）
5. 保存

---

## 📋 檢查清單

完成以下檢查：

- [ ] ✅ Bot Token 正確（格式：`123456789:ABCdef...`）
- [ ] ✅ Chat ID 正確（數字，例如：`-1003611242392`）
- [ ] ✅ Bot 已加入群組/頻道
- [ ] ✅ Bot 有發送訊息權限（如果是頻道，需要是管理員）
- [ ] ✅ GitHub Secrets 中的值已更新
- [ ] ✅ Thread ID 正確（如果使用）

---

## 🚀 更新後重新測試

1. **更新 Secrets 後**
2. **回到 Actions 頁面**
3. **重新執行 workflow**（點擊「Run workflow」）
4. **查看日誌確認是否成功**

---

## ❓ 需要協助？

如果還是不行，請告訴我：

1. **Bot Token 測試結果**：訪問 `getMe` API 是否成功？
2. **Chat ID 測試結果**：訪問 `getChat` API 是否成功？
3. **Bot 是否已加入群組/頻道**？
4. **使用的 Chat ID 是什麼**？（可以部分遮蓋，例如：`-1003611242xxx`）

我可以幫您進一步診斷問題！


