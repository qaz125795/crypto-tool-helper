# -*- coding: utf-8 -*-
"""
Opening Range Breakout 策略 (GOLD_ORB) + MA 趨勢濾網 + ATR 止損/止盈
融合：price_action.mqh 邏輯、MA100 濾網、344/338 風控 (1.5x ATR, 1:2 RR)
"""
import logging
from dataclasses import dataclass
from typing import Optional, Tuple

import pandas as pd

from indicators import add_indicators

logger = logging.getLogger(__name__)

# 訊號常數 (與 MQL5 一致)
SIGNAL_LONG = 11
SIGNAL_SHORT = 10
SIGNAL_NONE = 0

# 長影線判定：body 與 wick 差距超過此倍數的 ATR 則用 body 當 S/R
LONG_WICK_ATR_MULTIPLIER = 0.5
# 顯著變動門檻
MIN_CHANGE = 0.1


@dataclass
class CandleInfo:
    body_high: float = 0.0
    body_low: float = 0.0
    wick_high: float = 0.0
    wick_low: float = 0.0
    direction: bool = True  # True=陽線


@dataclass
class RangeState:
    wick_high: float = 0.0
    wick_low: float = 0.0
    body_high: float = 0.0
    body_low: float = 0.0
    candle_counter_resistance: int = 0
    candle_counter_support: int = 0
    candle_reset_resistance: bool = False
    candle_reset_support: bool = False
    trade_today: bool = True
    long_position_flag: bool = False
    short_position_flag: bool = False


def _get_candle_info(row: pd.Series) -> CandleInfo:
    o, h, l, c = row["Open"], row["High"], row["Low"], row["Close"]
    info = CandleInfo()
    if c > o:
        info.body_high = c
        info.body_low = o
        info.direction = True
    else:
        info.body_high = o
        info.body_low = c
        info.direction = False
    info.wick_high = h
    info.wick_low = l
    return info


def _first_candle_update_snr(
    range_state: RangeState,
    prev: CandleInfo,
    atr: float,
    point: float,
) -> None:
    """第一根 K 確立當日 range（對應 FirstCandleUpdateSnR）。"""
    if atr <= 0:
        atr = point * 500
    long_wick_thresh = max(atr * LONG_WICK_ATR_MULTIPLIER, 500 * point)

    if abs(prev.body_high - prev.wick_high) >= long_wick_thresh:
        range_state.wick_high = prev.body_high
        range_state.body_high = prev.body_high
    else:
        range_state.wick_high = prev.wick_high
        range_state.body_high = prev.body_high

    if abs(prev.body_low - prev.wick_low) >= long_wick_thresh:
        range_state.wick_low = prev.body_low
        range_state.body_low = prev.body_low
    else:
        range_state.wick_low = prev.wick_low
        range_state.body_low = prev.body_low


def _candle_update_snr(
    range_state: RangeState,
    prev: CandleInfo,
    atr: float,
    point: float,
    composition: int,
) -> None:
    """後續 K 更新 range（對應 CandleUpdateSnR）。"""
    if atr <= 0:
        atr = point * 500
    long_wick_thresh = max(atr * LONG_WICK_ATR_MULTIPLIER, 500 * point)

    if (
        range_state.wick_high > 0
        and range_state.wick_low > 0
        and 0 < range_state.candle_counter_resistance <= composition
    ):
        if (
            prev.wick_high > range_state.wick_high
            and abs(prev.wick_high - range_state.wick_high) > MIN_CHANGE
            and prev.body_high > range_state.body_high
            and abs(prev.body_high - range_state.body_high) > MIN_CHANGE
        ):
            if abs(prev.body_high - prev.wick_high) >= long_wick_thresh:
                range_state.wick_high = prev.body_high
                range_state.body_high = prev.body_high
            else:
                range_state.wick_high = prev.wick_high
                range_state.body_high = prev.body_high
            range_state.candle_counter_resistance = 1
            range_state.candle_reset_resistance = True

    if (
        range_state.wick_high > 0
        and range_state.wick_low > 0
        and 0 < range_state.candle_counter_support <= composition
    ):
        if (
            prev.wick_low < range_state.wick_low
            and abs(prev.wick_low - range_state.wick_low) > MIN_CHANGE
            and prev.body_low < range_state.body_low
            and abs(prev.body_low - range_state.body_low) > MIN_CHANGE
        ):
            if abs(prev.body_low - prev.wick_low) >= long_wick_thresh:
                range_state.wick_low = prev.body_low
                range_state.body_low = prev.body_low
            else:
                range_state.wick_low = prev.wick_low
                range_state.body_low = prev.body_low
            range_state.candle_counter_support = 1
            range_state.candle_reset_support = True


def _update_flags(
    range_state: RangeState,
    trades_per_day: int,
) -> None:
    if range_state.long_position_flag and range_state.short_position_flag:
        range_state.trade_today = False
    if range_state.candle_reset_resistance:
        range_state.candle_counter_resistance = 1
        range_state.candle_reset_resistance = False
    else:
        range_state.candle_counter_resistance += 1
    if range_state.candle_reset_support:
        range_state.candle_counter_support = 1
        range_state.candle_reset_support = False
    else:
        range_state.candle_counter_support += 1


def _get_buy_sell_signal(
    range_state: RangeState,
    prev: CandleInfo,
    composition: int,
    trades_per_day: int,
) -> int:
    """多：收盤突破區間實體高點；空：收盤跌破區間實體低點（以 Close 為主）。"""
    # 以實體高/低(body_high/body_low) 為區間上下緣，並以當根 Close 判斷突破
    close_price = prev.body_high if prev.direction else prev.body_low
    # 改為 >= 讓「區間內 N 根」達標後下一根就能出訊號
    if (
        range_state.candle_counter_resistance >= composition
        and close_price > range_state.body_high
        and range_state.trade_today
        and not range_state.long_position_flag
    ):
        if trades_per_day == 2:
            range_state.long_position_flag = True
        if trades_per_day == 1:
            range_state.trade_today = False
        return SIGNAL_LONG

    if (
        range_state.candle_counter_support >= composition
        and close_price < range_state.body_low
        and range_state.trade_today
        and not range_state.short_position_flag
    ):
        if trades_per_day == 2:
            range_state.short_position_flag = True
        if trades_per_day == 1:
            range_state.trade_today = False
        return SIGNAL_SHORT

    return SIGNAL_NONE


def get_trading_day_candles_1h(
    df: pd.DataFrame,
    session_start_hour_utc: int,
) -> pd.DataFrame:
    """
    從 1h DataFrame 切出「當前交易日的 K 棒」。
    交易日：session_start_hour_utc (UTC) 為起點到下一日同時間。
    一律在 UTC 下計算，避免 yfinance 美東時區造成當日只篩到 1 根。
    """
    if df.empty or not isinstance(df.index, pd.DatetimeIndex):
        return pd.DataFrame()
    df = df.sort_index().copy()
    # 轉成 UTC 再切交易日，否則美東等時區會讓「當日」只有 1 根
    if df.index.tz is None:
        df.index = df.index.tz_localize("America/New_York", ambiguous="infer").tz_convert("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    now = df.index[-1]
    # 今日交易日起點 (UTC)
    day_start = now.normalize().replace(hour=session_start_hour_utc, minute=0, second=0, microsecond=0)
    if now.hour < session_start_hour_utc:
        day_start = day_start - pd.Timedelta(days=1)
    mask = (df.index >= day_start) & (df.index <= now)
    return df.loc[mask].copy()


def run_orb_signal(
    df_1h: pd.DataFrame,
    session_start_hour_utc: int = 1,
    candle_composition: int = 3,
    trades_per_day: int = 2,
    point: float = 0.01,
) -> Tuple[int, Optional[float], Optional[float], RangeState]:
    """
    在當前交易日的 1h K 上跑 ORB，回傳 (signal, range_high, range_low, state)。
    signal: 11=多, 10=空, 0=無。邏輯：每根 K 收盤後用該 K 更新 range，再檢查該 K 是否產生訊號。
    """
    day_df = get_trading_day_candles_1h(df_1h, session_start_hour_utc)
    if day_df.empty or len(day_df) < 2:
        logger.info("[ORB] 當日 K 棒不足（需至少 2 根，目前 %s 根）", len(day_df) if not day_df.empty else 0)
        return SIGNAL_NONE, None, None, RangeState()

    if "ATR" not in day_df.columns:
        day_df = add_indicators(day_df, atr_period=14, ma_period=100)

    state = RangeState()
    state.trade_today = True
    new_trading_day_flag = True
    last_signal = SIGNAL_NONE
    range_high, range_low = None, None

    for i in range(1, len(day_df)):
        prev_row = day_df.iloc[i - 1]
        prev = _get_candle_info(prev_row)
        atr_prev = prev_row.get("ATR", point * 500)
        if pd.isna(atr_prev) or atr_prev <= 0:
            atr_prev = point * 500

        if new_trading_day_flag:
            _first_candle_update_snr(state, _get_candle_info(day_df.iloc[0]), atr_prev, point)
            new_trading_day_flag = False
            _update_flags(state, trades_per_day)
        else:
            _candle_update_snr(state, prev, atr_prev, point, candle_composition)
            _update_flags(state, trades_per_day)

        last_signal = _get_buy_sell_signal(state, prev, candle_composition, trades_per_day)
        if last_signal != SIGNAL_NONE:
            range_high = state.wick_high
            range_low = state.wick_low
            # 一旦出現有效訊號，立即中斷，避免後續 K 線將 last_signal 洗回 NONE
            break

    # 若迴圈尚未產生訊號，最後一根 K 也要參與突破判斷
    if len(day_df) >= 2 and last_signal == SIGNAL_NONE:
        last_row = day_df.iloc[-1]
        last_candle = _get_candle_info(last_row)
        atr_last = last_row.get("ATR", point * 500)
        if pd.isna(atr_last) or atr_last <= 0:
            atr_last = point * 500
        _candle_update_snr(state, last_candle, atr_last, point, candle_composition)
        _update_flags(state, trades_per_day)
        last_signal = _get_buy_sell_signal(state, last_candle, candle_composition, trades_per_day)
        if last_signal != SIGNAL_NONE:
            range_high = state.wick_high
            range_low = state.wick_low

    # 無訊號時也帶回實際用於判斷的區間，方便 log 對照
    if range_high is None and state.wick_high > 0:
        range_high = state.wick_high
    if range_low is None and state.wick_low > 0:
        range_low = state.wick_low
    return last_signal, range_high, range_low, state


def compute_sl_tp(
    direction: str,
    entry: float,
    atr_value: float,
    atr_sl_mult: float = 1.5,
    min_rr: float = 2.0,
) -> Tuple[float, float]:
    """依 344/338：1.5x ATR 止損、最少 1:2 RR。"""
    sl_dist = atr_value * atr_sl_mult
    tp_dist = sl_dist * min_rr
    if direction == "long":
        sl = entry - sl_dist
        tp = entry + tp_dist
    else:
        sl = entry + sl_dist
        tp = entry - tp_dist
    return round(sl, 2), round(tp, 2)


@dataclass
class SignalResult:
    direction: str  # "long" | "short"
    entry: float
    sl: float
    tp: float
    atr: float
    trend_strength: str
    rr_ratio: float
    source: str = "ORB+MA"
    raw_signal: int = 0


def compute_signal(
    df_1h: pd.DataFrame,
    config,
) -> Optional[SignalResult]:
    """
    主入口：ORB + MA 濾網 + ATR 風控，回傳可發 Telegram 的訊號結構。
    """
    df_1h = add_indicators(
        df_1h,
        atr_period=config.ATR_PERIOD,
        ma_period=config.MA_TREND_PERIOD,
        sma_fast=config.SMA_FAST,
        sma_slow=config.SMA_SLOW,
    )
    # 調試用：記錄當日 K 線與關鍵價位，方便對照外部圖表
    try:
        day_df_dbg = get_trading_day_candles_1h(df_1h, config.SESSION_START_HOUR_UTC)
        if day_df_dbg is None or day_df_dbg.empty:
            logger.info("[ORB-DEBUG] 當日K數=0（get_trading_day_candles_1h 為空）")
        else:
            first = day_df_dbg.iloc[0]
            last = day_df_dbg.iloc[-1]
            hi_today = float(day_df_dbg["High"].max())
            lo_today = float(day_df_dbg["Low"].min())
            close_last = float(last["Close"])
            # 調試用 MA：與實際趨勢濾網一致，使用 SMA_40
            ma_dbg = last.get("SMA_40") or last.get("MA") or last.get("SMA_100")
            atr_dbg = last.get("ATR")
            ma_dbg_val = float(ma_dbg) if ma_dbg is not None and not pd.isna(ma_dbg) else float("nan")
            atr_dbg_val = float(atr_dbg) if atr_dbg is not None and not pd.isna(atr_dbg) else float("nan")
            logger.info(
                "[ORB-DEBUG] 當日K數=%s 起點=%s 首根OHL=%.2f/%.2f/%.2f/%.2f 今日高=%.2f 低=%.2f 最新收盤=%.2f MA100=%.2f ATR=%.2f",
                len(day_df_dbg),
                day_df_dbg.index[0],
                float(first["Open"]),
                float(first["High"]),
                float(first["Low"]),
                float(first["Close"]),
                hi_today,
                lo_today,
                close_last,
                ma_dbg_val,
                atr_dbg_val,
            )
    except Exception as e:
        logger.warning("[ORB-DEBUG] 當日 K 線紀錄失敗: %s", e)
    signal, range_high, range_low, _ = run_orb_signal(
        df_1h,
        session_start_hour_utc=config.SESSION_START_HOUR_UTC,
        candle_composition=config.CANDLE_COMPOSITION,
        trades_per_day=config.MAX_TRADES_PER_DAY,
    )
    if signal == SIGNAL_NONE:
        logger.info(
            "[ORB] 當日無突破訊號（區間內未達突破條件或已達每日次數上限）區間上=%.2f 區間下=%.2f",
            range_high if range_high is not None else 0,
            range_low if range_low is not None else 0,
        )
        return None

    last = df_1h.iloc[-1]
    close = float(last["Close"])
    # 趨勢濾網使用較快的 SMA_40，必要時回退到原本 MA/SMA_100
    ma = last.get("SMA_40") or last.get("MA") or last.get("SMA_100")
    atr_val = float(last["ATR"])
    if pd.isna(atr_val) or atr_val <= 0:
        logger.warning("[ORB] ATR 無效，跳過本輪")
        return None

    # MA 趨勢濾網：多單僅在 close > SMA40、空單僅在 close < SMA40
    if signal == SIGNAL_LONG:
        if ma is not None and not pd.isna(ma) and close <= float(ma):
            logger.info("[ORB] 有多單突破但 MA 濾網未過（收盤 %.2f <= SMA40 %.2f）", close, float(ma))
            return None
        direction = "long"
        trend_strength = "多頭 (收盤 > SMA40)"
    else:
        if ma is not None and not pd.isna(ma) and close >= float(ma):
            logger.info("[ORB] 有空單突破但 MA 濾網未過（收盤 %.2f >= SMA40 %.2f）", close, float(ma))
            return None
        direction = "short"
        trend_strength = "空頭 (收盤 < SMA40)"

    sl, tp = compute_sl_tp(
        direction,
        close,
        atr_val,
        config.ATR_SL_MULTIPLIER,
        config.MIN_RR_RATIO,
    )
    return SignalResult(
        direction=direction,
        entry=round(close, 2),
        sl=sl,
        tp=tp,
        atr=round(atr_val, 2),
        trend_strength=trend_strength,
        rr_ratio=config.MIN_RR_RATIO,
        source="ORB+MA",
        raw_signal=signal,
    )
