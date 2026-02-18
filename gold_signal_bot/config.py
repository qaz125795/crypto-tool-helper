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

    # 數據源：yfinance | bingx（BingX 不需 API Key，公開行情）
    # BingX 黃金永續介面: https://bingx.com/zh-tc/perpetual/GOLD(XAU)-USDT ，API 符號 XAU-USDT
    DATA_SOURCE: str = "yfinance"   # 設 GOLD_DATA_SOURCE=bingx 則用 BingX
    SYMBOL_GOLD: str = "GC=F"       # yfinance: GC=F；bingx 時 XAU-USDT（可設 GOLD_SYMBOL 覆寫）
    SYMBOL_DXY: str = "DX-Y.NYB"    # 美元指數（僅 yfinance）

    # ORB 參數 (GOLD_ORB)
    SESSION_START_HOUR_UTC: int = 1   # 交易日起始小時 (UTC)，XAUUSD 約 1:02 server
    CANDLE_COMPOSITION: int = 3       # 區間內至少幾根 K 才視為「確立」
    MAX_TRADES_PER_DAY: int = 2       # 每日最多 1 多 1 空

    # 趨勢濾網 (GOLD_ORB MA100 + Gold-analysis SMA40/100)
    MA_TREND_PERIOD: int = 100       # 趨勢 MA 週期
    SMA_FAST: int = 40
    SMA_SLOW: int = 100

    # 風控 (344/338 PDF: 1% risk, 1.5x ATR SL, min 1:2 RR)
    ATR_PERIOD: int = 14
    ATR_SL_MULTIPLIER: float = 1.5
    MIN_RR_RATIO: float = 2.0        # 最少 1:2
    RISK_PERCENT_PER_TRADE: float = 1.0

    # 濾網開關
    USE_SESSION_FILTER: bool = True   # 僅美盤/歐盤重疊時段
    SESSION_START_UTC: int = 13      # 13:00 UTC = 美盤開
    SESSION_END_UTC: int = 21        # 21:00 UTC
    USE_VOLATILITY_FILTER: bool = True  # BB 寬度 / ATR 過低不交易
    VOLATILITY_ATR_PERCENT_MIN: float = 0.001  # ATR/Close 最低門檻
    USE_DXY_FILTER: bool = True      # 美元指數負相關
    DXY_LOOKBACK: int = 5            # DXY 短期均線週期

    def __post_init__(self):
        self.TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", self.TELEGRAM_BOT_TOKEN)
        self.TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", self.TELEGRAM_CHAT_ID)
        if os.environ.get("GOLD_SYMBOL"):
            self.SYMBOL_GOLD = os.environ.get("GOLD_SYMBOL", self.SYMBOL_GOLD)
        if os.environ.get("GOLD_DATA_SOURCE", "").strip().lower() == "bingx":
            self.DATA_SOURCE = "bingx"
            if not os.environ.get("GOLD_SYMBOL"):
                self.SYMBOL_GOLD = "XAU-USDT"  # BingX GOLD(XAU)-USDT 永續


def get_config() -> Config:
    return Config()
