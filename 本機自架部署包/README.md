# 本機自架部署包

此資料夾內為「把 JackBot 整包搬到另一台電腦、在本機依排程運行」的說明與範例腳本。  
**程式本體在上一層目錄**（`jackbot.py`、`requirements.txt`、`gold_signal_bot/` 等），  
請連同整個 repo 一起複製或用 `打包帶走.ps1` 壓縮後帶走。

---

## 1. 所有訊號模組一覽（共 14 支）

| 功能名稱 | CLI 指令 | GitHub Actions 原始頻率 | 必要額外套件 |
|----------|---------|------------------------|-------------|
| 持倉狙擊鏡 | `position_change` | 每 5 分鐘（你改為每 **30 分鐘**）| — |
| 鏈上巨鯨動向 | `hyperliquid` | 每 5 分鐘（你改為每 **30 分鐘**）| `ETHERSCAN_API_KEY` |
| 爆擊雷達 | `crit_radar` | 每 15 分鐘（**你指定**）| — |
| 市場地圖 | `screener_board` | 每小時 | — |
| 資金費率排行榜 | `funding_rate` | 每 4 小時 | — |
| 牛市超級燃料箱 | `buying_power_monitor` | 每小時 | — |
| 主力清算雷達 | `liquidity_radar` | 每小時 | `matplotlib` ✅已在 requirements |
| 山寨爆發雷達 | `altseason_radar` | 每小時 | — |
| 黃金獵首 | `gold_signal` | 每小時 | `yfinance` ✅已在 requirements |
| 新聞快訊 | `news` | 每 5 分鐘 | `TREE_API_KEY`（選用）|
| 重要經濟數據即時推播 | `economic_data` | 每 10 分鐘 | — |
| 重要經濟數據今日預告 | `economic_data_preview` | 每日 08:10 台北 | — |
| 板塊排行榜 | `sector_ranking` | 每小時 | — |
| 長線牛熊導航 | `long_term_index_once` | 每日 | — |

---

## 2. 打包帶走：要帶哪些檔

在「本機自架部署包」資料夾執行 `打包帶走.ps1`，會自動把整個專案壓成 zip。  
zip 內含所有必要檔案（見下表），**不含** `venv`、`.git`、`data`、`__pycache__`。

| 必要 | 項目 |
|------|------|
| ✅ 必帶 | `jackbot.py` — 主程式（含全部 14 支訊號） |
| ✅ 必帶 | `whale_wallet_tracker.py` — hyperliquid 巨鯨追蹤 |
| ✅ 必帶 | `kline_card_renderer.py` — K 線圖卡渲染 |
| ✅ 必帶 | `requirements.txt` |
| ✅ 必帶 | `gold_signal_bot/` — 黃金獵首策略模組 |
| ✅ 必帶 | `liquidations_chart/` — 清算雷達繪圖 |
| ✅ 必帶 | `本機自架部署包/` — 本資料夾（排程腳本）|
| ⬛ 可帶 | `data/` — 帶走可延續冷卻／狀態；不帶會全新開始 |
| ❌ 不帶 | `.venv/` 或 `venv/` — 目標機重建較乾淨 |
| ❌ 不帶 | `.git/` — 不需版控可省略 |

---

## 3. 目標電腦環境安裝

```bash
# 進入解壓目錄
cd 加密貨幣推播工具

# 建立虛擬環境
python3.11 -m venv .venv           # Linux / macOS
python -m venv .venv               # Windows

# 啟動
source .venv/bin/activate          # Linux / macOS
.\.venv\Scripts\Activate.ps1       # Windows

# 安裝依賴
pip install -U pip
pip install -r requirements.txt
```

複製 `本機自架部署包/env-keys-對照.example` 為專案根目錄的 `.env`，  
填入你的 `TG_TOKEN`、`CHAT_ID`、`CG_API_KEY` 等。

---

## 4. 手動測試（先測再跑排程）

```bash
python jackbot.py position_change
python jackbot.py hyperliquid
python jackbot.py crit_radar
```

收到 TG 推播就代表環境 OK。

---

## 5. 設定排程

- **Linux / macOS**：見 `linux/crontab-範例.txt`，編輯後 `crontab -e` 貼入。
- **Windows**：見 `windows/工作排程器-建立說明.md`，以及 `windows/run-one.example.bat`。

你指定的排程：

| 模組 | 間隔 | cron |
|------|------|------|
| position_change | 每 30 分鐘 | `*/30 * * * *` |
| hyperliquid | 每 30 分鐘（錯開 5 分）| `5,35 * * * *` |
| crit_radar | 每 15 分鐘 | `*/15 * * * *` |

---

## 6. Gist 冷卻（選用）

本機跑時若要與 GitHub Actions / Zeabur **共用冷卻狀態**（避免跨環境重複推播），  
請在 `.env` 保留 `GIST_ID` 與 `GITHUB_TOKEN`（與 Actions 設定相同）。  
若完全不使用雲端，留空即可，程式會改以本機 `data/` 為主。

---

## 7. 日誌查看

所有執行日誌寫入 `data/jackbot.log`（程式內建），Linux 的 cron 腳本另外記錄到 `data/cron_<模組>_<日期>.log`。
