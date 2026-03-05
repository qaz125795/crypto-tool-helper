# -*- coding: utf-8 -*-
"""
法人級黃金多空訊號機器人 - 設定
融合：XAUUSD AI (344/338)、GOLD_ORB、Gold-analysis
"""
import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class Config:
    # Telegram
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # 數據源：黃金 K 線自動走備援鏈（Gate.io → Bybit → BingX → yfinance），無需手動設定
    # SYMBOL_GOLD 僅作辨識用，實際來源由 fetch_gold_ohlc() 自動決定
    DATA_SOURCE: str = "auto"        # 保留相容性，黃金一律走備援鏈
    SYMBOL_GOLD: str = "XAU"         # 黃金識別符（fetch_ohlc 偵測到 XAU 即走備援鏈）
    SYMBOL_DXY: str = "DX-Y.NYB"    # 美元指數（yfinance）

    # ORB 參數 (GOLD_ORB)
    SESSION_START_HOUR_UTC: int = 1   # 交易日起始小時 (UTC)，用於計算「今日日期」與全日 debug
    CANDLE_COMPOSITION: int = 2       # 區間內至少幾根 K 才視為「確立」（原 3，改 2 較寬鬆）
    MAX_TRADES_PER_DAY: int = 2       # 每日最多 1 多 1 空

    # ── ORB 時段選擇：決定用哪個盤口的 K 線建立突破箱體 ──────────────────────
    # "asia"   → 01:00 UTC 亞洲盤（原行為）
    # "london" → 07:00 UTC 倫敦盤（推薦：歐美主力，假突破率低）
    # "ny"     → 13:00 UTC 紐約盤（波動最大，適合激進策略）
    ORB_SESSION: str = "london"
    LONDON_OPEN_UTC: int = 7          # 倫敦開盤時間 (UTC)，可微調（夏令 7、冬令 8）
    NY_OPEN_UTC: int = 13             # 紐約開盤時間 (UTC)，可微調（夏令 13、冬令 14）

    # 區間凍結：ORB 時段開盤後前 N 根 K 確立箱體後凍結，之後只偵測突破
    # 倫敦：lock=4 → 07-10 UTC 建立區間，11 UTC 起偵測突破
    # 紐約：lock=2 → 13-14 UTC 建立區間，15 UTC 起偵測突破（NY 盤較短，建議用 2）
    RANGE_LOCK_CANDLES: int = 4

    # 趨勢濾網 (GOLD_ORB MA100 + Gold-analysis SMA40/100)
    MA_TREND_PERIOD: int = 100       # 趨勢 MA 週期
    SMA_FAST: int = 40
    SMA_SLOW: int = 100

    # 風控 (344/338 PDF: 1% risk, 1.5x ATR SL, min 1:2 RR)
    ATR_PERIOD: int = 14
    ATR_SL_MULTIPLIER: float = 1.5
    MIN_RR_RATIO: float = 2.0        # 最少 1:2
    RISK_PERCENT_PER_TRADE: float = 1.0

    # 濾網開關（已關閉時段與 DXY，全天只看 ORB+MA）
    USE_SESSION_FILTER: bool = False  # 關閉：全天 24h 都可出訊號
    SESSION_START_UTC: int = 12      # 僅在 USE_SESSION_FILTER=True 時使用
    SESSION_END_UTC: int = 22
    USE_VOLATILITY_FILTER: bool = True  # BB 寬度 / ATR 過低不交易
    VOLATILITY_ATR_PERCENT_MIN: float = 0.001  # ATR/Close 最低門檻
    USE_DXY_FILTER: bool = False     # 關閉：不因美元指數擋單，只看黃金 ORB+MA
    DXY_LOOKBACK: int = 5            # DXY 短期均線週期

    def __post_init__(self):
        self.TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", self.TELEGRAM_BOT_TOKEN)
        self.TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", self.TELEGRAM_CHAT_ID)


def get_config() -> Config:
    return Config()
