# 設定不同 Thread ID 指南

## 🎯 需求說明

您想要為每個 workflow 設定不同的 Telegram Thread ID，這樣不同的推播會發送到不同的話題中。

## ✅ 方法 1：為每個 Workflow 單獨設定（推薦）

這是最靈活的方式，每個 workflow 都可以有獨立的 Thread ID。

### 步驟 1：在 GitHub Secrets 中添加各個 Thread ID

1. 前往 **Settings** → **Secrets and variables** → **Actions**
2. 點擊 **New repository secret**
3. 為每個 workflow 添加對應的 Thread ID Secret：

| Secret 名稱 | 說明 | 範例值 |
|------------|------|--------|
| `TG_THREAD_SECTOR_RANKING` | 主流板塊排行榜的 Thread ID | `5` |
| `TG_THREAD_WHALE_POSITION` | 巨鯨持倉動向的 Thread ID | `246` |
| `TG_THREAD_POSITION_CHANGE` | 持倉變化篩選的 Thread ID | `250` |
| `TG_THREAD_ECONOMIC_DATA` | 重要經濟數據推播的 Thread ID | `13` |
| `TG_THREAD_NEWS` | 新聞快訊推播的 Thread ID | `7` |
| `TG_THREAD_FUNDING_RATE` | 資金費率排行榜的 Thread ID | `244` |
| `TG_THREAD_LONG_TERM_INDEX` | 長線牛熊導航儀的 Thread ID | `248` |
| `TG_THREAD_LIQUIDITY_RADAR` | 流動性獵取雷達的 Thread ID | `3` |
| `TG_THREAD_ALTSEASON_RADAR` | 山寨爆發雷達的 Thread ID | `254` |

**添加步驟**：
- 每個 Secret 的 **Name** 使用上面的名稱（例如：`TG_THREAD_SECTOR_RANKING`）
- **Secret** 填入對應的 Thread ID 數字（例如：`5`）
- 點擊 **Add secret**

### 步驟 2：確認 Workflow 文件已配置

✅ **好消息**：所有 workflow 文件已經配置好了！它們會自動使用對應的 Thread ID Secret。

例如：
- `sector-ranking.yml` 會使用 `TG_THREAD_SECTOR_RANKING`
- `whale-position.yml` 會使用 `TG_THREAD_WHALE_POSITION`
- 以此類推...

### 步驟 3：完成！

設定完成後，每個 workflow 執行時會：
1. 讀取對應的 Thread ID Secret
2. 使用該 Thread ID 發送訊息到正確的話題

---

## ✅ 方法 2：使用 JSON 格式一次設定所有（適合批量設定）

如果您想一次設定所有 Thread IDs，可以使用 JSON 格式。

### 步驟 1：準備 JSON 格式的 Thread IDs

建立一個包含所有 Thread IDs 的 JSON：

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

**⚠️ 注意**：JSON 格式必須是單行，不能換行！正確格式：
```
{"sector_ranking":5,"whale_position":246,"position_change":250,"economic_data":13,"news":7,"funding_rate":244,"long_term_index":248,"liquidity_radar":3,"altseason_radar":254}
```

### 步驟 2：在 GitHub Secrets 中添加

1. 前往 **Settings** → **Secrets and variables** → **Actions**
2. 點擊 **New repository secret**
3. **Name**: 輸入 `TG_THREAD_IDS`
4. **Secret**: 貼上上面的 JSON（單行格式）
5. 點擊 **Add secret**

### 步驟 3：完成！

所有 workflow 會自動讀取這個 JSON 並使用對應的 Thread ID。

---

## 🎯 推薦設定方式

### 如果您想要**最靈活的控制**：
✅ **使用方法 1**：為每個 workflow 單獨設定 Secret
- 優點：可以單獨修改某個 workflow 的 Thread ID，不影響其他
- 適合：需要經常調整或不同 workflow 需要不同 Thread ID

### 如果您想要**快速批量設定**：
✅ **使用方法 2**：使用 JSON 格式
- 優點：一次設定所有，方便管理
- 適合：Thread IDs 相對固定，不常變更

---

## 📝 設定範例

假設您的 Thread IDs 是：

| Workflow | 您的 Thread ID |
|----------|---------------|
| 主流板塊排行榜 | `100` |
| 巨鯨持倉動向 | `200` |
| 持倉變化篩選 | `300` |
| 重要經濟數據 | `400` |
| 新聞快訊 | `500` |
| 資金費率 | `600` |
| 長線牛熊導航儀 | `700` |
| 流動性獵取雷達 | `800` |
| 山寨爆發雷達 | `900` |

### 使用方法 1：

添加以下 Secrets：
- `TG_THREAD_SECTOR_RANKING` = `100`
- `TG_THREAD_WHALE_POSITION` = `200`
- `TG_THREAD_POSITION_CHANGE` = `300`
- `TG_THREAD_ECONOMIC_DATA` = `400`
- `TG_THREAD_NEWS` = `500`
- `TG_THREAD_FUNDING_RATE` = `600`
- `TG_THREAD_LONG_TERM_INDEX` = `700`
- `TG_THREAD_LIQUIDITY_RADAR` = `800`
- `TG_THREAD_ALTSEASON_RADAR` = `900`

### 使用方法 2：

添加一個 Secret：
- **Name**: `TG_THREAD_IDS`
- **Secret**: `{"sector_ranking":100,"whale_position":200,"position_change":300,"economic_data":400,"news":500,"funding_rate":600,"long_term_index":700,"liquidity_radar":800,"altseason_radar":900}`

---

## ⚠️ 重要提醒

1. **不要兩種方法同時使用**：
   - 如果設定了 JSON 格式的 `TG_THREAD_IDS`，它會優先使用
   - 單獨設定的 Thread ID Secrets 會被忽略
   - **建議只使用其中一種方法**

2. **Thread ID 必須是數字**：
   - 不要加引號：❌ `"100"` → ✅ `100`
   - 直接輸入數字即可

3. **如果某個 workflow 沒有設定 Thread ID**：
   - 會使用預設值（顯示在 workflow 文件中）
   - 或者發送到主聊天室（如果沒有設定 `message_thread_id`）

---

## 🧪 測試

設定完成後：

1. **回到 Actions 頁面**
2. **選擇一個 workflow**（例如：主流板塊排行榜推播）
3. **點擊「Run workflow」**
4. **檢查 Telegram 訊息是否發送到正確的 Thread**

如果發送到錯誤的 Thread，檢查：
- Secret 名稱是否正確
- Thread ID 是否正確
- Secret 是否已添加

---

## ❓ 需要幫助？

如果設定時遇到問題，請告訴我：
1. 您想使用哪種方法？（方法 1 或方法 2）
2. 您的 Thread IDs 是什麼？
3. 遇到了什麼錯誤？

我可以幫您生成正確的設定！


