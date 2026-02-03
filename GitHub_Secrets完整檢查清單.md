# GitHub Secrets 完整檢查清單

## ✅ 您已經添加的（Thread IDs）

根據截圖，您已經添加了 9 個 Thread ID Secrets：
- ✅ `TG_THREAD_ALTSEASON_RADAR`
- ✅ `TG_THREAD_ECONOMIC_DATA`
- ✅ `TG_THREAD_FUNDING_RATE`
- ✅ `TG_THREAD_LIQUIDITY_RADAR`
- ✅ `TG_THREAD_LONG_TERM_INDEX`
- ✅ `TG_THREAD_NEWS`
- ✅ `TG_THREAD_POSITION_CHANGE`
- ✅ `TG_THREAD_SECTOR_RANKING`
- ✅ `TG_THREAD_WHALE_POSITION`

---

## ⚠️ 還需要添加的（必需的 API 金鑰）

這 5 個 Secrets **必須添加**，否則 workflow 無法執行！

### 1. Telegram Bot Token

- **Secret 名稱**: `TG_TOKEN`
- **說明**: Telegram Bot 的 Token
- **在哪裡找**: 
  - 在 Telegram 搜尋 `@BotFather`
  - 發送 `/mybots`
  - 選擇您的 Bot
  - 點擊 "API Token"
  - 複製 Token（格式：`123456789:ABCdefGHIjklMNOpqrsTUVwxyz`）

### 2. Telegram Chat ID

- **Secret 名稱**: `CHAT_ID`
- **說明**: Telegram 群組或頻道的 ID
- **在哪裡找**:
  - **群組**: 將 Bot 加入群組後，發送任意訊息
  - 訪問：`https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
  - 在 JSON 中找到 `"chat":{"id":-1001234567890}` 的數字
  - **頻道**: 通常是 `-100` 開頭的負數（例如：`-1003611242392`）

### 3. CoinGecko API 金鑰

- **Secret 名稱**: `CG_GECKO_API_KEY`
- **說明**: CoinGecko API 金鑰
- **在哪裡找**:
  - 前往：https://www.coingecko.com/en/api
  - 註冊/登入帳號
  - 申請 API 金鑰
  - 複製 API 金鑰（格式：`CG-RR9dam92RCAGpdV5VF7km59o`）

### 4. CoinGlass API 金鑰

- **Secret 名稱**: `CG_API_KEY`
- **說明**: CoinGlass API 金鑰
- **在哪裡找**:
  - 前往：https://www.coinglass.com/
  - 註冊/登入帳號
  - 前往 API 設置頁面
  - 生成並複製 API 金鑰（格式：`4a2fd6ee6d2e49b091d81f1cfdf6315c`）

### 5. Tree of Alpha API 金鑰

- **Secret 名稱**: `TREE_API_KEY`
- **說明**: Tree of Alpha API 金鑰
- **在哪裡找**:
  - 前往：https://news.treeofalpha.com/
  - 註冊/登入帳號
  - 前往 API 設置頁面
  - 生成並複製 API 金鑰（格式：`131c5449bc84d0b1f9cb17f399c62c21f9f4c06a70d0911e76cfbfa8cdbc070d`）

---

## 📋 完整添加步驟

### 步驟 1：點擊「New repository secret」

在 Secrets 頁面右上角，點擊綠色的「New repository secret」按鈕

### 步驟 2：逐個添加以下 5 個 Secrets

#### Secret 1: TG_TOKEN

1. **Name**: `TG_TOKEN`
2. **Secret**: 貼上您的 Telegram Bot Token
3. 點擊「Add secret」

#### Secret 2: CHAT_ID

1. **Name**: `CHAT_ID`
2. **Secret**: 貼上您的 Telegram Chat ID（數字，例如：`-1003611242392`）
3. 點擊「Add secret」

#### Secret 3: CG_GECKO_API_KEY

1. **Name**: `CG_GECKO_API_KEY`
2. **Secret**: 貼上您的 CoinGecko API 金鑰
3. 點擊「Add secret」

#### Secret 4: CG_API_KEY

1. **Name**: `CG_API_KEY`
2. **Secret**: 貼上您的 CoinGlass API 金鑰
3. 點擊「Add secret」

#### Secret 5: TREE_API_KEY

1. **Name**: `TREE_API_KEY`
2. **Secret**: 貼上您的 Tree of Alpha API 金鑰
3. 點擊「Add secret」

---

## ✅ 完成後的檢查清單

完成後，您的 Secrets 列表應該總共有 **14 個 Secrets**：

### Thread IDs (9 個)：
- ✅ TG_THREAD_ALTSEASON_RADAR
- ✅ TG_THREAD_ECONOMIC_DATA
- ✅ TG_THREAD_FUNDING_RATE
- ✅ TG_THREAD_LIQUIDITY_RADAR
- ✅ TG_THREAD_LONG_TERM_INDEX
- ✅ TG_THREAD_NEWS
- ✅ TG_THREAD_POSITION_CHANGE
- ✅ TG_THREAD_SECTOR_RANKING
- ✅ TG_THREAD_WHALE_POSITION

### API 金鑰 (5 個)：
- ✅ TG_TOKEN
- ✅ CHAT_ID
- ✅ CG_GECKO_API_KEY
- ✅ CG_API_KEY
- ✅ TREE_API_KEY

---

## 🎯 添加完成後

當所有 14 個 Secrets 都添加完成後：

1. ✅ **回到 Actions 頁面**
2. ✅ **選擇一個 workflow**（例如：主流板塊排行榜推播）
3. ✅ **點擊「Run workflow」測試執行**
4. ✅ **檢查 Telegram 是否收到訊息**

---

## ⚠️ 重要提醒

1. **Secret 名稱必須完全正確**（大小寫、底線都要對）
2. **Secret 的值不要有空格**（複製貼上時注意）
3. **添加後無法再次查看值**，如果填錯需要重新添加
4. **TG_TOKEN 和 CHAT_ID 是必需的**，沒有這兩個無法發送 Telegram 訊息
5. **API 金鑰如果沒有**，對應的功能會失敗，但不影響其他功能

---

## 🚀 現在開始添加

請按照上面的步驟，逐個添加 5 個 API 金鑰 Secrets。

**如果您已經有這些 API 金鑰**：
- 直接複製貼上即可

**如果您還沒有這些 API 金鑰**：
- 需要先去對應網站註冊並申請 API 金鑰
- 這可能需要一些時間

添加完成後告訴我，我們可以測試執行第一個 workflow！


