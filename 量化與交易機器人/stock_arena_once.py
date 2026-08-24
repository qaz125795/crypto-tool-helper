#!/usr/bin/env python3
"""Rebuild data/stock_arena.json (registered board). Independent of S3 arena.json.

Does not push Telegram / C34. Paper fills stay empty until the collector
writes stock_hits.jsonl and a later pass grows players.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from stock_arena_report import seed_board


DEFAULT_OUTS = (
    "/data/partner-apps/p-6e6dee8f/repo/data/stock_arena.json",
    "/data/partner-apps/p-6e6dee8f/data/stock_arena.json",
    "/data/partner-apps/p-6e6dee8f/releases/current/data/stock_arena.json",
    "/app/data/crit_collector/stock_arena.json",
    "/root/unified-platform/services/jackbot/data/crit_collector/stock_arena.json",
)


def write_seed(path: str, as_of: int | None = None) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    board = seed_board(as_of)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(board, f, ensure_ascii=False)
    return path


def main(argv=None):
    p = argparse.ArgumentParser(description="Write stock side-league seed board")
    p.add_argument("--out", action="append", default=[], help="Output JSON path (repeatable)")
    args = p.parse_args(argv)
    written = []
    targets = list(args.out) if args.out else [o for o in DEFAULT_OUTS if os.path.isdir(os.path.dirname(o) or ".")]
    if not targets:
        here = os.path.join(_HERE, "..", "arena_web", "data", "stock_arena.json")
        targets = [os.path.abspath(here)]
    for path in targets:
        written.append(write_seed(path))
    print("STOCK_ARENA", written)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
