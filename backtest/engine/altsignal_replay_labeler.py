"""EXIT_PROFILE for S3 補選. Missing key → _default.

Do not copy-paste the default when the rule has its own stop.
This file is additive: apply_rookie_exits() merges into a live map
without changing veteran keys that already exist.
"""
from __future__ import annotations

_default = {"sl_atr": 1.5, "tp_r": 2.0, "horizon_h": 48}

EXIT_PROFILE = {
    "cash_carry": {
        "sl_atr": 1.0,
        "tp_r": 1.5,
        "horizon_h": 24,
        "note": "basis carry; flatten when annualized premium crosses back through 0",
    },
    "crypto_regime_score": {
        "sl_atr": 2.0,
        "tp_r": 2.5,
        "horizon_h": 72,
        "note": "regime trend; flatten on first break <=39",
    },
    "pairs_residual_z": {
        "sl_atr": 1.2,
        "tp_r": 1.5,
        "horizon_h": 24,
        "z_stop": 3.0,
        "note": "stop |z|>3 (not ATR-default); fade residual, not momentum",
    },
    "wavetrend_cross": {
        "sl_atr": 1.5,
        "tp_r": 2.0,
        "horizon_h": 36,
        "note": "WT extreme cross; reverse on opposite WT2 extreme",
    },
    "turtle_soup": {
        "sl_atr": 1.0,
        "tp_r": 1.5,
        "horizon_h": 12,
        "note": "SL beyond soup wick; cancel if still outside on bar N+2",
    },
    "ichimoku_tk_cross": {
        "sl_atr": 2.0,
        "tp_r": 2.5,
        "horizon_h": 72,
        "note": "cloud trend; exit on TK reverse or close back through cloud",
    },
    "vwap_revert": {
        "sl_atr": 0.8,
        "tp_r": 0.5,
        "horizon_h": 8,
        "tp_frac_of_dev": 0.5,
        "sl_ext_of_dev": 0.8,
        "note": "TP = half the |px/VWAP-1| deviation; SL = 0.8× further",
    },
    "basis_z_fade": {
        "sl_atr": 1.2,
        "tp_r": 1.5,
        "horizon_h": 24,
        "z_flatten": 0.5,
        "z_cut": 4.0,
        "note": "flatten |z|<0.5; risk-cut |z|>4 (z of perp-index, not annualized)",
    },
    "triple_supertrend": {
        "sl_atr": 1.8,
        "tp_r": 2.5,
        "horizon_h": 48,
        "note": "exit when any of the three ST flips against the position",
    },
    "fvg_retest": {
        "sl_atr": 1.5,
        "tp_r": 2.0,
        "horizon_h": 24,
        "note": "SL 1.5 ATR beyond the unfilled gap extreme",
    },
    "funding_settle_fade": {
        "sl_atr": 0.8,
        "tp_r": 1.0,
        "horizon_h": 1,
        "hold_min": 15,
        "hold_max": 30,
        "note": "window trade: enter T-15m..T-3m, exit T+15..T+30m",
    },
    "weekend_momentum": {
        "sl_atr": 2.0,
        "tp_r": 2.0,
        "horizon_h": 48,
        "note": "hold Sat 00:00 UTC → Sun 23:59 UTC; flat weekdays",
    },
    "btc_lead_lag": {
        "sl_atr": 1.0,
        "tp_r": 1.2,
        "horizon_h": 1,
        "hold_min": 15,
        "hold_max": 30,
        "note": "15–30m hold after BTC impulse; skip ETH/BTC majors",
    },
    "poc_sweep_reclaim": {
        "sl_atr": 1.2,
        "tp_r": 1.5,
        "horizon_h": 12,
        "note": "SL beyond the sweep wick extreme, not a generic 1.5 ATR default",
    },
    "nr7_stretch": {
        "sl_atr": 1.0,
        "tp_r": 1.5,
        "horizon_h": 4,
        "exit_bars": 3,
        "note": "exit after 3 bars; no overlapping NR7 positions",
    },
}


def exit_for(key: str) -> dict:
    return dict(EXIT_PROFILE.get(key) or _default)


def apply_rookie_exits(base: dict | None) -> dict:
    """Add rookie profiles. Existing veteran keys are left untouched."""
    out = dict(base or {})
    for key, prof in EXIT_PROFILE.items():
        if key not in out:
            out[key] = dict(prof)
    return out
