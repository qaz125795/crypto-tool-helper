# 定時任務設置指南

## 持倉變化篩選 - 每 15 分鐘執行一次

### cron-job.org 設置

**任務名稱**: `JackBot - 持倉變化篩選（全幣種）`

**URL**: 
```
https://crypto-tool-helper.zeabur.app/position_change
```

**方法**: `GET` 或 `POST`

**Cron 表達式**: 
```
*/15 * * * *
```
（每 15 分鐘執行一次）

**時區**: `Asia/Taipei`

**說明**: 
- 抓取全部 904 個幣種
- 每 15 分鐘執行一次
- 執行時間約 10-15 分鐘（因為需要處理 904 個幣種）

---

## 完整定時任務列表

### 1. 主流板塊排行榜
- **URL**: `https://crypto-tool-helper.zeabur.app/sector_ranking`
- **Cron**: `0 * * * *`（每小時整點）
- **頻率**: 每小時

### 2. 巨鯨持倉動向
- **URL**: `https://crypto-tool-helper.zeabur.app/whale_position`
- **Cron**: `5 * * * *`（每小時第 5 分鐘）
- **頻率**: 每小時

### 3. 持倉變化篩選 ⭐ (已更新為 15 分鐘)
- **URL**: `https://crypto-tool-helper.zeabur.app/position_change`
- **Cron**: `*/15 * * * *`（每 15 分鐘）
- **頻率**: 每 15 分鐘
- **說明**: 抓取全部 904 個幣種

### 4. 重要經濟數據推播
- **URL**: `https://crypto-tool-helper.zeabur.app/economic_data`
- **Cron**: `*/10 * * * *`（每 10 分鐘）
- **頻率**: 每 10 分鐘

### 5. 新聞快訊推播
- **URL**: `https://crypto-tool-helper.zeabur.app/news`
- **Cron**: `* * * * *`（每分鐘）
- **頻率**: 每分鐘

### 6. 資金費率排行榜
- **URL**: `https://crypto-tool-helper.zeabur.app/funding_rate`
- **Cron**: `0 0,4,8,12,16,20 * * *`（每天 0,4,8,12,16,20 點）
- **頻率**: 每天 6 次

---

## 設置步驟

1. 前往 https://cron-job.org
2. 登入或註冊帳號
3. 點擊「Create cronjob」或「新增任務」
4. 按照上面的設置填寫
5. 保存並啟動任務

---

## 注意事項

⚠️ **持倉變化篩選任務**：
- 執行時間較長（約 10-15 分鐘）
- 每 15 分鐘執行一次，避免執行時間重疊
- 如果執行時間超過 15 分鐘，建議調整為每 20 或 30 分鐘執行一次

✅ **建議調整**：
如果發現任務執行時間超過 15 分鐘，可以：
- 改為每 20 分鐘：`*/20 * * * *`
- 或每 30 分鐘：`*/30 * * * *`

