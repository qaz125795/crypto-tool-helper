# 法人級黃金多空訊號機器人

融合 **XAUUSD AI (344/338)**、**GOLD_ORB**、**Gold-analysis** 三套邏輯，以 yfinance 取代 MT5，在 GitHub Actions 上定時運行並透過 Telegram 發送多空訊號。

## 策略摘要

- **ORB (Opening Range Breakout)**：以交易日起始 1h 建立區間，區間內至少 3 根 K 後突破/跌破 + 同向 K 棒才出訊號。
- **趨勢濾網**：多單僅在收盤 > MA100、空單僅在收盤 < MA100（GOLD_ORB）。
- **風控 (344/338)**：1.5× ATR 止損、最少 1:2 報酬風險比。
- **濾網**：可選交易時段、波動率(ATR/Close)、DXY 負相關。

## 環境

- Python 3.9+
- 依賴：`pandas`, `numpy`, `yfinance`, `requests`
- **數據源**：預設 yfinance (GC=F)。設環境變數 **`GOLD_DATA_SOURCE=bingx`** 則改為 BingX **XAU-USDT** 永續報價（公開 API，無需 Key）。

## 安裝與本機執行

```bash
cd gold_signal_bot
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN="你的 Bot Token"
export TELEGRAM_CHAT_ID="你的 Chat ID"
python main.py
```

## GitHub Actions 部署

1. 將本專案推至 GitHub（可單獨 repo 或放在 `黃金策略/gold_signal_bot` 下整份推送）。
2. 在 Repo **Settings → Secrets and variables → Actions** 新增：
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
3. 預設為每小時整點 (UTC) 執行；可於 `.github/workflows/signal.yml` 修改 `cron` 或使用 **Run workflow** 手動觸發。

## 專案結構

```
gold_signal_bot/
├── main.py              # 入口：拉數據 → 訊號 → 濾網 → Telegram
├── config.py            # 設定（可改為 .env）
├── data_provider.py     # yfinance 數據
├── indicators.py        # ATR, SMA, EMA, RSI, BB
├── strategy_orb.py      # ORB + MA + ATR 止損/止盈
├── filters.py           # 時段 / 波動率 / DXY 濾網
├── telegram_sender.py   # 訊號格式與發送
├── requirements.txt
├── README.md
└── .github/workflows/signal.yml
```

## 免責聲明

本程式僅供學習與研究，不保證獲利。實盤前請自行回測與驗證，並注意風險。
