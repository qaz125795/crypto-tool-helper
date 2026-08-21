"""S3 補選 ROSTER — SHADOW paper only.

This repo does not contain the live veteran arena_report.py (that file
runs on the Vultr collector). This module holds the 15 mid-S3 rookies
and a merge helper so the live ROSTER can absorb them without rewriting
veteran history or the global kickoff (1786982400).

ROSTER format: {key: (name, code, cat)}
"""
from __future__ import annotations

# Global S3 kickoff (veterans). Do not change.
S3_KICKOFF_TS = 1786982400  # 2026-08-18 00:00 Taipei

# Per-key intake for rookies who join mid-season. Not a second global kickoff.
S3_ROOKIE_INTAKE_TS = 1787241600  # 2026-08-21 00:00 Taipei
S3_ROOKIE_INTAKE_LABEL = "2026-08-21 Taipei"
S3_ROOKIE_CAT = "補選"
S3_ROOKIE_MAX_OPEN = 5
S3_ROOKIE_CAPITAL = 10000.0  # display only; do not change global capital
S3_ROOKIE_RISK = 100.0
S3_ROOKIE_LEVERAGE = 5.0

# Codes already on the live S3 board or reserved. Rookie codes must not collide.
FORBIDDEN_CODES = {
    "SMCP", "SURF", "BTCR", "SQZ", "WHAL", "TKL+", "TKUP", "SMRT", "TRND",
    "LSQ", "WHL", "RPL", "BRK+", "TRTL", "BRKq", "BRK", "MCR", "ASIA", "OI15",
    "RBND", "TRTLq", "RCKT", "SESN", "CAPT", "BOMB", "COIL", "CSMF", "EXHT",
    "FROI", "PFL", "SQCH", "PZON", "FNDS", "UNWD", "CVFS", "DIVR", "CVDD",
    "RCKTq", "FNDL", "CTRN", "CSCD", "FRD", "OIPD", "DMB", "LSS", "DSTR",
    "FRZ", "LSL", "FADE", "LLD", "TKS", "RPS", "WHS", "WHS+",
}

# Live registered placeholder (no classify hits). Same code, we implement the
# rule Jack specified (週末動量) rather than inventing a second code.
WKND_PLACEHOLDER_NOTE = (
    "live registered already has WKND='週末壓力 Weekend' / 臨時選手; "
    "this ROSTER replaces the display name with 週末動量 and cat=補選"
)

ROSTER = {
    "cash_carry": ("基差搬運 Carry", "CARY", S3_ROOKIE_CAT),
    "crypto_regime_score": ("體制分 Regime", "REGM", S3_ROOKIE_CAT),
    "pairs_residual_z": ("配對殘差 PairZ", "PRSD", S3_ROOKIE_CAT),
    "wavetrend_cross": ("波浪趨勢 WaveT", "WTRD", S3_ROOKIE_CAT),
    "turtle_soup": ("海龜湯 Soup", "TSUP", S3_ROOKIE_CAT),
    "ichimoku_tk_cross": ("一目雲 Kumo", "KUMO", S3_ROOKIE_CAT),
    "vwap_revert": ("VWAP回歸 VWAP", "VWAP", S3_ROOKIE_CAT),
    "basis_z_fade": ("基差Z BasisZ", "BSIZ", S3_ROOKIE_CAT),
    "triple_supertrend": ("三重Supertrend ST3", "STRP", S3_ROOKIE_CAT),
    "fvg_retest": ("缺口回補 FVG", "FVGR", S3_ROOKIE_CAT),
    "funding_settle_fade": ("結算窗淡出 FundW", "FSWD", S3_ROOKIE_CAT),
    "weekend_momentum": ("週末動量 Weekend", "WKND", S3_ROOKIE_CAT),
    "btc_lead_lag": ("BTC領漲滯後 BtcLag", "BTCL", S3_ROOKIE_CAT),
    "poc_sweep_reclaim": ("POC回收 PocR", "POCR", S3_ROOKIE_CAT),
    "nr7_stretch": ("NR7伸展 NR7", "NR7I", S3_ROOKIE_CAT),
}

S3_ROOKIE_KEYS = tuple(ROSTER.keys())

# Optional per-key intake / cap. Live build() may ignore unknown fields.
ROSTER_META = {
    key: {
        "intake_ts": S3_ROOKIE_INTAKE_TS,
        "intake_label": S3_ROOKIE_INTAKE_LABEL,
        "max_open": S3_ROOKIE_MAX_OPEN,
        "shadow": True,
        "push": False,
        "tg": False,
        "c34": False,
        "cat": S3_ROOKIE_CAT,
    }
    for key in S3_ROOKIE_KEYS
}

MAX_OPEN = {key: S3_ROOKIE_MAX_OPEN for key in S3_ROOKIE_KEYS}


def apply_rookie_roster(base: dict | None) -> dict:
    """Merge rookies into an existing live ROSTER. Veterans keep their tuples."""
    out = dict(base or {})
    for key, tup in ROSTER.items():
        out[key] = tup
    return out


def max_open_for(key: str, veteran_default=None):
    """Cap rookies at 5 concurrent. Do not lower veterans' caps."""
    if key in MAX_OPEN:
        return MAX_OPEN[key]
    return veteran_default


def intake_ts_for(key: str, veteran_default=None):
    if key in ROSTER_META:
        return ROSTER_META[key]["intake_ts"]
    return veteran_default


def roster_codes() -> dict[str, str]:
    return {key: tup[1] for key, tup in ROSTER.items()}


def _assert_codes_unique() -> None:
    codes = [tup[1] for tup in ROSTER.values()]
    if len(codes) != len(set(codes)):
        raise RuntimeError("rookie codes collide with each other")
    hit = set(codes) & FORBIDDEN_CODES
    if hit:
        raise RuntimeError("rookie codes collide with live board: %s" % sorted(hit))
    cats = {tup[2] for tup in ROSTER.values()}
    if cats != {S3_ROOKIE_CAT}:
        raise RuntimeError("rookie cat must be 補選 only, got %s" % cats)


_assert_codes_unique()
