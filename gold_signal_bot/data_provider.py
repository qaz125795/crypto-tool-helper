# -*- coding: utf-8 -*-
"""
數據層：黃金 K 線多交易所備援機制
優先順序：Gate.io → Bybit → BingX → yfinance (GC=F)
全部公開 API，無需 API Key。
"""
import logging
from typing import Optional, TYPE_CHECKING

import pandas as pd
import yfinance as yf

if TYPE_CHECKING:
    from config import Config

logger = logging.getLogger(__name__)

# ── 各交易所 API 端點 ──────────────────────────────────────────────────────────
GATEIO_KLINE_URL = "https://api.gateio.ws/api/v4/futures/usdt/candlesticks"
BYBIT_KLINE_URL  = "https://api.bybit.com/v5/market/kline"
BINGX_KLINE_URL  = "https://open-api.bingx.com/openApi/swap/v3/quote/klines"

# ── 黃金對應符號 ──────────────────────────────────────────────────────────────
GATEIO_GOLD_SYMBOL  = "XAU_USDT"
BYBIT_GOLD_SYMBOL   = "XAUUSDT"
BINGX_GOLD_SYMBOLS  = ("GOLD(XAU)-USDT", "GOLD-USDT", "XAU-USDT")
YFINANCE_GOLD_SYMBOL = "GC=F"

# ── interval 對照表 ──────────────────────────────────────────────────────────
_GATEIO_INTERVAL = {"1h": "1h", "30m": "30m", "15m": "15m", "5m": "5m", "4h": "4h", "1d": "1d"}
_BYBIT_INTERVAL  = {"1h": "60", "30m": "30", "15m": "15", "5m": "5", "4h": "240", "1d": "D"}
_BINGX_INTERVAL  = {"1h": "1h", "30m": "30m", "15m": "15m", "5m": "5m", "1d": "1d"}

_REQUIRED_COLS = ["Open", "High", "Low", "Close"]


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """確保 DataFrame 包含必要欄位且按時間排序。"""
    for col in _REQUIRED_COLS:
        if col not in df.columns:
            return pd.DataFrame()
    return df[_REQUIRED_COLS].sort_index()


# ── Gate.io ──────────────────────────────────────────────────────────────────

def fetch_ohlc_gateio(
    symbol: str = GATEIO_GOLD_SYMBOL,
    interval: str = "1h",
    limit: int = 150,
) -> pd.DataFrame:
    """Gate.io 永續合約 K 線（USDT 本位，公開 API）。"""
    try:
        import requests
        gi = _GATEIO_INTERVAL.get(interval, "1h")
        r = requests.get(
            GATEIO_KLINE_URL,
            params={"contract": symbol, "interval": gi, "limit": limit},
            timeout=10,
        )
        if r.status_code != 200:
            logger.warning("[Gate.io] HTTP %s for %s", r.status_code, symbol)
            return pd.DataFrame()
        data = r.json()
        if not isinstance(data, list) or len(data) < 2:
            logger.warning("[Gate.io] 回傳資料不足 for %s", symbol)
            return pd.DataFrame()
        rows = []
        for c in data:
            try:
                rows.append({
                    "Open":  float(c["o"]),
                    "High":  float(c["h"]),
                    "Low":   float(c["l"]),
                    "Close": float(c["c"]),
                    "_time": float(c["t"]),
                })
            except (KeyError, TypeError, ValueError):
                continue
        if len(rows) < 2:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df.index = pd.to_datetime(df["_time"], unit="s", utc=True)
        logger.info("[Gate.io] %s 取得 %d 根 K 線", symbol, len(df))
        return _normalize(df)
    except Exception as e:
        logger.warning("[Gate.io] fetch 失敗 %s: %s", symbol, e)
        return pd.DataFrame()


# ── Bybit ─────────────────────────────────────────────────────────────────────

def fetch_ohlc_bybit(
    symbol: str = BYBIT_GOLD_SYMBOL,
    interval: str = "1h",
    limit: int = 200,
) -> pd.DataFrame:
    """Bybit v5 線性永續合約 K 線（公開 API）。"""
    try:
        import requests
        bi = _BYBIT_INTERVAL.get(interval, "60")
        r = requests.get(
            BYBIT_KLINE_URL,
            params={"symbol": symbol, "interval": bi, "limit": limit, "category": "linear"},
            timeout=10,
        )
        if r.status_code != 200:
            logger.warning("[Bybit] HTTP %s for %s", r.status_code, symbol)
            return pd.DataFrame()
        j = r.json()
        if j.get("retCode") != 0:
            logger.warning("[Bybit] retCode %s %s", j.get("retCode"), j.get("retMsg"))
            return pd.DataFrame()
        # list 格式：[startTime(ms), open, high, low, close, volume, turnover]
        raw = j.get("result", {}).get("list", [])
        if not isinstance(raw, list) or len(raw) < 2:
            logger.warning("[Bybit] 回傳資料不足 for %s", symbol)
            return pd.DataFrame()
        rows = []
        for c in raw:
            try:
                rows.append({
                    "Open":  float(c[1]),
                    "High":  float(c[2]),
                    "Low":   float(c[3]),
                    "Close": float(c[4]),
                    "_time": float(c[0]) / 1000.0,  # ms → s
                })
            except (IndexError, TypeError, ValueError):
                continue
        if len(rows) < 2:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df.index = pd.to_datetime(df["_time"], unit="s", utc=True)
        logger.info("[Bybit] %s 取得 %d 根 K 線", symbol, len(df))
        return _normalize(df)
    except Exception as e:
        logger.warning("[Bybit] fetch 失敗 %s: %s", symbol, e)
        return pd.DataFrame()


# ── BingX ─────────────────────────────────────────────────────────────────────

def fetch_ohlc_bingx(
    symbol: str,
    interval: str = "1h",
    limit: int = 150,
) -> pd.DataFrame:
    """BingX 永續合約 K 線（公開 API）。"""
    try:
        import requests
        bi = _BINGX_INTERVAL.get(interval, "1h")
        r = requests.get(
            BINGX_KLINE_URL,
            params={"symbol": symbol, "interval": bi, "limit": limit},
            timeout=10,
        )
        if r.status_code != 200:
            logger.warning("[BingX] HTTP %s for %s", r.status_code, symbol)
            return pd.DataFrame()
        j = r.json()
        if j.get("code") != 0:
            logger.warning("[BingX] code %s %s", j.get("code"), j.get("msg"))
            return pd.DataFrame()
        data = j.get("data") or []
        if not isinstance(data, list) or len(data) < 2:
            return pd.DataFrame()
        rows = []
        for c in data:
            try:
                if isinstance(c, dict):
                    t = c.get("time") or c.get("openTime")
                    rows.append({
                        "Open":  float(c.get("open") or 0),
                        "High":  float(c.get("high") or 0),
                        "Low":   float(c.get("low") or 0),
                        "Close": float(c.get("close") or 0),
                        "_time": float(t),
                    })
                elif isinstance(c, (list, tuple)) and len(c) >= 5:
                    rows.append({
                        "Open": float(c[1]), "High": float(c[2]),
                        "Low":  float(c[3]), "Close": float(c[4]),
                        "_time": float(c[0]),
                    })
            except (KeyError, TypeError, ValueError):
                continue
        if len(rows) < 2:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        ts = df["_time"].tolist()
        if ts[0] > 1e12:
            df["_time"] = df["_time"] / 1000.0
        df.index = pd.to_datetime(df["_time"], unit="s", utc=True)
        logger.info("[BingX] %s 取得 %d 根 K 線", symbol, len(df))
        return _normalize(df)
    except Exception as e:
        logger.warning("[BingX] fetch 失敗 %s: %s", symbol, e)
        return pd.DataFrame()


# ── yfinance ──────────────────────────────────────────────────────────────────

def _fetch_ohlc_yfinance(symbol: str, interval: str = "1h", period: str = "5d") -> pd.DataFrame:
    """yfinance 備援（GC=F）。"""
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval, auto_adjust=True)
        if df is None or df.empty:
            logger.warning("[yfinance] 空資料 %s %s %s", symbol, interval, period)
            return pd.DataFrame()
        logger.info("[yfinance] %s 取得 %d 根 K 線", symbol, len(df))
        return _normalize(df)
    except Exception as e:
        logger.warning("[yfinance] fetch 失敗 %s: %s", symbol, e)
        return pd.DataFrame()


# ── 黃金專用備援鏈 ────────────────────────────────────────────────────────────

def fetch_gold_ohlc(interval: str = "1h", period: str = "5d") -> pd.DataFrame:
    """
    黃金 K 線多交易所備援鏈（無需任何 API Key）：
      1. Gate.io   XAU_USDT
      2. Bybit     XAUUSDT
      3. BingX     GOLD(XAU)-USDT
      4. yfinance  GC=F
    只要任一成功即回傳，並記錄使用的數據源。
    """
    limit = 150 if interval == "1h" else 100

    # 1. Gate.io
    df = fetch_ohlc_gateio(GATEIO_GOLD_SYMBOL, interval=interval, limit=limit)
    if not df.empty:
        logger.info("[數據] 黃金 K 線來源：Gate.io %s", GATEIO_GOLD_SYMBOL)
        return df

    logger.warning("[數據] Gate.io %s 無資料，改嘗試 Bybit", GATEIO_GOLD_SYMBOL)

    # 2. Bybit
    df = fetch_ohlc_bybit(BYBIT_GOLD_SYMBOL, interval=interval, limit=limit)
    if not df.empty:
        logger.info("[數據] 黃金 K 線來源：Bybit %s", BYBIT_GOLD_SYMBOL)
        return df

    logger.warning("[數據] Bybit %s 無資料，改嘗試 BingX", BYBIT_GOLD_SYMBOL)

    # 3. BingX（輪流嘗試多個黃金符號）
    for bx_sym in BINGX_GOLD_SYMBOLS:
        df = fetch_ohlc_bingx(bx_sym, interval=interval, limit=limit)
        if not df.empty:
            logger.info("[數據] 黃金 K 線來源：BingX %s", bx_sym)
            return df

    logger.warning("[數據] BingX 所有黃金符號均無資料，退回 yfinance %s", YFINANCE_GOLD_SYMBOL)

    # 4. yfinance GC=F
    df = _fetch_ohlc_yfinance(YFINANCE_GOLD_SYMBOL, interval=interval, period=period)
    if not df.empty:
        logger.info("[數據] 黃金 K 線來源：yfinance %s", YFINANCE_GOLD_SYMBOL)
        return df

    logger.error("[數據] 所有黃金數據源均失敗，無法取得 K 線")
    return pd.DataFrame()


# ── 通用入口（向下相容）──────────────────────────────────────────────────────

_ALL_GOLD_SYMBOLS = set(BINGX_GOLD_SYMBOLS) | {
    GATEIO_GOLD_SYMBOL, BYBIT_GOLD_SYMBOL, YFINANCE_GOLD_SYMBOL,
    "GC=F", "XAUUSD", "XAUUSDT",
}


def fetch_ohlc(
    symbol: str,
    interval: str = "1h",
    period: str = "5d",
    config: Optional["Config"] = None,
) -> pd.DataFrame:
    """
    通用 OHLC 入口。
    黃金相關符號自動走多交易所備援鏈；其餘符號依 config.DATA_SOURCE 決定。
    """
    if symbol in _ALL_GOLD_SYMBOLS or "XAU" in symbol.upper() or "GOLD" in symbol.upper():
        return fetch_gold_ohlc(interval=interval, period=period)

    # 非黃金符號：維持原有邏輯
    if config is not None and getattr(config, "DATA_SOURCE", "").strip().lower() == "bingx":
        limit = 120 if (period == "5d" and interval == "1h") else 100
        df = fetch_ohlc_bingx(symbol, interval=interval, limit=limit)
        if not df.empty:
            return df
    return _fetch_ohlc_yfinance(symbol, interval=interval, period=period)


def fetch_multi_timeframe(
    symbol: str,
    periods: Optional[dict] = None,
) -> dict:
    """多週期數據（供多週期共振使用）。"""
    if periods is None:
        periods = {"1d": "3mo", "1h": "5d", "30m": "5d"}
    out = {}
    for interval, period in periods.items():
        df = fetch_ohlc(symbol, interval=interval, period=period)
        if not df.empty:
            out[interval] = df
    return out


def get_latest_price(symbol: str) -> Optional[float]:
    """最新收盤價。"""
    df = fetch_ohlc(symbol, interval="1h", period="5d")
    if df is None or df.empty:
        return None
    return float(df["Close"].iloc[-1])
