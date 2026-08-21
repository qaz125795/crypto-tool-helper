"""股票代幣場外賽 classify — SHADOW, stock universe only.

Never emit S3 keys. Never hook into live crypto classify() hits.
ORB needs 1m/5m bars; hourly-only → skip that key, do not fake a 15m range.
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.join(os.path.dirname(_HERE), "backtest", "engine")
for _p in (_HERE, _ENGINE):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

from stock_arena_report import (
    MAX_OPEN,
    ROSTER_META,
    SESSIONS,
    STOCK_KEYS,
    in_orb_trade,
    in_rth,
    in_universe,
    region_of,
    session_now,
)

try:
    from stock_arena_exits import EXIT_PROFILE  # type: ignore
except ImportError:
    EXIT_PROFILE = {}

from _altsignal_collector_once import (
    _bars_of,
    _f,
    _now_ts,
    _nr7_stretch,
    _triple_supertrend,
    _turtle_soup,
    _wavetrend_cross,
)

logger = logging.getLogger("stock_arena_classify")

GAP_TH = 0.006
ONDR_TH = 0.004
VWAP_TH = 0.012
NDRF_TH = 0.0003
PARKED_FR = 0.00012  # Gate stock perps often sit at 0.0001
CTRL_REMAP = {
    "wavetrend_cross": "eq_wavetrend",
    "turtle_soup": "eq_turtle_soup",
    "nr7_stretch": "eq_nr7",
    "triple_supertrend": "eq_triple_st",
}


def _hit(key, side, reason, **extra):
    rec = {
        "key": key,
        "strategy": key,
        "side": side.upper(),
        "reason": reason,
        "shadow": True,
        "push": False,
        "tg": False,
        "c34": False,
        "league": "stock_side",
        "max_open": MAX_OPEN.get(key, 5),
        "intake_ts": (ROSTER_META.get(key) or {}).get("intake_ts"),
    }
    rec.update(extra)
    return rec


def _intraday(row):
    return _bars_of(row, "bars_1m") or _bars_of(row, "bars_5m")


def _local(region, ts):
    return session_now(region, ts)


def _session_open_dt(region, ts):
    cfg = SESSIONS[region]
    dt = session_now(region, ts)
    h, m = cfg["open"]
    return dt.replace(hour=h, minute=m, second=0, microsecond=0)


def _or_bounds(region, bars, ts):
    """High/low of the opening-range window on the session date of ts."""
    cfg = SESSIONS[region]
    tz = ZoneInfo(cfg["tz"])
    open_dt = _session_open_dt(region, ts)
    end_dt = open_dt + timedelta(minutes=cfg["orb_min"])
    t0, t1 = int(open_dt.timestamp()), int(end_dt.timestamp())
    window = [b for b in bars if t0 <= int(b.get("ts") or 0) < t1]
    if len(window) < 3:
        return None
    return max(b["h"] for b in window), min(b["l"] for b in window), len(window)


def _last_pre_session_close(region, bars, ts):
    open_dt = _session_open_dt(region, ts)
    t0 = int(open_dt.timestamp())
    prev = [b for b in bars if int(b.get("ts") or 0) < t0]
    if not prev:
        return None
    return prev[-1]["c"]


def _last_rth_close(region, bars, ts):
    """Most recent cash close, not the last bar before today's open.

    After 16:00 ET, overnight drift must anchor to *today's* RTH last print.
    Using pre-open close would count the whole cash session as 'overnight'.
    """
    cfg = SESSIONS[region]
    dt = session_now(region, ts)
    now_m = dt.hour * 60 + dt.minute
    cls_h, cls_m = cfg["close"]
    cls = cls_h * 60 + cls_m
    d = dt
    if dt.weekday() >= 5:
        d = dt - timedelta(days=(dt.weekday() - 4))
    elif now_m < cls:
        d = dt - timedelta(days=1)
        while d.weekday() >= 5:
            d = d - timedelta(days=1)
    close_dt = d.replace(hour=cls_h, minute=cls_m, second=0, microsecond=0)
    t_close = int(close_dt.timestamp())
    prev = [b for b in bars if int(b.get("ts") or 0) <= t_close]
    if not prev:
        return None
    return prev[-1]["c"]


def _session_open_px(region, bars, ts):
    open_dt = _session_open_dt(region, ts)
    t0 = int(open_dt.timestamp())
    t1 = t0 + 5 * 60
    for b in bars:
        if t0 <= int(b.get("ts") or 0) < t1:
            return b["o"]
    return None


def _session_vwap(region, bars, ts):
    open_dt = _session_open_dt(region, ts)
    t0 = int(open_dt.timestamp())
    now = int(ts)
    num = den = 0.0
    for b in bars:
        bt = int(b.get("ts") or 0)
        if bt < t0 or bt > now:
            continue
        typical = (b["h"] + b["l"] + b["c"]) / 3.0
        num += typical * b["v"]
        den += b["v"]
    if den <= 0:
        return None
    return num / den


def _ny_orb(row, bars, region, ts, intra):
    if region != "ny" or not in_orb_trade("ny", ts):
        return None
    src = intra or bars
    bounds = _or_bounds("ny", src, ts)
    if not bounds:
        return None
    hi, lo, n = bounds
    px = src[-1]["c"]
    if px > hi:
        return _hit("ny_orb", "LONG", "NY 15m OR break up (n=%d, hi=%.4f)" % (n, hi),
                    or_high=hi, or_low=lo, session="ny")
    if px < lo:
        return _hit("ny_orb", "SHORT", "NY 15m OR break down (n=%d, lo=%.4f)" % (n, lo),
                    or_high=hi, or_low=lo, session="ny")
    return None


def _hk_orb(row, bars, region, ts, intra):
    if region != "hk" or not in_orb_trade("hk", ts):
        return None
    src = intra or bars
    bounds = _or_bounds("hk", src, ts)
    if not bounds:
        return None
    hi, lo, n = bounds
    px = src[-1]["c"]
    if px > hi:
        return _hit("hk_orb", "LONG", "HK 15m OR break up (n=%d)" % n, or_high=hi, or_low=lo, session="hk")
    if px < lo:
        return _hit("hk_orb", "SHORT", "HK 15m OR break down (n=%d)" % n, or_high=hi, or_low=lo, session="hk")
    return None


def _kr_orb(row, bars, region, ts, intra):
    if region != "kr" or not in_orb_trade("kr", ts):
        return None
    src = intra or bars
    bounds = _or_bounds("kr", src, ts)
    if not bounds:
        return None
    hi, lo, n = bounds
    px = src[-1]["c"]
    if px > hi:
        return _hit("kr_orb", "LONG", "KR 15m OR break up (n=%d)" % n, or_high=hi, or_low=lo, session="kr")
    if px < lo:
        return _hit("kr_orb", "SHORT", "KR 15m OR break down (n=%d)" % n, or_high=hi, or_low=lo, session="kr")
    return None


def _gap_fill(row, bars, region, ts, intra):
    if not in_rth(region, ts):
        return None
    # only the first 30 minutes after cash open
    cfg = SESSIONS[region]
    dt = session_now(region, ts)
    open_m = cfg["open"][0] * 60 + cfg["open"][1]
    now_m = dt.hour * 60 + dt.minute
    if now_m >= open_m + 30:
        return None
    src = intra or bars
    prev = _last_pre_session_close(region, src, ts)
    opn = _session_open_px(region, src, ts)
    if not prev or not opn:
        return None
    gap = opn / prev - 1.0
    if abs(gap) < GAP_TH:
        return None
    px = src[-1]["c"]
    # fade the gap: gap-up → short toward prev close
    if gap > 0 and px > prev:
        return _hit("gap_fill", "SHORT",
                    "gap-up %+0.2f%% fade toward prior close" % (gap * 100),
                    gap=gap, session=region)
    if gap < 0 and px < prev:
        return _hit("gap_fill", "LONG",
                    "gap-down %+0.2f%% fade toward prior close" % (gap * 100),
                    gap=gap, session=region)
    return None


def _overnight_drift(row, bars, region, ts, intra):
    if in_rth(region, ts):
        return None
    src = intra or bars
    prev = _last_rth_close(region, src, ts)
    if not prev or not src:
        return None
    px = src[-1]["c"]
    ret = px / prev - 1.0
    if abs(ret) < ONDR_TH:
        return None
    side = "LONG" if ret > 0 else "SHORT"
    return _hit("overnight_drift", side,
                "overnight %+0.2f%% same-side (cash closed)" % (ret * 100),
                overnight_ret=ret, session=region)


def _weekend_converge(row, bars, region, ts, intra):
    dt = session_now(region, ts)
    # Fri after cash close, Sat, Sun, Mon before cash open.
    cfg = SESSIONS[region]
    now_m = dt.hour * 60 + dt.minute
    cls = cfg["close"][0] * 60 + cfg["close"][1]
    opn = cfg["open"][0] * 60 + cfg["open"][1]
    ok = False
    if dt.weekday() == 4 and now_m >= cls:
        ok = True
    elif dt.weekday() in (5, 6):
        ok = True
    elif dt.weekday() == 0 and now_m < opn:
        ok = True
    if not ok:
        return None
    src = intra or bars
    # Friday RTH last close: last bar before Friday close on the most recent Friday
    tz = ZoneInfo(cfg["tz"])
    # walk back to last Friday close timestamp
    days_back = (dt.weekday() - 4) % 7
    fri = dt - timedelta(days=days_back)
    fri_close = fri.replace(hour=cfg["close"][0], minute=cfg["close"][1], second=0, microsecond=0)
    t_close = int(fri_close.timestamp())
    prev = [b for b in src if int(b.get("ts") or 0) <= t_close]
    if not prev:
        return None
    px0 = prev[-1]["c"]
    px = src[-1]["c"]
    move = px / px0 - 1.0
    if abs(move) < GAP_TH:
        return None
    # fade weekend drift (opposite of S3 WKND momentum)
    side = "SHORT" if move > 0 else "LONG"
    return _hit("weekend_converge", side,
                "fade weekend %+0.2f%% into cash open (not WKND hold)" % (move * 100),
                weekend_move=move, session=region)


def _session_vwap_fade(row, bars, region, ts, intra):
    if not in_rth(region, ts):
        return None
    src = intra or bars
    vwap = _session_vwap(region, src, ts)
    if not vwap:
        return None
    px = _f(row.get("px") or row.get("close")) or src[-1]["c"]
    dev = px / vwap - 1.0
    if abs(dev) < VWAP_TH:
        return None
    side = "SHORT" if dev > 0 else "LONG"
    return _hit("session_vwap", side,
                "RTH |px/sessVWAP-1|=%.2f%% fade" % (abs(dev) * 100),
                vwap=vwap, deviation=dev, session=region)


def _overnight_fund(row, bars, region, ts, intra):
    if in_rth(region, ts):
        return None
    fr = _f(row.get("funding") if row.get("funding") is not None else row.get("fr") or row.get("pred_funding"))
    if fr is None or abs(fr) < NDRF_TH:
        return None
    if abs(fr) <= PARKED_FR:
        return None
    side = "SHORT" if fr > 0 else "LONG"
    return _hit("overnight_fund_fade", side,
                "cash-closed fade funding %+0.3f%% (not parked 0.01%%)" % (fr * 100),
                funding=fr, session=region)


def _retag(hit, new_key):
    if not hit:
        return None
    out = dict(hit)
    out["key"] = new_key
    out["strategy"] = new_key
    out["league"] = "stock_side"
    out["shadow"] = True
    out["push"] = False
    out["tg"] = False
    out["c34"] = False
    out["control"] = True
    out["max_open"] = MAX_OPEN.get(new_key, 5)
    if "reason" in out and "[eq]" not in out["reason"]:
        out["reason"] = "[eq] " + out["reason"]
    return out


def _controls(row, bars):
    hits = []
    mapping = (
        (_wavetrend_cross, "eq_wavetrend"),
        (_turtle_soup, "eq_turtle_soup"),
        (_nr7_stretch, "eq_nr7"),
        (_triple_supertrend, "eq_triple_st"),
    )
    for fn, key in mapping:
        try:
            raw = fn(row, bars)
        except Exception as exc:
            logger.error("stock control %s: %s", key, exc)
            continue
        if not raw:
            continue
        items = raw if isinstance(raw, list) else [raw]
        for h in items:
            src_key = h.get("key")
            if src_key not in CTRL_REMAP:
                continue
            hits.append(_retag(h, CTRL_REMAP[src_key]))
    return hits


_MAIN = (
    _ny_orb, _hk_orb, _kr_orb, _gap_fill,
    _overnight_drift, _weekend_converge,
    _session_vwap_fade, _overnight_fund,
)


def classify_stock(row, ctx=None):
    """Stock-token hits only. Empty list for BTC/ETH/alts and unknown names."""
    if ctx:
        merged = dict(ctx)
        merged.update(row or {})
        row = merged
    row = row or {}
    sym = row.get("sym") or row.get("symbol") or ""
    if not in_universe(sym):
        return []
    region = region_of(sym)
    if not region:
        return []
    ts = _now_ts(row)
    if not ts:
        return []
    bars = _bars_of(row)
    intra = _intraday(row)
    hits = []
    for fn in _MAIN:
        try:
            h = fn(row, bars, region, ts, intra)
        except Exception as exc:
            logger.error("stock rule %s: %s", fn.__name__, exc)
            continue
        if h:
            h["sym"] = sym
            h["region"] = region
            hits.append(h)
    for h in _controls(row, bars or intra):
        if h:
            h["sym"] = sym
            h["region"] = region
            hits.append(h)
    # Drop any S3 key that leaked through a control retag miss.
    return [h for h in hits if h.get("key") in STOCK_KEYS]


def classify(row, ctx=None):
    """Alias. Do not use as S3 classify()."""
    return classify_stock(row, ctx)
