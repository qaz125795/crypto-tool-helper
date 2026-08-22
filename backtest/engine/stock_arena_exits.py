"""EXIT_PROFILE for 股票代幣場外賽. Separate from S3 / veteran maps."""
from __future__ import annotations

_default = {"sl_atr": 1.5, "tp_r": 2.0, "horizon_h": 48}

EXIT_PROFILE = {
    "ny_orb": {
        "sl_atr": 1.0, "tp_r": 1.5, "horizon_h": 8,
        "eod_flat": True, "session": "ny",
        "note": "EOD 16:45 ET flatten; ORB is a same-session trade",
    },
    "hk_orb": {
        "sl_atr": 1.0, "tp_r": 1.5, "horizon_h": 8,
        "eod_flat": True, "session": "hk",
        "note": "HKEX close flatten; morning ORB only",
    },
    "kr_orb": {
        "sl_atr": 1.0, "tp_r": 1.5, "horizon_h": 8,
        "eod_flat": True, "session": "kr",
        "note": "KRX close flatten",
    },
    "gap_fill": {
        "sl_atr": 0.8, "tp_r": 0.5, "horizon_h": 4,
        "tp_frac_of_gap": 0.5,
        "note": "TP half the gap; if unfilled 30m, thesis is invalidated (stop, not breakout chase)",
    },
    "overnight_drift": {
        "sl_atr": 1.2, "tp_r": 1.5, "horizon_h": 16,
        "flatten_on_rth": True,
        "note": "no new entries in RTH; flatten when cash opens",
    },
    "weekend_converge": {
        "sl_atr": 1.2, "tp_r": 1.5, "horizon_h": 48,
        "flatten_on_monday_rth": True,
        "note": "fade weekend perp drift; opposite of S3 WKND momentum hold",
    },
    "session_vwap": {
        "sl_atr": 0.8, "tp_r": 0.5, "horizon_h": 6,
        "tp_frac_of_dev": 0.5, "sl_ext_of_dev": 0.8,
        "note": "RTH session VWAP only; 1.2% deviation; not 24h VWAP",
    },
    "overnight_fund_fade": {
        "sl_atr": 0.8, "tp_r": 1.0, "horizon_h": 12,
        "min_abs_funding": 0.0003,
        "note": "skip parked 0.01% funding; fade only outside RTH",
    },
    "eq_wavetrend": {
        "sl_atr": 1.5, "tp_r": 2.0, "horizon_h": 36,
        "note": "control: same WT as S3 WTRD, stock universe only",
    },
    "eq_turtle_soup": {
        "sl_atr": 1.0, "tp_r": 1.5, "horizon_h": 12,
        "note": "control: soup on stock tokens",
    },
    "eq_nr7": {
        "sl_atr": 1.0, "tp_r": 1.5, "horizon_h": 4, "exit_bars": 3,
        "note": "control: Crabel NR7 originally from equities",
    },
    "eq_triple_st": {
        "sl_atr": 1.8, "tp_r": 2.5, "horizon_h": 48,
        "note": "control: trend following; expected to suffer RTH/on-off gaps",
    },
}


def exit_for(key: str) -> dict:
    return dict(EXIT_PROFILE.get(key) or _default)


def apply_stock_exits(base: dict | None) -> dict:
    """Merge into a dedicated stock map. Do not call on the S3 EXIT_PROFILE."""
    out = dict(base or {})
    for key, prof in EXIT_PROFILE.items():
        if key not in out:
            out[key] = dict(prof)
    return out
