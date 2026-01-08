# 區塊鏈船長—傑克：自動化推播系統

這是一個整合多種功能的加密貨幣市場監控和自動推播系統，所有功能都已整合到單一 Python 文件中。

## 功能模塊

1. **主流板塊排行榜推播** - 抓取 CoinGecko 的主流板塊排行榜
2. **巨鯨持倉動向** - 監控 CoinGlass 的巨鯨持倉數據
3. **持倉變化篩選** - 監控持倉變化，分類為多方開倉/平倉、空方開倉/平倉（支援 904 個幣種）
4. **重要經濟數據推播** - 抓取 CoinGlass 的經濟數據並推送
5. **新聞快訊推播** - 抓取 Tree of Alpha 和 CoinGlass 的新聞並翻譯推送
6. **資金費率排行榜** - 抓取幣安資金費率排行榜
7. **長線牛熊導航儀** - 整合 Ahr999、彩虹圖、Pi 循環指標、恐懼貪婪指數
8. **流動性獵取雷達** - 監控極端爆倉事件
9. **山寨爆發雷達** - Altcoin Season 指數 + RSI + Buy Ratio 分析

## 安裝步驟

1. 安裝 Python 3.7 或更高版本

2. 安裝依賴套件：
```bash
pip install -r requirements.txt
```

## 配置說明

在 `jackbot.py` 文件中，請確認以下配置：

- `CG_GECKO_API_KEY` - CoinGecko API 密鑰
- `CG_API_KEY` - CoinGlass API 密鑰
- `TREE_API_KEY` - Tree of Alpha API 密鑰
- `TG_TOKEN` - Telegram Bot Token
- `CHAT_ID` - Telegram 聊天室 ID
- `TG_THREAD_IDS` - 各功能對應的 Telegram 話題 ID

## 使用方法

執行單一功能：

```bash
# 主流板塊排行榜推播
python jackbot.py sector_ranking

# 巨鯨持倉動向
python jackbot.py whale_position

# 持倉變化篩選
python jackbot.py position_change

# 重要經濟數據推播
python jackbot.py economic_data

# 新聞快訊推播
python jackbot.py news

# 資金費率排行榜
python jackbot.py funding_rate
```

## 定時任務設置

### 🌟 推薦：使用 GitHub Actions（推薦）

**優點**：
- ✅ 無執行時間限制（單次最多 6 小時）
- ✅ 完全免費（公開倉庫）
- ✅ 完整的執行日誌
- ✅ 可以手動觸發測試
- ✅ 無需自己的伺服器

**設置步驟**：
1. 將代碼推送到 GitHub 倉庫
2. 設置 GitHub Secrets（API 金鑰等）
3. 啟用 GitHub Actions
4. 完成！

詳細設置指南請查看：[`GITHUB_ACTIONS_SETUP.md`](GITHUB_ACTIONS_SETUP.md)

---

### 本地執行：Windows (使用工作排程器)

1. 打開「工作排程器」
2. 建立基本工作
3. 設定觸發器（例如：每小時執行）
4. 設定動作：啟動程式 `python`，參數為 `jackbot.py sector_ranking`，起始位置為專案目錄

### 本地執行：Linux/Mac (使用 cron)

編輯 crontab：
```bash
crontab -e
```

添加定時任務（範例）：
```bash
# 每小時執行主流板塊排行榜
0 * * * * cd /path/to/JackBot && python jackbot.py sector_ranking

# 每小時執行巨鯨持倉監控
5 * * * * cd /path/to/JackBot && python jackbot.py whale_position

# 每小時執行持倉變化篩選
10 * * * * cd /path/to/JackBot && python jackbot.py position_change

# 每10分鐘執行經濟數據推播
*/10 * * * * cd /path/to/JackBot && python jackbot.py economic_data

# 每分鐘執行新聞快訊推播
* * * * * cd /path/to/JackBot && python jackbot.py news

# 每天 0、4、8、12、16、20 點執行資金費率排行榜
0 0,4,8,12,16,20 * * * cd /path/to/JackBot && python jackbot.py funding_rate
```

## 數據存儲

程式會在專案目錄下創建 `data` 資料夾來存儲：
- 已推送的經濟數據 ID
- 已推送的新聞 ID
- 最後的新聞時間戳

## 注意事項

1. 請確保 API 密鑰有效且有足夠的配額
2. 翻譯功能需要安裝 `googletrans` 套件（可選，未安裝時會使用原文）
3. 建議根據 API 限制調整執行頻率
4. 首次執行前請測試各個功能是否正常運作

## 授權

本專案僅供學習和個人使用。

