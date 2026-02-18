# -*- coding: utf-8 -*-
"""
數據層：yfinance 或 BingX API（公開行情，無需 API Key）
BingX: https://bingx-api.github.io/docs-v3/#/zh-tw/Swap/Market%20Data
"""
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple, TYPE_CHECKING

import pandas as pd
import yfinance as yf

if TYPE_CHECKING:
    from config import Config

logger = logging.getLogger(__name__)

BINGX_KLINE_URL = "https://open-api.bingx.com/openApi/swap/v3/quote/klines"
# BingX interval: 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d
BINGX_INTERVAL_MAP = {"1h": "1h", "30m": "30m", "15m": "15m", "1d": "1d", "5m": "5m"}


def fetch_ohlc_bingx(
    symbol: str,
    interval: str = "1h",
    limit: int = 150,
) -> pd.DataFrame:
    """
    從 BingX 取得合約 K 線（公開 API，不需 Key）。
    symbol: 如 XAU-USDT（黃金永續）
    """
    try:
        import requests
        kinterval = BINGX_INTERVAL_MAP.get(interval, "1h")
        r = requests.get(
            BINGX_KLINE_URL,
            params={"symbol": symbol, "interval": kinterval, "limit": limit},
            timeout=10,
        )
        if r.status_code != 200:
            logger.warning("BingX klines HTTP %s for %s", r.status_code, symbol)
            return pd.DataFrame()
        j = r.json()
        if j.get("code") != 0:
            logger.warning("BingX klines code %s %s", j.get("code"), j.get("msg"))
            return pd.DataFrame()
        data = j.get("data") or []
        if not isinstance(data, list) or len(data) < 2:
            return pd.DataFrame()
        rows = []
        for c in data:
            if isinstance(c, dict):
                o = float(c.get("open") or 0)
                h = float(c.get("high") or 0)
                l = float(c.get("low") or 0)
                cl = float(c.get("close") or 0)
                t = c.get("time") or c.get("openTime")
                rows.append({"Open": o, "High": h, "Low": l, "Close": cl, "_time": t})
            elif isinstance(c, (list, tuple)) and len(c) >= 5:
                # [openTime, open, high, low, close, ...]
                rows.append({
                    "Open": float(c[1]), "High": float(c[2]), "Low": float(c[3]), "Close": float(c[4]),
                    "_time": c[0] if len(c) > 0 else None,
                })
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        if "_time" in df.columns and df["_time"].notna().any():
            ts = df["_time"].astype(float).tolist()
            if ts and ts[0] > 1e12:
                ts = [t / 1000.0 for t in ts]
            df.index = pd.to_datetime(ts, unit="s", utc=True)
        df = df[["Open", "High", "Low", "Close"]].copy()
        df = df.sort_index()
        return df
    except Exception as e:
        logger.exception("fetch_ohlc_bingx failed %s: %s", symbol, e)
        return pd.DataFrame()


# BingX 黃金符號在公開 swap API 可能不存在，此時用 yfinance 代用
# 網頁圖表連結: https://bingx.com/zh-tc/perpetual/GOLD(XAU)-USDT
BINGX_GOLD_SYMBOLS = ("GOLD(XAU)-USDT", "GOLD-USDT", "XAU-USDT")
YFINANCE_GOLD_SYMBOL = "GC=F"


def fetch_ohlc(
    symbol: str,
    interval: str = "1h",
    period: str = "5d",
    config: Optional["Config"] = None,
) -> pd.DataFrame:
    """
    取得 OHLC。若 config.DATA_SOURCE=="bingx" 則走 BingX，否則 yfinance。
    BingX 若回傳空（例如黃金合約不在公開 swap 列表），會自動改用 yfinance GC=F。
    interval: 1d, 1h, 30m, 15m, 5m
    period: 1d, 5d, 1mo（僅 yfinance 用；bingx 用 limit）
    """
    if config is not None and getattr(config, "DATA_SOURCE", "").strip().lower() == "bingx":
        limit = 150 if interval == "1h" else 100
        if period == "5d" and interval == "1h":
            limit = 120
        df = fetch_ohlc_bingx(symbol, interval=interval, limit=limit)
        # BingX 黃金合約可能不在公開 API，空則改用 yfinance
        if df.empty and symbol in BINGX_GOLD_SYMBOLS:
            logger.warning(
                "[數據] BingX %s 無資料（可能未支援），改用 yfinance %s",
                symbol,
                YFINANCE_GOLD_SYMBOL,
            )
            return _fetch_ohlc_yfinance(YFINANCE_GOLD_SYMBOL, interval=interval, period=period)
        return df
    return _fetch_ohlc_yfinance(symbol, interval=interval, period=period)


def _fetch_ohlc_yfinance(symbol: str, interval: str = "1h", period: str = "5d") -> pd.DataFrame:
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval, auto_adjust=True)
        if df is None or df.empty:
            logger.warning("fetch_ohlc: empty data for %s %s %s", symbol, interval, period)
            return pd.DataFrame()
        df = df.rename(columns={"Open": "Open", "High": "High", "Low": "Low", "Close": "Close"})
        return df[["Open", "High", "Low", "Close"]].copy()
    except Exception as e:
        logger.exception("fetch_ohlc failed %s: %s", symbol, e)
        return pd.DataFrame()


def fetch_multi_timeframe(
    symbol: str,
    periods: Optional[dict] = None,
) -> dict:
    """
    取得多週期數據，供多週期共振使用。
    periods: {"1d": "3mo", "1h": "5d", "30m": "5d"}
    """
    if periods is None:
        periods = {"1d": "3mo", "1h": "5d", "30m": "5d"}
    out = {}
    for interval, period in periods.items():
        df = fetch_ohlc(symbol, interval=interval, period=period)
        if not df.empty:
            out[interval] = df
    return out


def get_latest_price(symbol: str) -> Optional[float]:
    """最新收盤價（用於進場價顯示）。"""
    df = fetch_ohlc(symbol, interval="1h", period="5d")
    if df is None or df.empty:
        return None
    return float(df["Close"].iloc[-1])
