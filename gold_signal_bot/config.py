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
    # BingX 黃金永續介面: https://bingx.com/zh-tc/perpetual/GOLD(XAU)-USDT ，API 符號與連結一致
    DATA_SOURCE: str = "yfinance"   # 設 GOLD_DATA_SOURCE=bingx 則用 BingX
    SYMBOL_GOLD: str = "GC=F"       # yfinance: GC=F；bingx 時 GOLD(XAU)-USDT（可設 GOLD_SYMBOL 覆寫）
    SYMBOL_DXY: str = "DX-Y.NYB"    # 美元指數（僅 yfinance）

    # ORB 參數 (GOLD_ORB)（已放寬：區間內 2 根 K 即確立，較易出訊號）
    SESSION_START_HOUR_UTC: int = 1   # 交易日起始小時 (UTC)，XAUUSD 約 1:02 server
    CANDLE_COMPOSITION: int = 2       # 區間內至少幾根 K 才視為「確立」（原 3，改 2 較寬鬆）
    MAX_TRADES_PER_DAY: int = 2       # 每日最多 1 多 1 空
    # 區間凍結：開盤後前 N 根 K 確立 ORB 區間後凍結，不再擴張（0 = 不限制，維持原行為）
    # 建議值 4：亞洲盤 4 小時確立區間（01:00~05:00 UTC），倫敦/紐約盤突破才出訊號
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
        if os.environ.get("GOLD_SYMBOL"):
            self.SYMBOL_GOLD = os.environ.get("GOLD_SYMBOL", self.SYMBOL_GOLD)
        if os.environ.get("GOLD_DATA_SOURCE", "").strip().lower() == "bingx":
            self.DATA_SOURCE = "bingx"
            if not os.environ.get("GOLD_SYMBOL"):
                self.SYMBOL_GOLD = "GOLD(XAU)-USDT"  # 與網頁連結 https://bingx.com/zh-tc/perpetual/GOLD(XAU)-USDT 一致


def get_config() -> Config:
    return Config()
