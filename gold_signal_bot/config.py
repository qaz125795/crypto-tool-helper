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
    CANDLE_COMPOSITION: int = 2       # 區間內至少幾根 K 才視為「確立」（放寬以避免一週完全無突破）
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
    RANGE_LOCK_CANDLES: int = 3

    # 趨勢濾網 (GOLD_ORB MA100 + Gold-analysis SMA40/100)
    MA_TREND_PERIOD: int = 100       # 趨勢 MA 週期
    SMA_FAST: int = 40
    SMA_SLOW: int = 100

    # 風控 (344/338 PDF: 1% risk, 1.5x ATR SL, min 1:2 RR)
    ATR_PERIOD: int = 14
    ATR_SL_MULTIPLIER: float = 1.5
    MIN_RR_RATIO: float = 2.0        # 最少 1:2
    RISK_PERCENT_PER_TRADE: float = 1.0

    # 突破強度：收盤需超過箱體實體邊至少「此倍數 × ATR」，過濾毛刺假突破（寧缺勿濫）
    MIN_BREAKOUT_ATR_MULT: float = 0.12

    # 濾網（偏勝率：流動主力時段 + 美元方向 + 波動/動能）
    USE_SESSION_FILTER: bool = True   # 交易時段濾網（UTC）
    SESSION_START_UTC: int = 7        # 放寬起點：讓倫敦開盤後更容易觸發
    SESSION_END_UTC: int = 23        # 放寬終點：避免訊號都出現在 21:00 後被整段擋掉
    USE_VOLATILITY_FILTER: bool = True
    VOLATILITY_ATR_PERCENT_MIN: float = 0.00155  # ATR/Close 提高，盤整假突破少做
    USE_DXY_FILTER: bool = True      # 與 DXY 短期走勢負相關才出單
    DXY_LOOKBACK: int = 7            # 稍長視窗，減少單根噪音
    # DXY 相對變動門檻：|收盤變化|/最新價 須達此比例才視為「有效」美元强弱（0=維持舊行為）
    DXY_MIN_REL_MOVE: float = 0.0001  # 約 0.01%，過小視同橫盤不過濾

    # RSI：避免極端追高殺低
    USE_RSI_FILTER: bool = True
    RSI_LONG_MAX: float = 62.0      # 多單：收緊，減少過熱追多
    RSI_SHORT_MIN: float = 38.0     # 空單：收緊，減少過冷追空

    # SMA40 / SMA100 排列：順勢突破（多：快線在上；空：快線在下）
    USE_MA_STACK_FILTER: bool = True

    def __post_init__(self):
        self.TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", self.TELEGRAM_BOT_TOKEN)
        self.TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", self.TELEGRAM_CHAT_ID)


def get_config() -> Config:
    """支援以環境變數覆寫（便於測試）：GOLD_MIN_BREAKOUT_ATR_MULT、GOLD_VOLATILITY_ATR_PERCENT_MIN 等。"""
    c = Config()

    def _ef(name: str, cur: float) -> float:
        raw = os.environ.get(name, "").strip()
        if not raw:
            return cur
        try:
            return float(raw)
        except ValueError:
            return cur

    def _ei(name: str, cur: int) -> int:
        raw = os.environ.get(name, "").strip()
        if not raw:
            return cur
        try:
            return int(raw)
        except ValueError:
            return cur

    c.MIN_BREAKOUT_ATR_MULT = _ef("GOLD_MIN_BREAKOUT_ATR_MULT", c.MIN_BREAKOUT_ATR_MULT)
    c.VOLATILITY_ATR_PERCENT_MIN = _ef("GOLD_VOLATILITY_ATR_PERCENT_MIN", c.VOLATILITY_ATR_PERCENT_MIN)
    c.DXY_MIN_REL_MOVE = _ef("GOLD_DXY_MIN_REL_MOVE", c.DXY_MIN_REL_MOVE)
    c.DXY_LOOKBACK = _ei("GOLD_DXY_LOOKBACK", c.DXY_LOOKBACK)
    c.RSI_LONG_MAX = _ef("GOLD_RSI_LONG_MAX", c.RSI_LONG_MAX)
    c.RSI_SHORT_MIN = _ef("GOLD_RSI_SHORT_MIN", c.RSI_SHORT_MIN)
    c.SESSION_START_UTC = _ei("GOLD_SESSION_START_UTC", c.SESSION_START_UTC)
    c.SESSION_END_UTC = _ei("GOLD_SESSION_END_UTC", c.SESSION_END_UTC)
    return c
