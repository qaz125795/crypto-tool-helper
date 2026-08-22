"""股票代幣場外賽 — SHADOW, separate from S3.

Do not merge this ROSTER into the live crypto arena_report.ROSTER.
S3 veterans / 補選 stay on data/arena.json; this league is data/stock_arena.json.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from arena_report import FORBIDDEN_CODES, S3_ROOKIE_KEYS, roster_codes as s3_codes

# Own kickoff (not S3 1786982400). Mid-S3 calendar date, new league.
STOCK_KICKOFF_TS = 1787241600  # 2026-08-21 00:00 Taipei
STOCK_INTAKE_LABEL = "2026-08-21 Taipei"
STOCK_CAT = "場外賽"
STOCK_CTRL_CAT = "場外對照"
STOCK_MAX_OPEN = 5
STOCK_CAPITAL = 10000.0
STOCK_RISK = 100.0
STOCK_LEVERAGE = 5.0
STOCK_GOAL = 20000.0

# Liquid Gate USDT-perps only. Thin names (CATL/SMIC/BYD/2L) and indexes stay out.
UNIVERSE_US = frozenset({
    "TSLAX", "NVDAX", "AAPLX", "GOOGLX", "AMZNX", "METAX", "MSFT",
    "AMD", "COINX", "HOODX", "MSTRX", "PLTRX", "NFLX", "SMCI",
    "QQQX", "SPYX", "TQQQX", "SOXL", "SQQQ",
})
UNIVERSE_HK = frozenset({
    "TENCENT", "XIAOMI", "XIAOMIHKD", "BABA",
})
UNIVERSE_KR = frozenset({
    "SKHYNIX", "SAMSUNG", "SKHY",
})
UNIVERSE = UNIVERSE_US | UNIVERSE_HK | UNIVERSE_KR

# Bases that leak into crypto scanners without the Gate "X" suffix (S3 already
# opened MSTRUSDT as 板塊熱). Skip S3 classify; do not trade unless in UNIVERSE.
STOCK_ALIASES = frozenset({
    "TSLA", "NVDA", "AAPL", "GOOGL", "AMZN", "META", "MSFT", "COIN",
    "HOOD", "MSTR", "PLTR", "NFLX", "QQQ", "SPY", "TQQQ",
})

CRYPTO_BLOCK = frozenset({
    "BTC", "ETH", "SOL", "DOGE", "XRP", "ADA", "SUI", "PEPE", "WIF",
    "BTCUSDT", "ETHUSDT",
})

REGION = {}
REGION.update({b: "ny" for b in UNIVERSE_US})
REGION.update({b: "hk" for b in UNIVERSE_HK})
REGION.update({b: "kr" for b in UNIVERSE_KR})

SESSIONS = {
    "ny": {
        "tz": "America/New_York",
        "open": (9, 30),
        "close": (16, 0),
        "orb_min": 15,
        "eod_flat": (16, 45),
        "lunch": None,
    },
    "hk": {
        "tz": "Asia/Hong_Kong",
        "open": (9, 30),
        "close": (16, 0),
        "orb_min": 15,
        "eod_flat": (16, 0),
        "lunch": ((12, 0), (13, 0)),
    },
    "kr": {
        "tz": "Asia/Seoul",
        "open": (9, 0),
        "close": (15, 30),
        "orb_min": 15,
        "eod_flat": (15, 30),
        "lunch": None,
    },
}

# 8 equity-native + 4 crypto price-action controls. Codes must not hit S3.
ROSTER = {
    "ny_orb": ("美股開盤區間 NYORB", "NYOR", STOCK_CAT),
    "hk_orb": ("港股開盤區間 HKORB", "HKOR", STOCK_CAT),
    "kr_orb": ("韓股開盤區間 KRORB", "KROR", STOCK_CAT),
    "gap_fill": ("跳空回補 GapFill", "GAPF", STOCK_CAT),
    "overnight_drift": ("隔夜漂移 OvDrift", "ONDR", STOCK_CAT),
    "weekend_converge": ("週末收斂 WkConv", "WKCV", STOCK_CAT),
    "session_vwap": ("盤中VWAP SessVW", "SVWP", STOCK_CAT),
    "overnight_fund_fade": ("隔夜資費淡出 NightFR", "NDRF", STOCK_CAT),
    "eq_wavetrend": ("波浪對照 WT-eq", "EWTR", STOCK_CTRL_CAT),
    "eq_turtle_soup": ("海龜湯對照 Soup-eq", "ETSU", STOCK_CTRL_CAT),
    "eq_nr7": ("NR7對照 NR7-eq", "ENR7", STOCK_CTRL_CAT),
    "eq_triple_st": ("三重ST對照 ST3-eq", "ESTR", STOCK_CTRL_CAT),
}

STOCK_KEYS = tuple(ROSTER.keys())
STOCK_MAIN_KEYS = (
    "ny_orb", "hk_orb", "kr_orb", "gap_fill", "overnight_drift",
    "weekend_converge", "session_vwap", "overnight_fund_fade",
)
STOCK_CTRL_KEYS = ("eq_wavetrend", "eq_turtle_soup", "eq_nr7", "eq_triple_st")

ROSTER_META = {
    key: {
        "intake_ts": STOCK_KICKOFF_TS,
        "intake_label": STOCK_INTAKE_LABEL,
        "max_open": STOCK_MAX_OPEN,
        "shadow": True,
        "push": False,
        "tg": False,
        "c34": False,
        "league": "stock_side",
        "cat": ROSTER[key][2],
    }
    for key in STOCK_KEYS
}

MAX_OPEN = {key: STOCK_MAX_OPEN for key in STOCK_KEYS}

STOCK_NO_PUSH = set(STOCK_KEYS)


def _base(sym: str) -> str:
    s = (sym or "").upper().replace("_", "").replace("-", "")
    if s.endswith("USDT"):
        s = s[:-4]
    return s


def region_of(sym: str) -> str | None:
    return REGION.get(_base(sym))


def is_stock_token(sym_or_row) -> bool:
    """True for the 場外賽 universe (and STOCK-suffix tokenized names)."""
    if isinstance(sym_or_row, dict):
        sym = sym_or_row.get("sym") or sym_or_row.get("symbol") or ""
    else:
        sym = sym_or_row or ""
    base = _base(sym)
    if base in CRYPTO_BLOCK:
        return False
    if base in UNIVERSE or base in STOCK_ALIASES:
        return True
    if base.endswith("STOCK") or base.endswith("TOKEN"):
        return True
    return False


def in_universe(sym: str) -> bool:
    return _base(sym) in UNIVERSE


def session_now(region: str, ts: int):
    cfg = SESSIONS[region]
    return datetime.fromtimestamp(int(ts), tz=ZoneInfo(cfg["tz"]))


def _hm(dt: datetime) -> tuple[int, int]:
    return dt.hour, dt.minute


def _minutes(hm: tuple[int, int]) -> int:
    return hm[0] * 60 + hm[1]


def in_rth(region: str, ts: int) -> bool:
    cfg = SESSIONS[region]
    dt = session_now(region, ts)
    if dt.weekday() >= 5:
        return False
    now_m = _minutes(_hm(dt))
    opn = _minutes(cfg["open"])
    cls = _minutes(cfg["close"])
    if not (opn <= now_m < cls):
        return False
    lunch = cfg.get("lunch")
    if lunch:
        a, b = _minutes(lunch[0]), _minutes(lunch[1])
        if a <= now_m < b:
            return False
    return True


def in_orb_window(region: str, ts: int) -> bool:
    """First orb_min minutes after cash open (range is still forming)."""
    cfg = SESSIONS[region]
    dt = session_now(region, ts)
    if dt.weekday() >= 5:
        return False
    now_m = _minutes(_hm(dt))
    opn = _minutes(cfg["open"])
    return opn <= now_m < opn + cfg["orb_min"]


def in_orb_trade(region: str, ts: int) -> bool:
    """After OR complete, still same-session RTH."""
    return in_rth(region, ts) and not in_orb_window(region, ts)


def max_open_for(key: str, veteran_default=None):
    if key in MAX_OPEN:
        return MAX_OPEN[key]
    return veteran_default


def seed_board(as_of: int | None = None) -> dict:
    """Registered-only board. Players stay empty until classify fills exist."""
    registered = [
        {"name": name, "code": code, "cat": cat, "key": key}
        for key, (name, code, cat) in ROSTER.items()
    ]
    ts = int(as_of or STOCK_KICKOFF_TS)
    return {
        "as_of": ts,
        "league": "stock_side",
        "goal": STOCK_GOAL,
        "capital": STOCK_CAPITAL,
        "risk_per_trade": STOCK_RISK,
        "leverage": STOCK_LEVERAGE,
        "calibrated": False,
        "kickoff": STOCK_KICKOFF_TS,
        "season": {
            "id": "stock_side",
            "label": "股票代幣場外賽",
            "day": 1,
            "total": 14,
            "season_no": 0,
            "desc": "美／港／韓股票永續 SHADOW；不與 S3 幣圈榜混打",
        },
        "tournament": {
            "leverage": STOCK_LEVERAGE,
            "capital": STOCK_CAPITAL,
            "goal": STOCK_GOAL,
            "risk_per_trade": STOCK_RISK,
            "season_days": 14,
            "radar_baseline_avg_r": None,
            "phases": [
                {"id": "stock_side", "days": "1–14", "label": "場外賽",
                 "desc": "8 檔股票原生 + 4 檔幣圈價格對照，獨立宇宙"},
            ],
            "tiers": [
                {"id": "watch", "emoji": "🔭", "label": "觀察", "req": "有成交或持倉"},
                {"id": "warmup", "emoji": "🚧", "label": "暖身", "req": "尚未有成交"},
            ],
            "dream_goal": "獨立驗證股票代幣時鐘，不併入 S3 晉級",
        },
        "tier_counts": {"warmup": len(ROSTER)},
        "zone_counts": {"warmup": len(ROSTER)},
        "players": [],
        "registered": registered,
    }


def _assert_codes() -> None:
    codes = [t[1] for t in ROSTER.values()]
    if len(codes) != len(set(codes)):
        raise RuntimeError("stock side codes collide with each other")
    hit = set(codes) & FORBIDDEN_CODES
    if hit:
        raise RuntimeError("stock codes collide with S3 live: %s" % sorted(hit))
    hit2 = set(codes) & set(s3_codes().values())
    if hit2:
        raise RuntimeError("stock codes collide with S3 補選: %s" % sorted(hit2))
    if set(ROSTER) & set(S3_ROOKIE_KEYS):
        raise RuntimeError("stock keys collide with S3 補選 keys")


_assert_codes()
