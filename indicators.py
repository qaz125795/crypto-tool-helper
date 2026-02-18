# -*- coding: utf-8 -*-
"""
技術指標：ATR、SMA、EMA、RSI、BB Width
對應 344/338 PDF + GOLD_ORB + Gold-analysis
"""
import numpy as np
import pandas as pd


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range."""
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def sma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(period, min_periods=1).mean()


def ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50)


def bb_width(close: pd.Series, period: int = 20, num_std: float = 2.0) -> pd.Series:
    """Bollinger Band 寬度 (上-下)/中軌，用於波動率濾網。"""
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    width = (upper - lower) / mid.replace(0, np.nan)
    return width


def add_indicators(
    df: pd.DataFrame,
    atr_period: int = 14,
    ma_period: int = 100,
    sma_fast: int = 40,
    sma_slow: int = 100,
    rsi_period: int = 14,
) -> pd.DataFrame:
    """在 DataFrame 上新增常用指標欄位。"""
    out = df.copy()
    h, l, c = out["High"], out["Low"], out["Close"]
    out["ATR"] = atr(h, l, c, atr_period)
    out["MA"] = sma(c, ma_period)
    out["SMA_40"] = sma(c, sma_fast)
    out["SMA_100"] = sma(c, sma_slow)
    out["RSI"] = rsi(c, rsi_period)
    out["BB_Width"] = bb_width(c, 20, 2.0)
    return out
