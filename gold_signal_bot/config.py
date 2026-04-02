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
    CANDLE_COMPOSITION: int = 3       # 區間內至少幾根 K 才視為「確立」（收緊以降低假突破）
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

    # 突破強度：收盤需超過箱體實體邊至少「此倍數 × ATR」，過濾毛刺假突破
    MIN_BREAKOUT_ATR_MULT: float = 0.12

    # 濾網（偏勝率：流動主力時段 + 美元方向 + 波動/動能）
    USE_SESSION_FILTER: bool = True   # 僅倫敦～紐約活躍時段（UTC）
    SESSION_START_UTC: int = 7       # 倫敦開盤前後
    SESSION_END_UTC: int = 22        # 紐約午後前
    USE_VOLATILITY_FILTER: bool = True
    VOLATILITY_ATR_PERCENT_MIN: float = 0.00125  # ATR/Close 略提高，盤整少做
    USE_DXY_FILTER: bool = True      # 與 DXY 短期走勢負相關才出單
    DXY_LOOKBACK: int = 5

    # RSI：避免極端追高殺低
    USE_RSI_FILTER: bool = True
    RSI_LONG_MAX: float = 66.0      # 多單：RSI 不高於此（預設 14 週期）
    RSI_SHORT_MIN: float = 34.0     # 空單：RSI 不低於此

    # SMA40 / SMA100 排列：順勢突破（多：快線在上；空：快線在下）
    USE_MA_STACK_FILTER: bool = True

    def __post_init__(self):
        self.TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", self.TELEGRAM_BOT_TOKEN)
        self.TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", self.TELEGRAM_CHAT_ID)


def get_config() -> Config:
    return Config()
