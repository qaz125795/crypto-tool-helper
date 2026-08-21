"""Promotion / push flags. Rookies stay SHADOW — no TG, no C34, no live.

HAS_PUSH / ALREADY_TG must remain only lowcap_momo_long.
Do not add any S3 補選 key here.
"""
from __future__ import annotations

HAS_PUSH = {"lowcap_momo_long"}
ALREADY_TG = {"lowcap_momo_long"}

# Explicit denylist so a future merge cannot quietly enable rookies.
S3_ROOKIE_NO_PUSH = {
    "cash_carry",
    "crypto_regime_score",
    "pairs_residual_z",
    "wavetrend_cross",
    "turtle_soup",
    "ichimoku_tk_cross",
    "vwap_revert",
    "basis_z_fade",
    "triple_supertrend",
    "fvg_retest",
    "funding_settle_fade",
    "weekend_momentum",
    "btc_lead_lag",
    "poc_sweep_reclaim",
    "nr7_stretch",
}

try:
    from stock_arena_report import STOCK_NO_PUSH
except ImportError:
    STOCK_NO_PUSH = set()

NO_PUSH = S3_ROOKIE_NO_PUSH | set(STOCK_NO_PUSH)


def apply_promote_guard(has_push=None, already_tg=None):
    """Keep live push sets, but strip any rookie / 場外賽 key that leaked in."""
    hp = set(has_push if has_push is not None else HAS_PUSH)
    tg = set(already_tg if already_tg is not None else ALREADY_TG)
    hp -= NO_PUSH
    tg -= NO_PUSH
    if hp != {"lowcap_momo_long"} and has_push is None:
        hp = {"lowcap_momo_long"}
    return hp, tg
