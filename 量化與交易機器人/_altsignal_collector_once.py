"""S3 補選 classify() — SHADOW paper only.

Live veteran classify() is not in this repo (it runs on Vultr).
This module emits only the 15 mid-S3 rookie keys. A merge hook extends
the live classify() without replacing it.

Feeds used (already on the collector row, or skip that key):
  OHLCV bars, optional 1m bars, funding / pred_funding,
  mark / index (and optional index history), optional BTC/ETH series,
  optional alt breadth / btc_d series.
No Deribit GEX, no footprint — OPXN / QHMR are not implemented.
"""
from __future__ import annotations

import logging
import math
import os
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_ENGINE = os.path.join(_ROOT, "backtest", "engine")
for _p in (_HERE, _ROOT, _ENGINE):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from s3_rookie_roster import (  # type: ignore
        MAX_OPEN,
        ROSTER,
        ROSTER_META,
        S3_ROOKIE_KEYS,
        max_open_for,
    )
except ImportError:
    from arena_report import (  # type: ignore
        MAX_OPEN,
        ROSTER,
        ROSTER_META,
        S3_ROOKIE_KEYS,
        max_open_for,
    )

try:
    from altsignal_replay_labeler import EXIT_PROFILE, exit_for  # type: ignore
except ImportError:
    try:
        from s3_rookie_exits import EXIT_PROFILE, exit_for  # type: ignore
    except ImportError:
        EXIT_PROFILE = {}

        def exit_for(key):
            return {"sl_atr": 1.5, "tp_r": 2.0, "horizon_h": 48}

try:
    from stock_arena_report import is_stock_token
except ImportError:
    def is_stock_token(_sym_or_row):
        return False

logger = logging.getLogger("s3_rookie_classify")

MAJORS = {"BTC", "ETH", "BTCUSDT", "ETHUSDT", "BTC_USDT", "ETH_USDT"}
ANN_PERIODS_PER_YEAR = 3 * 365  # 8h funding convention
CARY_ANN_TH = 0.005
BASIS_Z_BARS = 336
WT_N1, WT_N2, WT_SMOOTH = 10, 21, 4
WT_EXT = 53.0
ST_PARAMS = ((4.0, 8), (7.0, 9), (1.0, 8))  # (multiplier, period)
FVG_MIN_ATR = 0.3
FVG_AGE = (2, 100)
FUNDING_TH = 0.0005  # 0.05%
FUNDING_HOURS = (0, 8, 16)
POC_BIN_COUNT = 24

REGIME_WEIGHTS_FULL = {
    "btc_trend": 25.0,
    "alt_breadth": 20.0,
    "btc_d": 15.0,
    "funding_8h": 15.0,
    "dd_vol": 15.0,
    "momo_30d": 10.0,
}


def _f(x):
    try:
        if x is None or x == "":
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _base(sym: str) -> str:
    s = (sym or "").upper().replace("_", "")
    if s.endswith("USDT"):
        s = s[:-4]
    return s


def _bars_of(row, key="bars"):
    raw = row.get(key) or (row.get("ohlcv") if key == "bars" else None)
    if not raw:
        return []
    out = []
    for b in raw:
        if isinstance(b, dict):
            o = _f(b.get("o") if b.get("o") is not None else b.get("open"))
            h = _f(b.get("h") if b.get("h") is not None else b.get("high"))
            l = _f(b.get("l") if b.get("l") is not None else b.get("low"))
            c = _f(b.get("c") if b.get("c") is not None else b.get("close"))
            v = _f(b.get("v") if b.get("v") is not None else b.get("volume")) or 0.0
            idx = _f(b.get("index") if b.get("index") is not None else b.get("index_price"))
            mk = _f(b.get("mark") if b.get("mark") is not None else b.get("mark_price"))
            ts = b.get("ts") or b.get("time") or 0
            if None in (o, h, l, c):
                continue
            rec = {"o": o, "h": h, "l": l, "c": c, "v": v, "ts": int(ts) if ts else 0}
            if idx is not None:
                rec["index"] = idx
            if mk is not None:
                rec["mark"] = mk
            out.append(rec)
        elif isinstance(b, (list, tuple)) and len(b) >= 5:
            out.append({
                "ts": int(b[0]) if b[0] else 0,
                "o": float(b[1]), "h": float(b[2]), "l": float(b[3]),
                "c": float(b[4]),
                "v": float(b[5]) if len(b) > 5 else 0.0,
            })
    return out


def _closes(bars):
    return [b["c"] for b in bars]


def _sma(xs, n):
    if n <= 0 or len(xs) < n:
        return None
    return sum(xs[-n:]) / n


def _sma_series(xs, n):
    out = [None] * len(xs)
    if n <= 0:
        return out
    acc = 0.0
    for i, x in enumerate(xs):
        acc += x
        if i >= n:
            acc -= xs[i - n]
        if i >= n - 1:
            out[i] = acc / n
    return out


def _ema_series(xs, n):
    out = [None] * len(xs)
    if n <= 0 or not xs:
        return out
    k = 2.0 / (n + 1.0)
    seed = _sma(xs[:n], n) if len(xs) >= n else xs[0]
    if seed is None:
        return out
    start = n - 1 if len(xs) >= n else 0
    prev = seed
    out[start] = seed
    for i in range(start + 1, len(xs)):
        prev = xs[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def _std(xs):
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(var) if var > 0 else 0.0


def _true_ranges(bars):
    trs = []
    prev_c = None
    for b in bars:
        hl = b["h"] - b["l"]
        if prev_c is None:
            trs.append(hl)
        else:
            trs.append(max(hl, abs(b["h"] - prev_c), abs(b["l"] - prev_c)))
        prev_c = b["c"]
    return trs


def _atr(bars, n=14):
    if len(bars) < n + 1:
        return None
    trs = _true_ranges(bars)
    return _sma(trs, n)


def _returns(closes):
    out = []
    for i in range(1, len(closes)):
        if closes[i - 1]:
            out.append(closes[i] / closes[i - 1] - 1.0)
        else:
            out.append(0.0)
    return out


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
        "max_open": MAX_OPEN.get(key, 5),
        "intake_ts": (ROSTER_META.get(key) or {}).get("intake_ts"),
    }
    rec.update(extra)
    return rec


def _now_ts(row):
    ts = row.get("now_ts") or row.get("ts")
    if ts:
        return int(ts)
    bars = _bars_of(row)
    if bars and bars[-1].get("ts"):
        return int(bars[-1]["ts"])
    return 0


def _utc(ts):
    return datetime.fromtimestamp(int(ts), tz=timezone.utc)


# ── indicators ──────────────────────────────────────────────
def _wavetrend(bars, n1=WT_N1, n2=WT_N2, smooth=WT_SMOOTH):
    if len(bars) < n1 + n2 + smooth + 2:
        return None, None
    ap = [(b["h"] + b["l"] + b["c"]) / 3.0 for b in bars]
    esa = _ema_series(ap, n1)
    d_in = []
    for i, a in enumerate(ap):
        if esa[i] is None:
            d_in.append(0.0)
        else:
            d_in.append(abs(a - esa[i]))
    d = _ema_series(d_in, n1)
    ci = []
    for i, a in enumerate(ap):
        den = 0.015 * (d[i] or 0.0)
        if esa[i] is None or den == 0:
            ci.append(0.0)
        else:
            ci.append((a - esa[i]) / den)
    wt1 = _ema_series(ci, n2)
    wt1_f = [0.0 if x is None else x for x in wt1]
    wt2 = _sma_series(wt1_f, smooth)
    return wt1, wt2


def _mfi(bars, n=14):
    if len(bars) < n + 2:
        return None
    pos = 0.0
    neg = 0.0
    prev_tp = None
    window = bars[-(n + 1):]
    for b in window:
        tp = (b["h"] + b["l"] + b["c"]) / 3.0
        mf = tp * b["v"]
        if prev_tp is None:
            prev_tp = tp
            continue
        if tp > prev_tp:
            pos += mf
        elif tp < prev_tp:
            neg += mf
        prev_tp = tp
    if neg == 0 and pos == 0:
        return 50.0
    if neg == 0:
        return 100.0
    ratio = pos / neg
    return 100.0 - 100.0 / (1.0 + ratio)


def _ichimoku(bars, tenkan_n=20, kijun_n=60, senkou_b_n=120):
    if len(bars) < senkou_b_n:
        return None

    def mid(n, end):
        sl = bars[end - n + 1:end + 1]
        return (max(b["h"] for b in sl) + min(b["l"] for b in sl)) / 2.0

    i = len(bars) - 1
    tenkan = mid(tenkan_n, i)
    kijun = mid(kijun_n, i)
    tenkan_prev = mid(tenkan_n, i - 1)
    kijun_prev = mid(kijun_n, i - 1)
    senkou_a = (tenkan + kijun) / 2.0
    senkou_b = mid(senkou_b_n, i)
    # Displacement = kijun period when we have history; else unshifted (documented).
    shifted = False
    if len(bars) >= senkou_b_n + kijun_n + 1:
        j = i - kijun_n
        sa = (mid(tenkan_n, j) + mid(kijun_n, j)) / 2.0
        sb = mid(senkou_b_n, j)
        cloud_top = max(sa, sb)
        cloud_bot = min(sa, sb)
        shifted = True
    else:
        cloud_top = max(senkou_a, senkou_b)
        cloud_bot = min(senkou_a, senkou_b)
    return {
        "tenkan": tenkan, "kijun": kijun,
        "tenkan_prev": tenkan_prev, "kijun_prev": kijun_prev,
        "cloud_top": cloud_top, "cloud_bot": cloud_bot,
        "shifted": shifted,
    }


def _supertrend_dir(bars, period, multiplier):
    if len(bars) < period + 3:
        return None
    trs = _true_ranges(bars)
    atrs = [None] * len(bars)
    acc = 0.0
    for i, tr in enumerate(trs):
        acc += tr
        if i >= period:
            acc -= trs[i - period]
        if i >= period - 1:
            atrs[i] = acc / period
    fu = [None] * len(bars)
    fl = [None] * len(bars)
    trend = [0] * len(bars)
    for i, b in enumerate(bars):
        if atrs[i] is None:
            continue
        hl2 = (b["h"] + b["l"]) / 2.0
        basic_u = hl2 + multiplier * atrs[i]
        basic_l = hl2 - multiplier * atrs[i]
        if i == 0 or fu[i - 1] is None:
            fu[i] = basic_u
            fl[i] = basic_l
            trend[i] = 1 if b["c"] >= hl2 else -1
            continue
        fu[i] = basic_u if (basic_u < fu[i - 1] or bars[i - 1]["c"] > fu[i - 1]) else fu[i - 1]
        fl[i] = basic_l if (basic_l > fl[i - 1] or bars[i - 1]["c"] < fl[i - 1]) else fl[i - 1]
        if trend[i - 1] == 1:
            trend[i] = -1 if b["c"] < fl[i] else 1
        else:
            trend[i] = 1 if b["c"] > fu[i] else -1
    return trend[-1]


def _session_vwap(bars_1m, now_ts, lookback_s=3600):
    if not bars_1m:
        return None
    cutoff = now_ts - lookback_s if now_ts else 0
    num = 0.0
    den = 0.0
    for b in bars_1m:
        if cutoff and b.get("ts") and b["ts"] < cutoff:
            continue
        typical = (b["h"] + b["l"] + b["c"]) / 3.0
        num += typical * b["v"]
        den += b["v"]
    if den <= 0:
        return None
    return num / den


def _bar_vwap(bars, n=60):
    sl = bars[-n:] if len(bars) >= n else bars
    num = 0.0
    den = 0.0
    for b in sl:
        typical = (b["h"] + b["l"] + b["c"]) / 3.0
        num += typical * b["v"]
        den += b["v"]
    if den <= 0:
        return None
    return num / den


def _volume_poc(bars_1m):
    if not bars_1m:
        return None
    lows = [b["l"] for b in bars_1m]
    highs = [b["h"] for b in bars_1m]
    lo, hi = min(lows), max(highs)
    if hi <= lo:
        return bars_1m[-1]["c"]
    width = (hi - lo) / POC_BIN_COUNT
    bins = [0.0] * POC_BIN_COUNT
    for b in bars_1m:
        typical = (b["h"] + b["l"] + b["c"]) / 3.0
        idx = int((typical - lo) / width)
        if idx >= POC_BIN_COUNT:
            idx = POC_BIN_COUNT - 1
        if idx < 0:
            idx = 0
        bins[idx] += b["v"]
    best = max(range(POC_BIN_COUNT), key=lambda i: bins[i])
    return lo + (best + 0.5) * width


def _linreg_beta(a, b):
    n = min(len(a), len(b))
    if n < 10:
        return None
    a, b = a[-n:], b[-n:]
    mb = sum(b) / n
    ma = sum(a) / n
    var_b = sum((x - mb) ** 2 for x in b)
    if var_b <= 0:
        return None
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    return cov / var_b


def _annualized_basis(mark, index):
    if not mark or not index:
        return None
    return (mark / index - 1.0) * ANN_PERIODS_PER_YEAR


# ── per-key rules ───────────────────────────────────────────
def _cash_carry(row, bars):
    mark = _f(row.get("mark") or row.get("mark_price"))
    index = _f(row.get("index") or row.get("index_price"))
    if mark is None or index is None:
        if bars and "mark" in bars[-1] and "index" in bars[-1]:
            mark = bars[-1]["mark"]
            index = bars[-1]["index"]
    if mark is None or index is None:
        return None
    ann = _annualized_basis(mark, index)
    if ann is None:
        return None
    raw = mark / index - 1.0
    if ann > CARY_ANN_TH:
        return _hit("cash_carry", "SHORT",
                    "ann basis %+0.3f%% (raw %+0.4f%%) > +0.5%% → short perp" % (ann * 100, raw * 100),
                    ann_basis=ann, raw_basis=raw)
    if ann < -CARY_ANN_TH:
        return _hit("cash_carry", "LONG",
                    "ann basis %+0.3f%% (raw %+0.4f%%) < −0.5%% → long perp" % (ann * 100, raw * 100),
                    ann_basis=ann, raw_basis=raw)
    return None


def _regime_components(row, bars):
    """Return (components dict 0-1, missing list). Do not invent BTC.D."""
    comp = {}
    missing = []
    btc_bars = _bars_of(row, "btc_bars") or (_bars_of(row) if _base(row.get("sym") or row.get("symbol") or "") == "BTC" else [])
    if len(btc_bars) >= 50:
        c = btc_bars[-1]["c"]
        sma = _sma(_closes(btc_bars), 50)
        ema20 = _ema_series(_closes(btc_bars), 20)
        ema50 = _ema_series(_closes(btc_bars), 50)
        score = 0.0
        if sma and c > sma:
            score += 0.6
        elif sma and sma > 0:
            score += max(0.0, min(0.6, c / sma - 0.97) / 0.05 * 0.6)
        if ema20[-1] is not None and ema50[-1] is not None and ema20[-1] > ema50[-1]:
            score += 0.4
        comp["btc_trend"] = max(0.0, min(1.0, score))
    else:
        missing.append("btc_trend")

    alt_rets = row.get("alt_returns")
    if isinstance(alt_rets, (list, tuple)) and len(alt_rets) >= 5:
        up = sum(1 for r in alt_rets if _f(r) is not None and _f(r) > 0)
        comp["alt_breadth"] = up / len(alt_rets)
    else:
        missing.append("alt_breadth")

    btc_d = _f(row.get("btc_d"))
    btc_d_prev = _f(row.get("btc_d_prev"))
    if btc_d is not None and btc_d_prev is not None:
        # falling dominance = risk-on. Level alone is not used (would invent).
        delta = btc_d_prev - btc_d
        comp["btc_d"] = max(0.0, min(1.0, 0.5 + delta / 2.0))
    else:
        missing.append("btc_d")

    fr = _f(row.get("funding") if row.get("funding") is not None else row.get("fr") or row.get("funding_8h"))
    if fr is not None:
        # negative / flat funding → higher long-regime score
        comp["funding_8h"] = max(0.0, min(1.0, 0.5 - fr / 0.001))
    else:
        missing.append("funding_8h")

    src = btc_bars if len(btc_bars) >= 30 else bars
    if len(src) >= 30:
        cl = _closes(src)
        peak = cl[-30]
        dd = 0.0
        for x in cl[-30:]:
            peak = max(peak, x)
            if peak:
                dd = min(dd, x / peak - 1.0)
        vol = _std(_returns(cl[-31:])) or 0.0
        dd_s = 1.0 - min(1.0, abs(dd) / 0.25)
        vol_s = 1.0 - min(1.0, vol / 0.06)
        comp["dd_vol"] = 0.5 * dd_s + 0.5 * vol_s
        momo = cl[-1] / cl[-30] - 1.0 if cl[-30] else 0.0
        comp["momo_30d"] = max(0.0, min(1.0, (momo + 0.05) / 0.20))
    else:
        missing.append("dd_vol")
        missing.append("momo_30d")
    return comp, missing


def _regime_score(comp, missing):
    """0-100. Drop missing factors and renormalize. Need ≥3 factors."""
    weights = {k: w for k, w in REGIME_WEIGHTS_FULL.items() if k in comp}
    if len(weights) < 3:
        return None, "reduced: insufficient factors %s" % missing
    total_w = sum(weights.values())
    score = 100.0 * sum(comp[k] * (weights[k] / total_w) for k in weights)
    note = "full" if set(weights) == set(REGIME_WEIGHTS_FULL) else (
        "reduced (dropped %s; renormalized over %s)" % (
            ",".join(missing), ",".join(weights)))
    return score, note


def _crypto_regime(row, bars):
    cur, note = _regime_score(*_regime_components(row, bars))
    if cur is None:
        return None
    prev_row = dict(row)
    if bars:
        prev_row["bars"] = bars[:-1]
    if row.get("btc_bars"):
        prev_row["btc_bars"] = row["btc_bars"][:-1]
    prev, _ = _regime_score(*_regime_components(prev_row, bars[:-1] if bars else bars))
    if prev is None:
        return None
    if prev < 80.0 <= cur:
        return _hit("crypto_regime_score", "LONG",
                    "regime first cross ≥80 (%.1f→%.1f, %s)" % (prev, cur, note),
                    score=cur, prev_score=prev, regime_note=note)
    if prev > 39.0 >= cur:
        return _hit("crypto_regime_score", "SHORT",
                    "regime first break ≤39 (%.1f→%.1f, %s)" % (prev, cur, note),
                    score=cur, prev_score=prev, regime_note=note)
    return None


def _pairs_residual(row, bars):
    a_bars = bars
    b_bars = _bars_of(row, "btc_bars")
    eth_bars = _bars_of(row, "eth_bars")
    sym = _base(row.get("sym") or row.get("symbol") or "")
    two_leg = False
    if eth_bars and b_bars and (sym in ("ETH", "") or row.get("pair_owner")):
        a_bars = eth_bars
        two_leg = True
        a_name, b_name = "ETH", "BTC"
    elif b_bars and sym and sym != "BTC":
        a_name, b_name = sym, "BTC"
    else:
        return None
    n = min(len(a_bars), len(b_bars))
    if n < 40:
        return None
    a_c = _closes(a_bars)[-n:]
    b_c = _closes(b_bars)[-n:]
    a_r = _returns(a_c)
    b_r = _returns(b_c)
    beta = _linreg_beta(a_r, b_r)
    if beta is None:
        return None
    resid = []
    acc = 0.0
    for i in range(len(a_r)):
        acc += a_r[i] - beta * b_r[i]
        resid.append(acc)
    mu = sum(resid) / len(resid)
    sd = _std(resid)
    if not sd:
        return None
    z = (resid[-1] - mu) / sd
    richer = a_name
    if two_leg:
        av = sum(b["v"] for b in a_bars[-20:])
        bv = sum(b["v"] for b in b_bars[-20:])
        richer = a_name if av >= bv else b_name
    if z < -2.0:
        hits = [_hit("pairs_residual_z", "LONG",
                     "residual z=%.2f β=%.2f long %s / short %s" % (z, beta, a_name, b_name),
                     z=z, beta=beta, pair="%s-%s" % (a_name, b_name))]
        if two_leg:
            hits.append(_hit("pairs_residual_z", "SHORT",
                             "pair leg short %s (z=%.2f)" % (b_name, z),
                             z=z, beta=beta, pair_leg=b_name, sym="BTCUSDT"))
        elif richer != a_name:
            return None
        return hits
    if z > 2.0:
        hits = [_hit("pairs_residual_z", "SHORT",
                     "residual z=%.2f β=%.2f short %s / long %s" % (z, beta, a_name, b_name),
                     z=z, beta=beta, pair="%s-%s" % (a_name, b_name))]
        if two_leg:
            hits.append(_hit("pairs_residual_z", "LONG",
                             "pair leg long %s (z=%.2f)" % (b_name, z),
                             z=z, beta=beta, pair_leg=b_name, sym="BTCUSDT"))
        return hits
    return None


def _wavetrend_cross(row, bars):
    wt1, wt2 = _wavetrend(bars)
    if not wt1 or not wt2 or wt1[-1] is None or wt2[-1] is None:
        return None
    if wt1[-2] is None or wt2[-2] is None:
        return None
    cross_up = wt1[-2] <= wt2[-2] and wt1[-1] > wt2[-1]
    cross_dn = wt1[-2] >= wt2[-2] and wt1[-1] < wt2[-1]
    if cross_up and wt2[-1] < -WT_EXT:
        return _hit("wavetrend_cross", "LONG",
                    "WT1 cross up WT2=%.1f < -53 (hlc3 n1=10 n2=21)" % wt2[-1],
                    wt1=wt1[-1], wt2=wt2[-1])
    if cross_dn and wt2[-1] > WT_EXT:
        return _hit("wavetrend_cross", "SHORT",
                    "WT1 cross down WT2=%.1f > +53 (hlc3 n1=10 n2=21)" % wt2[-1],
                    wt1=wt1[-1], wt2=wt2[-1])
    return None


def _turtle_soup(row, bars):
    # Event = bars[-2], confirm = bars[-1]. Cancel if reclaim is later than N+1.
    if len(bars) < 22:
        return None
    event = bars[-2]
    confirm = bars[-1]
    window = bars[-22:-2]  # 20 bars before event
    lo20 = min(b["l"] for b in window)
    hi20 = max(b["h"] for b in window)
    if event["l"] < lo20 and confirm["c"] > lo20:
        return _hit("turtle_soup", "LONG",
                    "wick through 20-low %.4f, next close back above" % lo20,
                    soup_level=lo20, sl=event["l"])
    if event["h"] > hi20 and confirm["c"] < hi20:
        return _hit("turtle_soup", "SHORT",
                    "wick through 20-high %.4f, next close back below" % hi20,
                    soup_level=hi20, sl=event["h"])
    return None


def _ichimoku_tk(row, bars):
    ich = _ichimoku(bars)
    if not ich:
        return None
    mfi = _mfi(bars)
    if mfi is None:
        return None
    close = bars[-1]["c"]
    cross_up = ich["tenkan_prev"] <= ich["kijun_prev"] and ich["tenkan"] > ich["kijun"]
    cross_dn = ich["tenkan_prev"] >= ich["kijun_prev"] and ich["tenkan"] < ich["kijun"]
    if cross_up and close > ich["cloud_top"] and mfi > 50:
        return _hit("ichimoku_tk_cross", "LONG",
                    "TK cross up + close>cloud + MFI %.1f (20/60/120%s)" % (
                        mfi, " shifted" if ich["shifted"] else " unshifted-cloud"),
                    mfi=mfi, cloud_top=ich["cloud_top"])
    if cross_dn and close < ich["cloud_bot"] and mfi < 50:
        return _hit("ichimoku_tk_cross", "SHORT",
                    "TK cross down + close<cloud + MFI %.1f (20/60/120%s)" % (
                        mfi, " shifted" if ich["shifted"] else " unshifted-cloud"),
                    mfi=mfi, cloud_bot=ich["cloud_bot"])
    return None


def _vwap_revert(row, bars):
    now = _now_ts(row)
    bars_1m = _bars_of(row, "bars_1m")
    vwap = _session_vwap(bars_1m, now, 3600) if bars_1m else None
    src = "1m typical*vol 60m"
    if vwap is None:
        vwap = _bar_vwap(bars, 60)
        src = "bar VWAP (no 1m)"
    if not vwap:
        return None
    px = _f(row.get("px") or row.get("close")) or bars[-1]["c"]
    dev = px / vwap - 1.0
    if abs(dev) < 0.02:
        return None
    side = "SHORT" if dev > 0 else "LONG"
    return _hit("vwap_revert", side,
                "|px/VWAP-1|=%.2f%% fade (%s)" % (abs(dev) * 100, src),
                vwap=vwap, deviation=dev,
                tp_frac=0.5, sl_ext=0.8)


def _basis_z_fade(row, bars):
    series = []
    hist = row.get("basis_hist") or row.get("index_hist")
    if isinstance(hist, (list, tuple)) and hist and not isinstance(hist[0], dict):
        # parallel to bars: index prices
        n = min(len(bars), len(hist))
        for i in range(n):
            idx = _f(hist[-n + i])
            px = bars[-n + i].get("mark") or bars[-n + i]["c"]
            if idx:
                series.append(px - idx)
    else:
        for b in bars:
            idx = b.get("index")
            mk = b.get("mark") or b["c"]
            if idx:
                series.append(mk - idx)
    if len(series) < BASIS_Z_BARS:
        return None
    sl = series[-BASIS_Z_BARS:]
    mu = sum(sl) / len(sl)
    sd = _std(sl)
    if not sd:
        return None
    z = (sl[-1] - mu) / sd
    if z > 2.0:
        return _hit("basis_z_fade", "SHORT",
                    "basis z=%.2f > +2 (336-bar μ/σ, not annualized)" % z, z=z)
    if z < -2.0:
        return _hit("basis_z_fade", "LONG",
                    "basis z=%.2f < −2 (336-bar μ/σ, not annualized)" % z, z=z)
    return None


def _triple_supertrend(row, bars):
    dirs = []
    for m, p in ST_PARAMS:
        d = _supertrend_dir(bars, p, m)
        if d is None:
            return None
        dirs.append(d)
    if all(d > 0 for d in dirs):
        return _hit("triple_supertrend", "LONG",
                    "ST all up (m,p)=(4,8)(7,9)(1,8)", st=dirs)
    if all(d < 0 for d in dirs):
        return _hit("triple_supertrend", "SHORT",
                    "ST all down (m,p)=(4,8)(7,9)(1,8) → short/flat", st=dirs)
    return None


def _fvg_retest(row, bars):
    atr = _atr(bars)
    if atr is None or atr <= 0 or len(bars) < 8:
        return None
    # scan oldest-unfilled first in age window; take first return
    last = bars[-1]
    lo_age, hi_age = FVG_AGE
    for age in range(lo_age, min(hi_age, len(bars) - 2) + 1):
        k = len(bars) - 1 - age  # middle of 3-candle pattern ends at k+1? 
        # 3-candle FVG at bars[j-2], bars[j-1], bars[j] where j = len-1-age+2? 
        j = len(bars) - 1 - age
        if j < 2:
            continue
        a, c = bars[j - 2], bars[j]
        bull_gap = c["l"] - a["h"]
        bear_gap = a["l"] - c["h"]
        filled = False
        if bull_gap >= FVG_MIN_ATR * atr:
            gap_lo, gap_hi = a["h"], c["l"]
            for b in bars[j + 1:-1]:
                if b["l"] <= gap_lo:
                    filled = True
                    break
            if filled:
                continue
            # first return into gap
            if last["l"] <= gap_hi and last["h"] >= gap_lo:
                return _hit("fvg_retest", "LONG",
                            "bull FVG %.3f ATR age=%d first retest" % (bull_gap / atr, age),
                            sl=gap_lo - 1.5 * atr, gap_lo=gap_lo, gap_hi=gap_hi, age=age)
        filled = False
        if bear_gap >= FVG_MIN_ATR * atr:
            gap_lo, gap_hi = c["h"], a["l"]
            for b in bars[j + 1:-1]:
                if b["h"] >= gap_hi:
                    filled = True
                    break
            if filled:
                continue
            if last["h"] >= gap_lo and last["l"] <= gap_hi:
                return _hit("fvg_retest", "SHORT",
                            "bear FVG %.3f ATR age=%d first retest" % (bear_gap / atr, age),
                            sl=gap_hi + 1.5 * atr, gap_lo=gap_lo, gap_hi=gap_hi, age=age)
    return None


def _funding_settle_fade(row, bars):
    pred = _f(row.get("pred_funding") if row.get("pred_funding") is not None else row.get("funding") or row.get("fr"))
    if pred is None or abs(pred) < FUNDING_TH:
        return None
    now = _now_ts(row)
    if not now:
        return None
    dt = _utc(now)
    minute = dt.minute + dt.second / 60.0
    # T is 00/08/16 UTC. Window T-15m..T-3m lives in the previous hour
    # (23:45-23:57, 07:45-07:57, 15:45-15:57). The funding hour itself is too late.
    next_funding_hour = (dt.hour + 1) % 24
    in_window = next_funding_hour in FUNDING_HOURS and 45 <= minute <= 57
    if row.get("in_funding_window"):
        in_window = True
    if not in_window:
        return None
    ret15 = _f(row.get("ret_15m"))
    if ret15 is None and len(bars) >= 2:
        # last 15m: if bars are 15m use last return; if 1m use last 15; if 1h skip unless provided
        tf = (row.get("tf") or row.get("timeframe") or "").lower()
        if tf in ("15m", "15min", "15"):
            ret15 = bars[-1]["c"] / bars[-2]["c"] - 1.0 if bars[-2]["c"] else 0.0
        elif tf in ("1m", "1min", "1") or _bars_of(row, "bars_1m"):
            m1 = _bars_of(row, "bars_1m") or bars
            if len(m1) >= 16:
                ret15 = m1[-1]["c"] / m1[-16]["c"] - 1.0 if m1[-16]["c"] else 0.0
    if ret15 is None:
        return None
    if pred == 0 or ret15 == 0:
        return None
    if (pred > 0 and ret15 > 0):
        return _hit("funding_settle_fade", "SHORT",
                    "fade +funding %.3f%% with +15m ret (T-15..T-3)" % (pred * 100),
                    pred_funding=pred, ret_15m=ret15)
    if (pred < 0 and ret15 < 0):
        return _hit("funding_settle_fade", "LONG",
                    "fade −funding %.3f%% with −15m ret (T-15..T-3)" % (pred * 100),
                    pred_funding=pred, ret_15m=ret15)
    return None


def _weekend_momentum(row, bars):
    now = _now_ts(row)
    if not now:
        return None
    dt = _utc(now)
    if dt.weekday() not in (5, 6):  # Sat/Sun
        return None
    if dt.weekday() != 5:
        # Sunday: no new entry (hold existing). Skip.
        return None
    sym = _base(row.get("sym") or row.get("symbol") or "")
    if sym in ("BTC", "ETH"):
        return None  # prefer alts
    if len(bars) < 8:
        return None
    # prior 7d return: 7 daily or 7*24 hourly
    cl = _closes(bars)
    if len(cl) >= 7 * 24:
        ret = cl[-1] / cl[-7 * 24] - 1.0
    elif len(cl) >= 8:
        ret = cl[-1] / cl[-8] - 1.0
    else:
        return None
    side = "LONG" if ret > 0 else "SHORT"
    return _hit("weekend_momentum", side,
                "Sat UTC 7d ret %+0.2f%% → %s until Sun 23:59" % (ret * 100, side),
                ret_7d=ret)


def _btc_lead_lag(row, bars):
    sym = _base(row.get("sym") or row.get("symbol") or "")
    if sym in ("BTC", "ETH") or not sym:
        return None
    btc_5m = _f(row.get("btc_5m_ret"))
    btc_7d = _f(row.get("btc_7d_ret"))
    btc_5m_bars = _bars_of(row, "btc_5m_bars")
    if btc_5m is None and len(btc_5m_bars) >= 2:
        btc_5m = btc_5m_bars[-1]["c"] / btc_5m_bars[-2]["c"] - 1.0 if btc_5m_bars[-2]["c"] else 0.0
    if btc_7d is None:
        btc_d = _bars_of(row, "btc_bars")
        if len(btc_d) >= 8:
            btc_7d = btc_d[-1]["c"] / btc_d[-8]["c"] - 1.0 if btc_d[-8]["c"] else 0.0
        elif len(btc_d) >= 7 * 24:
            btc_7d = btc_d[-1]["c"] / btc_d[-7 * 24] - 1.0
    if btc_5m is None or btc_7d is None:
        return None
    th = 0.01 if btc_7d < 0 else 0.005
    if abs(btc_5m) < th:
        return None
    # after 1–3 bars of 15m
    lag = row.get("lag_15m_bars")
    if lag is None:
        tf = (row.get("tf") or "").lower()
        if tf in ("15m", "15min", "15") and len(bars) >= 4:
            lag = 2
        else:
            lag = _f(row.get("bars_since_btc_impulse"))
    if lag is None:
        return None
    if not (1 <= float(lag) <= 3):
        return None
    side = "LONG" if btc_5m > 0 else "SHORT"
    return _hit("btc_lead_lag", side,
                "BTC 5m %+0.2f%% (th %.1f%%, 7d %s) lag %s×15m same-side alt" % (
                    btc_5m * 100, th * 100, "bear" if btc_7d < 0 else "bull", int(float(lag))),
                btc_5m_ret=btc_5m, btc_7d_ret=btc_7d)


def _poc_sweep_reclaim(row, bars):
    if len(bars) < 3:
        return None
    bars_1m = _bars_of(row, "bars_1m")
    prior_1m = _bars_of(row, "prior_session_1m") or bars_1m
    poc = _volume_poc(prior_1m) if prior_1m else None
    src = "1m volume POC"
    if poc is None:
        # documented fallback: prior bar typical, not a fake footprint
        prev = bars[-2]
        poc = (prev["h"] + prev["l"] + prev["c"]) / 3.0
        src = "prior-bar typical (no 1m)"
    sweep = bars[-2]
    reclaim = bars[-1]
    # wick through POC + close back, next close reclaim
    swept_low = sweep["l"] < poc <= min(sweep["o"], sweep["c"])
    swept_high = sweep["h"] > poc >= max(sweep["o"], sweep["c"])
    if swept_low and reclaim["c"] > poc:
        return _hit("poc_sweep_reclaim", "LONG",
                    "sweep POC %.4f + reclaim close (%s)" % (poc, src),
                    poc=poc, sl=sweep["l"])
    if swept_high and reclaim["c"] < poc:
        return _hit("poc_sweep_reclaim", "SHORT",
                    "sweep POC %.4f + reclaim close (%s)" % (poc, src),
                    poc=poc, sl=sweep["h"])
    return None


def _nr7_stretch(row, bars):
    if len(bars) < 12:
        return None
    # NR7 bar is bars[-2]; trigger bar is bars[-1]. No overlapping last 3.
    nr = bars[-2]
    trig = bars[-1]
    ranges = [b["h"] - b["l"] for b in bars]
    win7 = ranges[-8:-1]  # 7 bars ending at nr
    if len(win7) < 7:
        return None
    is_nr7 = ranges[-2] <= min(win7) + 1e-12
    prev = bars[-3]
    inside = nr["h"] < prev["h"] and nr["l"] > prev["l"]
    win4 = ranges[-6:-1]
    is_inside_nr4 = inside and len(win4) >= 4 and ranges[-2] <= min(win4) + 1e-12
    if not (is_nr7 or is_inside_nr4):
        return None
    # Stretch = 10-bar avg min(|O-H|,|O-L|)
    stretch_src = bars[-12:-2]
    stretch = sum(min(abs(b["o"] - b["h"]), abs(b["o"] - b["l"])) for b in stretch_src) / len(stretch_src)
    buy_stop = nr["c"] + stretch
    sell_stop = nr["c"] - stretch
    # no overlapping: if a stretch already triggered in last 3 completed bars, skip
    if row.get("nr7_open"):
        return None
    kind = "NR7" if is_nr7 else "inside+NR4"
    if trig["h"] >= buy_stop:
        return _hit("nr7_stretch", "LONG",
                    "%s buy-stop %.4f (stretch %.4f), exit 3 bars" % (kind, buy_stop, stretch),
                    stop=buy_stop, stretch=stretch)
    if trig["l"] <= sell_stop:
        return _hit("nr7_stretch", "SHORT",
                    "%s sell-stop %.4f (stretch %.4f), exit 3 bars" % (kind, sell_stop, stretch),
                    stop=sell_stop, stretch=stretch)
    return None


_RULES = (
    _cash_carry,
    _crypto_regime,
    _pairs_residual,
    _wavetrend_cross,
    _turtle_soup,
    _ichimoku_tk,
    _vwap_revert,
    _basis_z_fade,
    _triple_supertrend,
    _fvg_retest,
    _funding_settle_fade,
    _weekend_momentum,
    _btc_lead_lag,
    _poc_sweep_reclaim,
    _nr7_stretch,
)


def classify(row, ctx=None):
    """Return a list of SHADOW hits. Never sets push/tg/c34.

    ``row`` is the collector symbol payload. Extra feeds may sit on ``row``
    or ``ctx`` (merged). Missing feed → that key is skipped, not faked.
    """
    if ctx:
        merged = dict(ctx)
        merged.update(row or {})
        row = merged
    row = row or {}
    if is_stock_token(row):
        # Lazy import: stock_arena_hook → classify_stock → this module.
        try:
            from stock_arena_hook import record_stock_row
            record_stock_row(row)
        except Exception as exc:
            logger.error("stock_arena record: %s", exc)
        return []
    bars = _bars_of(row)
    hits = []
    for fn in _RULES:
        try:
            out = fn(row, bars)
        except Exception as exc:
            logger.error("rookie rule %s failed: %s", fn.__name__, exc)
            continue
        if not out:
            continue
        if isinstance(out, dict):
            hits.append(out)
        else:
            hits.extend(out)
    return hits


def classify_rookies(row, ctx=None):
    """Alias used by the live-collector merge hook."""
    return classify(row, ctx)


def extend_hits(existing, row, ctx=None):
    """Append rookie hits onto a live classify() result (list / dict / None)."""
    if existing is None:
        hits = []
    elif isinstance(existing, dict):
        hits = [existing]
    else:
        hits = list(existing)
    hits.extend(classify(row, ctx))
    return hits
