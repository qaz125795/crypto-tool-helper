# -*- coding: utf-8 -*-
"""
勝率增強濾網：時段、波動率、DXY 負相關
"""
import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


def is_in_session(
    start_utc: int = 13,
    end_utc: int = 21,
    now: Optional[datetime] = None,
) -> bool:
    """是否在交易時段內（美盤/歐盤重疊）。"""
    now = now or datetime.now(timezone.utc)
    h = now.hour
    if start_utc <= end_utc:
        return start_utc <= h < end_utc
    return h >= start_utc or h < end_utc


def rsi_ok(
    direction: str,
    df: pd.DataFrame,
    long_max: float = 66.0,
    short_min: float = 34.0,
) -> bool:
    """避免 RSI 極端區追價：做多不過熱、做空不過冷。"""
    if df is None or df.empty or "RSI" not in df.columns:
        return True
    r = df.iloc[-1].get("RSI")
    if r is None or pd.isna(r):
        return True
    try:
        rv = float(r)
    except (TypeError, ValueError):
        return True
    if direction == "long":
        return rv <= long_max
    return rv >= short_min


def volatility_ok(
    df: pd.DataFrame,
    min_atr_pct: float = 0.001,
) -> bool:
    """波動率濾網：ATR/Close 不低於門檻，避免盤整假突破。"""
    if df is None or df.empty or "ATR" not in df.columns or "Close" not in df.columns:
        return True
    last = df.iloc[-1]
    atr = last.get("ATR")
    close = last.get("Close")
    if pd.isna(atr) or pd.isna(close) or close <= 0:
        return True
    return (atr / close) >= min_atr_pct


def dxy_aligned(
    direction: str,
    df_gold: pd.DataFrame,
    df_dxy: pd.DataFrame,
    lookback: int = 5,
) -> bool:
    """
    黃金與 DXY 負相關濾網：做多黃金時 DXY 弱勢(短期下跌)，做空時 DXY 強勢(短期上漲)。
    若 DXY 數據缺失則放行。
    """
    if df_dxy is None or df_dxy.empty or len(df_dxy) < lookback:
        return True
    if df_gold is None or df_gold.empty:
        return True
    try:
        dxy = df_dxy["Close"].tail(lookback)
        if dxy.isna().all():
            return True
        recent = dxy.dropna()
        if len(recent) < 2:
            return True
        # 短期趨勢：最近收盤 vs 前收
        trend = float(recent.iloc[-1]) - float(recent.iloc[0])
        if direction == "long":
            return trend <= 0  # DXY 下跌利於黃金多
        return trend >= 0  # DXY 上漲利於黃金空
    except Exception as e:
        logger.warning("dxy_aligned check failed: %s", e)
        return True


def apply_filters(
    direction: str,
    config,
    df_1h: pd.DataFrame,
    df_dxy: Optional[pd.DataFrame] = None,
    now: Optional[datetime] = None,
) -> Tuple[bool, str]:
    """
    套用所有啟用濾網。回傳 (通過與否, 原因說明)。
    """
    if config.USE_SESSION_FILTER and not is_in_session(
        config.SESSION_START_UTC, config.SESSION_END_UTC, now
    ):
        return False, "不在交易時段內"

    if config.USE_VOLATILITY_FILTER and not volatility_ok(df_1h, config.VOLATILITY_ATR_PERCENT_MIN):
        return False, "波動率過低(盤整)，跳過交易"

    if getattr(config, "USE_RSI_FILTER", False):
        if not rsi_ok(
            direction,
            df_1h,
            float(getattr(config, "RSI_LONG_MAX", 66.0)),
            float(getattr(config, "RSI_SHORT_MIN", 34.0)),
        ):
            return False, "RSI 處於極端區，跳過追高/追低"

    if config.USE_DXY_FILTER and df_dxy is not None and not df_dxy.empty:
        if not dxy_aligned(direction, df_1h, df_dxy, config.DXY_LOOKBACK):
            return False, "DXY 與黃金方向未負相關"

    return True, "通過"
