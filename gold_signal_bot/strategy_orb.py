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

# ── ORB 時段定義 ─────────────────────────────────────────────────────────────
# 各主力時段的開盤 UTC 小時與顯示標籤，可透過 config 覆寫具體時間
_ORB_SESSION_DEFAULTS: dict = {
    "asia":   {"start_utc": 1,  "label": "亞洲盤"},
    "london": {"start_utc": 7,  "label": "倫敦盤"},
    "ny":     {"start_utc": 13, "label": "紐約盤"},
}


def _resolve_orb_session(config) -> Tuple[int, str]:
    """
    依 config.ORB_SESSION 決定 ORB 區間建立的起始時間 (UTC hour) 與顯示標籤。
    config.LONDON_OPEN_UTC / NY_OPEN_UTC 可覆寫預設值（方便夏/冬令時調整）。
    回傳 (range_start_utc: int, session_label: str)
    """
    key = getattr(config, "ORB_SESSION", "london").lower().strip()
    if key == "london":
        start = int(getattr(config, "LONDON_OPEN_UTC", 7))
        return start, "倫敦盤"
    if key == "ny":
        start = int(getattr(config, "NY_OPEN_UTC", 13))
        return start, "紐約盤"
    # "asia" 或其他未知值 → 亞洲盤（維持原行為）
    return int(getattr(config, "SESSION_START_HOUR_UTC", 1)), "亞洲盤"


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
    range_lock_candles: int = 0,
) -> Tuple[int, Optional[float], Optional[float], RangeState]:
    """
    在當前交易日的 1h K 上跑 ORB，回傳 (signal, range_high, range_low, state)。
    signal: 11=多, 10=空, 0=無。邏輯：每根 K 收盤後用該 K 更新 range，再檢查該 K 是否產生訊號。

    range_lock_candles > 0：開盤後前 N 根 K 確立區間後凍結，後續不再擴張；
    解決「區間跟著日內最高/最低不斷擴張 → 收盤永遠在區間內 → 永無訊號」的問題。
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
    range_high, range_low = None, None

    for i in range(1, len(day_df)):
        prev_row = day_df.iloc[i - 1]
        prev = _get_candle_info(prev_row)
        atr_prev = prev_row.get("ATR", point * 500)
        if pd.isna(atr_prev) or atr_prev <= 0:
            atr_prev = point * 500

        # range_lock_candles > 0：超過鎖定根數後凍結區間，不再呼叫 _candle_update_snr
        range_is_locked = range_lock_candles > 0 and i > range_lock_candles

        if new_trading_day_flag:
            _first_candle_update_snr(state, _get_candle_info(day_df.iloc[0]), atr_prev, point)
            new_trading_day_flag = False
            _update_flags(state, trades_per_day)
        elif not range_is_locked:
            _candle_update_snr(state, prev, atr_prev, point, candle_composition)
            _update_flags(state, trades_per_day)
        else:
            # 區間已凍結：只推進計數器，讓 composition 條件可滿足，不更新高低點
            _update_flags(state, trades_per_day)

        # 仍須每根呼叫以更新 long/short 旗標（MAX_TRADES_PER_DAY）；訊號結論改由「最後一根完成 K」決定
        _ = _get_buy_sell_signal(state, prev, candle_composition, trades_per_day)

    # 一律以「最後一根完成 K 線」的突破結果為準，避免盤中早預跳出迴圈、晚盤已跌回箱體仍回報多單
    last_signal = SIGNAL_NONE
    if len(day_df) >= 2:
        last_row = day_df.iloc[-1]
        last_candle = _get_candle_info(last_row)
        atr_last = last_row.get("ATR", point * 500)
        if pd.isna(atr_last) or atr_last <= 0:
            atr_last = point * 500
        last_i = len(day_df) - 1
        if range_lock_candles == 0 or last_i <= range_lock_candles:
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

    if range_lock_candles > 0:
        logger.info(
            "[ORB] 區間凍結模式 lock=%d 根 | 凍結區間 body_high=%.2f body_low=%.2f",
            range_lock_candles,
            state.body_high,
            state.body_low,
        )

    return last_signal, range_high, range_low, state


def compute_sl_tp(
    direction: str,
    entry: float,
    atr_value: float,
    atr_sl_mult: float = 1.5,
    min_rr: float = 2.0,
) -> Tuple[float, float, float]:
    """
    依 344/338：1.5x ATR 止損、1:1 與 1:2 雙目標。
    回傳 (sl, tp1, tp2)：
      sl   = entry ± (atr_sl_mult × ATR)         止損
      tp1  = entry ± (atr_sl_mult × ATR)         目標一 R:R 1:1
      tp2  = entry ± (atr_sl_mult × min_rr × ATR) 目標二 R:R 1:2
    """
    sl_dist  = atr_value * atr_sl_mult          # 1.5 ATR
    tp1_dist = sl_dist                           # 1:1 → 同距離
    tp2_dist = sl_dist * min_rr                  # 1:2 → 原 tp
    if direction == "long":
        sl  = entry - sl_dist
        tp1 = entry + tp1_dist
        tp2 = entry + tp2_dist
    else:
        sl  = entry + sl_dist
        tp1 = entry - tp1_dist
        tp2 = entry - tp2_dist
    return round(sl, 2), round(tp1, 2), round(tp2, 2)


@dataclass
class SignalResult:
    direction: str   # "long" | "short"
    entry: float
    sl: float        # 止損：entry ± (1.5 × ATR)
    tp1: float       # 目標一：entry ± (1.5 × ATR)，R:R 1:1
    tp2: float       # 目標二：entry ± (3.0 × ATR)，R:R 1:2
    atr: float
    trend_strength: str
    rr_ratio: float
    source: str = "ORB+MA"
    raw_signal: int = 0

    @property
    def tp(self) -> float:
        """向下相容：tp 指向 tp2（主目標）。"""
        return self.tp2


def compute_signal(
    df_1h: pd.DataFrame,
    config,
) -> Optional[SignalResult]:
    """
    主入口：ORB + MA 濾網 + ATR 風控，回傳可發 Telegram 的訊號結構。

    ORB 時段由 config.ORB_SESSION 控制：
      "asia"   → 亞洲盤 SESSION_START_HOUR_UTC (01:00 UTC)
      "london" → 倫敦盤 LONDON_OPEN_UTC (07:00 UTC)  ← 預設
      "ny"     → 紐約盤 NY_OPEN_UTC (13:00 UTC)
    前 RANGE_LOCK_CANDLES 根 K 建立箱體，之後偵測收盤突破。
    """
    df_1h = add_indicators(
        df_1h,
        atr_period=config.ATR_PERIOD,
        ma_period=config.MA_TREND_PERIOD,
        sma_fast=config.SMA_FAST,
        sma_slow=config.SMA_SLOW,
    )

    # ── 1. 解析 ORB 時段 ────────────────────────────────────────────────────
    range_start_utc, session_label = _resolve_orb_session(config)
    range_lock = int(getattr(config, "RANGE_LOCK_CANDLES", 4))
    range_end_utc = range_start_utc + range_lock   # 箱體確立後的第一根偵測小時（參考用）

    # ── 2. 確認 ORB 時段今日是否已開盤 ──────────────────────────────────────
    # 以 df_1h 最後一根 K 的時間作為「現在」的代理
    try:
        last_ts = df_1h.index[-1]
        last_ts_utc = last_ts.tz_convert("UTC") if getattr(last_ts, "tzinfo", None) else last_ts
        today_orb_open = last_ts_utc.normalize().replace(
            hour=range_start_utc, minute=0, second=0, microsecond=0
        )
        if last_ts_utc < today_orb_open:
            logger.info(
                "[ORB] %s 時段今日尚未開盤（需 %02d:00 UTC，最新K=%s UTC），跳過",
                session_label, range_start_utc,
                last_ts_utc.strftime("%H:%M"),
            )
            return None
    except Exception as e:
        logger.warning("[ORB] 時段開盤檢查失敗（繼續執行）: %s", e)

    # ── 3. Debug：全日 K 線概況（以 SESSION_START_HOUR_UTC=1 為全日起點）──────
    try:
        day_df_dbg = get_trading_day_candles_1h(df_1h, config.SESSION_START_HOUR_UTC)
        orb_df_dbg  = get_trading_day_candles_1h(df_1h, range_start_utc)
        if day_df_dbg is None or day_df_dbg.empty:
            logger.info("[ORB-DEBUG] 當日K數=0")
        else:
            first_d = day_df_dbg.iloc[0]
            last_d  = day_df_dbg.iloc[-1]
            ma_dbg  = last_d.get("SMA_40") or last_d.get("MA") or last_d.get("SMA_100")
            atr_dbg = last_d.get("ATR")
            logger.info(
                "[ORB-DEBUG] 全日K數=%d 起點=%s 今日高=%.2f 低=%.2f 收盤=%.2f SMA40=%.2f ATR=%.2f",
                len(day_df_dbg),
                day_df_dbg.index[0].strftime("%H:%M UTC"),
                float(day_df_dbg["High"].max()),
                float(day_df_dbg["Low"].min()),
                float(last_d["Close"]),
                float(ma_dbg)  if ma_dbg  is not None and not pd.isna(ma_dbg)  else float("nan"),
                float(atr_dbg) if atr_dbg is not None and not pd.isna(atr_dbg) else float("nan"),
            )
        if orb_df_dbg is not None and not orb_df_dbg.empty:
            range_candles = orb_df_dbg.iloc[:range_lock]
            box_high = float(range_candles["High"].max()) if not range_candles.empty else float("nan")
            box_low  = float(range_candles["Low"].min())  if not range_candles.empty else float("nan")
            logger.info(
                "[ORB-DEBUG] %s 時段K數=%d 建立箱體根數=%d | 箱體 %.2f ~ %.2f | 偵測起點≈%02d:00 UTC",
                session_label, len(orb_df_dbg), min(range_lock, len(range_candles)),
                box_high, box_low, range_end_utc,
            )
    except Exception as e:
        logger.warning("[ORB-DEBUG] K 線紀錄失敗: %s", e)

    # ── 4. 執行 ORB 訊號計算（使用 ORB 時段起點） ────────────────────────────
    signal, range_high, range_low, orb_state = run_orb_signal(
        df_1h,
        session_start_hour_utc=range_start_utc,      # 倫敦/紐約/亞洲開盤時間
        candle_composition=config.CANDLE_COMPOSITION,
        trades_per_day=config.MAX_TRADES_PER_DAY,
        range_lock_candles=range_lock,
    )
    if signal == SIGNAL_NONE:
        logger.info(
            "[ORB] %s 無突破訊號 | 箱體上=%.2f 下=%.2f",
            session_label,
            range_high if range_high is not None else 0,
            range_low  if range_low  is not None else 0,
        )
        return None

    last  = df_1h.iloc[-1]
    close = float(last["Close"])

    # ── 5. 訊號有效性檢查：防止「歷史突破但當前已反轉」的假訊號 ──────────────
    body_high = orb_state.body_high
    body_low  = orb_state.body_low
    if signal == SIGNAL_LONG and body_low > 0 and close < body_low:
        logger.info(
            "[ORB] 多單訊號已失效（收盤 %.2f < 箱體下緣 %.2f，突破反轉），跳過",
            close, body_low,
        )
        return None
    if signal == SIGNAL_SHORT and body_high > 0 and close > body_high:
        logger.info(
            "[ORB] 空單訊號已失效（收盤 %.2f > 箱體上緣 %.2f，突破反轉），跳過",
            close, body_high,
        )
        return None

    # ── 5b. 突破強度：收盤需超過箱體實體邊至少 min_breakout × ATR（假突破過濾）
    atr_val = float(last["ATR"])
    if pd.isna(atr_val) or atr_val <= 0:
        logger.warning("[ORB] ATR 無效，跳過本輪")
        return None
    _brk_mult = float(getattr(config, "MIN_BREAKOUT_ATR_MULT", 0.12))
    _buf = max(atr_val * _brk_mult, 1e-9)
    if signal == SIGNAL_LONG and body_high > 0:
        if (close - body_high) < _buf:
            logger.info(
                "[ORB] 多單突破幅度不足（收盤-箱體上=%.3f < %.3f = %.2f×ATR），跳過",
                close - body_high,
                _buf,
                _brk_mult,
            )
            return None
    if signal == SIGNAL_SHORT and body_low > 0:
        if (body_low - close) < _buf:
            logger.info(
                "[ORB] 空單突破幅度不足（箱體下-收盤=%.3f < %.3f = %.2f×ATR），跳過",
                body_low - close,
                _buf,
                _brk_mult,
            )
            return None

    # ── 6. MA 趨勢濾網（SMA40）────────────────────────────────────────────
    ma      = last.get("SMA_40") or last.get("MA") or last.get("SMA_100")
    if signal == SIGNAL_LONG:
        if ma is not None and not pd.isna(ma) and close <= float(ma):
            logger.info(
                "[ORB] %s 多單突破，但 MA 濾網未過（收盤 %.2f <= SMA40 %.2f）",
                session_label, close, float(ma),
            )
            return None
        direction      = "long"
        trend_strength = f"{session_label} 突破 | 多頭 (收盤 > SMA40)"
    else:
        if ma is not None and not pd.isna(ma) and close >= float(ma):
            logger.info(
                "[ORB] %s 空單突破，但 MA 濾網未過（收盤 %.2f >= SMA40 %.2f）",
                session_label, close, float(ma),
            )
            return None
        direction      = "short"
        trend_strength = f"{session_label} 突破 | 空頭 (收盤 < SMA40)"

    # ── 6b. SMA40 / SMA100 排列：順勢突破（降低逆大週期均線硬追）
    # 允許 0.5% 誤差：黃金牛市初期 SMA40 剛剛上穿 SMA100，兩線非常接近
    # 若強制 SMA40 > SMA100 無誤差，會在最佳進場點附近誤殺大量訊號
    if getattr(config, "USE_MA_STACK_FILTER", True):
        s40 = last.get("SMA_40")
        s100 = last.get("SMA_100")
        if (
            s40 is not None
            and s100 is not None
            and not pd.isna(s40)
            and not pd.isna(s100)
        ):
            f40, f100 = float(s40), float(s100)
            _margin = f100 * 0.005   # 允許 0.5% 誤差，避免剛剛穿越時誤殺
            if direction == "long" and f40 < f100 - _margin:
                logger.info(
                    "[ORB] 多單未過均線排列濾網（SMA40 %.2f < SMA100 %.2f - 0.5%%margin）",
                    f40,
                    f100,
                )
                return None
            if direction == "short" and f40 > f100 + _margin:
                logger.info(
                    "[ORB] 空單未過均線排列濾網（SMA40 %.2f > SMA100 %.2f + 0.5%%margin）",
                    f40,
                    f100,
                )
                return None

    sl, tp1, tp2 = compute_sl_tp(
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
        tp1=tp1,
        tp2=tp2,
        atr=round(atr_val, 2),
        trend_strength=trend_strength,
        rr_ratio=config.MIN_RR_RATIO,
        source=f"{session_label} ORB+MA",
        raw_signal=signal,
    )
