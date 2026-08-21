"""場外賽 import surface. Do NOT merge into S3 ROSTER / classify hits."""
from __future__ import annotations

import json
import logging
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.join(os.path.dirname(_HERE), "backtest", "engine")
for _p in (_HERE, _ENGINE):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

from stock_arena_report import (
    ROSTER as STOCK_ROSTER,
    STOCK_KEYS,
    STOCK_KICKOFF_TS,
    UNIVERSE,
    in_universe,
    is_stock_token,
    max_open_for as stock_max_open_for,
    seed_board,
)
from stock_arena_classify import classify_stock
from stock_arena_exits import EXIT_PROFILE as STOCK_EXIT_PROFILE
from stock_arena_exits import apply_stock_exits, exit_for as stock_exit_for

logger = logging.getLogger("stock_arena_hook")

LIVE_COLLECTOR_DIR = "/app/data/crit_collector"


def _hits_path():
    d = os.environ.get("STOCK_ARENA_DIR")
    if d:
        return os.path.join(d, "stock_hits.jsonl")
    if os.path.isdir(LIVE_COLLECTOR_DIR):
        return os.path.join(LIVE_COLLECTOR_DIR, "stock_hits.jsonl")
    return None


def record_stock_row(row):
    """Classify a collector row for the 場外賽 jsonl. Never extends S3 hits.

    No-op unless STOCK_ARENA_DIR is set or the live collector directory exists,
    so unit tests do not write files.
    """
    dest = _hits_path()
    if dest is None:
        return []
    row = row or {}
    if not is_stock_token(row):
        return []
    sym = row.get("sym") or row.get("symbol") or ""
    if not in_universe(sym):
        return []
    try:
        hits = classify_stock(row)
    except Exception as exc:
        logger.error("classify_stock: %s", exc)
        return []
    hits = [h for h in hits if h.get("key") in STOCK_KEYS]
    if not hits:
        return []
    try:
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        rec = {"ts": int(time.time()), "sym": sym, "hits": hits}
        with open(dest, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.error("stock_hits jsonl: %s", exc)
    return hits
